"""Command line interface.

  python -m jobfinder serve                      # web dashboard on http://127.0.0.1:8000
  python -m jobfinder search --profile me.txt    # run a search from the terminal
  python -m jobfinder discover                   # detect career-site ATS for companies lacking one
  python -m jobfinder import companies.csv       # add companies (name,website[,ats,ats_id])
  python -m jobfinder export results.csv         # export latest ranked results
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from .config import settings
from .db import DB
from .engine import ALL_SOURCES, SearchRequest, import_companies_csv, ranked_results, run_search, seed_companies


def get_db() -> DB:
    db = DB(settings.db_path)
    if not db.list_companies():
        seed_companies(db)
    return db


def write_csv(rows: list[dict], path: str):
    cols = ["score", "title", "company", "location", "it_category", "level", "posted_at", "salary", "source",
            "matched_skills", "missing_skills", "url"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([", ".join(r[c]) if isinstance(r.get(c), list) else r.get(c, "") for c in cols])


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobfinder", description="Deep search for IT jobs in Illinois")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="start the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    s = sub.add_parser("search", help="run a search")
    s.add_argument("--profile", help="text file with your job description / resume")
    s.add_argument("--keywords", default="", help="comma-separated must-have keywords, e.g. 'python,aws'")
    s.add_argument("--exclude", default="", help="comma-separated words to exclude, e.g. 'clearance,unpaid'")
    s.add_argument("--no-remote", action="store_true", help="only physical Illinois locations")
    s.add_argument("--sources", default=",".join(ALL_SOURCES))
    s.add_argument("--max-age", type=int, default=0, help="only jobs posted in the last N days")
    s.add_argument("--companies", default="", help="comma-separated company names to restrict to")
    s.add_argument("--max-employees", type=int, default=300, help="only employers with <= N employees (0 = any size)")
    s.add_argument("--include-unknown-size", action="store_true", help="also search employers with unknown headcount")
    s.add_argument("--top", type=int, default=25)
    s.add_argument("--csv", help="also write ranked results to this CSV file")

    sub.add_parser("discover", help="find career-site ATS for companies without one")
    s = sub.add_parser("import", help="import companies from CSV (name,website[,ats,ats_id,hq])")
    s.add_argument("file")
    s = sub.add_parser("export", help="export the latest run's ranked results to CSV")
    s.add_argument("file")
    sub.add_parser("companies", help="list tracked companies")
    sub.add_parser("enrich-sizes", help="look up missing employee counts (needs PDL_API_KEY)")
    s = sub.add_parser("find-companies", help="add Illinois employers with <= N employees (needs PDL_API_KEY)")
    s.add_argument("--max-employees", type=int, default=300)
    s.add_argument("--min-employees", type=int, default=5)
    s.add_argument("--count", type=int, default=100, help="how many companies to add (1 PDL credit each)")
    s = sub.add_parser("set-size", help="set a company's employee count manually")
    s.add_argument("name")
    s.add_argument("employees", type=int)
    s = sub.add_parser("save-companies", help="write companies (with discovered ATS) back to a YAML file")
    s.add_argument("file", nargs="?", default=str(settings.companies_file))

    a = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if a.cmd == "serve":
        import uvicorn
        print(f"Dashboard: http://{a.host}:{a.port}")
        uvicorn.run("jobfinder.web:app", host=a.host, port=a.port, log_level="warning")
        return

    db = get_db()
    if a.cmd == "search":
        profile = Path(a.profile).read_text(encoding="utf-8") if a.profile else ""
        req = SearchRequest(
            profile=profile, keywords=[k.strip() for k in a.keywords.split(",") if k.strip()],
            exclude=[k.strip() for k in a.exclude.split(",") if k.strip()], include_remote=not a.no_remote,
            sources=[x.strip() for x in a.sources.split(",")], max_age_days=a.max_age,
            company_filter=[x.strip() for x in a.companies.split(",") if x.strip()],
            max_employees=a.max_employees, include_unknown_size=a.include_unknown_size,
        )
        from .engine import RUNS

        async def go():
            task = asyncio.create_task(run_search(db, req))
            printed = 0
            while not task.done():
                await asyncio.sleep(0.5)
                for st in RUNS.values():
                    for line in st.log[printed:]:
                        print(line)
                    printed = len(st.log)
            run_id = await task
            for line in RUNS[run_id].log[printed:]:
                print(line)
            return run_id

        run_id = asyncio.run(go())
        rows = ranked_results(db, run_id)
        print(f"\nTop {min(a.top, len(rows))} of {len(rows)} matches:\n")
        for r in rows[: a.top]:
            print(f"{r['score']:5.1f}  {r['title'][:60]:60}  {r['company'][:28]:28}  {r['location'][:35]}")
            print(f"       {r['url']}")
        if a.csv:
            write_csv(rows, a.csv)
            print(f"\nSaved {len(rows)} rows to {a.csv}")
    elif a.cmd == "discover":
        from .engine import RUNS
        req = SearchRequest(sources=["companies"], max_employees=0,
                            company_filter=[c["name"] for c in db.list_companies() if not c["ats"]])
        if not req.company_filter:
            print("All companies already have a detected career-site type.")
            return
        run_id = asyncio.run(run_search(db, req))
        print("\n".join(RUNS[run_id].log))
    elif a.cmd == "import":
        n = import_companies_csv(db, Path(a.file).read_text(encoding="utf-8-sig"))
        print(f"Imported {n} companies. Run `python -m jobfinder discover` to detect their career sites.")
    elif a.cmd == "export":
        runs = db.list_runs(1)
        rows = ranked_results(db, runs[0]["id"] if runs else None)
        write_csv(rows, a.file)
        print(f"Saved {len(rows)} rows to {a.file}")
    elif a.cmd == "enrich-sizes":
        from .sizes import enrich_sizes
        try:
            asyncio.run(enrich_sizes(db))
        except RuntimeError as e:
            print(e)
    elif a.cmd == "find-companies":
        from .sizes import find_companies
        try:
            asyncio.run(find_companies(db, a.max_employees, a.count, a.min_employees))
        except RuntimeError as e:
            print(e)
    elif a.cmd == "set-size":
        db.set_company_size(a.name, a.employees, "manual")
        print(f"{a.name}: {a.employees} employees")
    elif a.cmd == "save-companies":
        import yaml
        rows = [{k: c[k] for k in ("name", "website", "ats", "ats_id", "hq", "employees", "size_source")
                 if c.get(k) not in (None, "")} for c in db.list_companies()]
        header = ("# Illinois employers tracked by the job finder (generated by `python -m jobfinder save-companies`).\n"
                  "#   ats: greenhouse | lever | ashby | smartrecruiters | workday | icims | jsonld | blank = auto-discover\n"
                  "#   unsupported:<name> = career site runs on a system without a public feed (check manually)\n")
        Path(a.file).write_text(header + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True, width=200),
                                encoding="utf-8")
        print(f"Wrote {len(rows)} companies to {a.file}")
    elif a.cmd == "companies":
        for c in db.list_companies():
            print(f"{c['name'][:40]:40} {str(c['employees'] or '?'):>6} {c['ats'] or '-':16} {c['ats_id'][:50]:50} jobs={c['last_count']} {c['last_error'] or ''}")


if __name__ == "__main__":
    sys.exit(main())
