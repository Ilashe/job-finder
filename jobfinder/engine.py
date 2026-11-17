"""Search orchestration: crawl every company + database concurrently, filter to Illinois IT jobs, store, rank."""
from __future__ import annotations

import asyncio
import csv
import io
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import yaml

from .config import settings
from .db import DB
from .filters import classify_it, detect_level, extract_skills, is_illinois, is_remote_us
from .http import Fetcher
from .models import Company, Job
from .ranking import Ranker
from .sources import AGGREGATORS, CONNECTORS, discover
from .sources.base import Ctx

ALL_SOURCES = ["companies", "usajobs", "careeronestop", "adzuna"]


@dataclass
class SearchRequest:
    profile: str = ""                     # the user's job description / resume text
    keywords: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    include_remote: bool = True
    sources: list[str] = field(default_factory=lambda: list(ALL_SOURCES))
    max_age_days: int = 0                 # 0 = any age
    company_filter: list[str] = field(default_factory=list)  # restrict to these company names
    max_employees: int = 300              # only employers with <= this many employees (0 = no limit)
    include_unknown_size: bool = False    # also keep employers whose headcount is unknown


class RunState:
    def __init__(self, run_id: int):
        self.run_id = run_id
        self.log: list[str] = []
        self.stats = {"companies_total": 0, "companies_done": 0, "fetched": 0, "kept": 0, "new": 0,
                      "by_source": {}, "errors": 0}
        self.status = "running"
        self.t0 = time.time()

    def add(self, msg: str):
        self.log.append(f"[{time.time() - self.t0:6.1f}s] {msg}")


RUNS: dict[int, RunState] = {}


def seed_companies(db: DB, path=None) -> int:
    path = path or settings.companies_file
    if not path.exists():
        return 0
    items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for it in items:
        db.upsert_company(Company(name=it["name"], website=it.get("website", ""), ats=it.get("ats", ""),
                                  ats_id=str(it.get("ats_id", "")), hq=it.get("hq", ""), notes=it.get("notes", ""),
                                  employees=it.get("employees"), size_source=it.get("size_source", "")))
    return len(items)


def _norm_company(name: str) -> str:
    return re.sub(r"\b(inc|llc|corp|corporation|co|ltd|the|company|group)\b|[^a-z0-9]", "", (name or "").lower())


def size_ok(employees, req: "SearchRequest") -> bool:
    if not req.max_employees:
        return True
    if employees is None or employees == "":
        return req.include_unknown_size
    return int(employees) <= req.max_employees


def import_companies_csv(db: DB, text: str) -> int:
    """CSV columns: name, website[, employees, ats, ats_id, hq]. Header row required."""
    n = 0
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if row.get("name"):
            emp = row.get("employees", "").replace(",", "")
            db.upsert_company(Company(name=row["name"], website=row.get("website", ""), ats=row.get("ats", ""),
                                      ats_id=row.get("ats_id", ""), hq=row.get("hq", ""),
                                      employees=int(emp) if emp.isdigit() else None,
                                      size_source="CSV import" if emp.isdigit() else ""))
            n += 1
    return n


def enrich(job: Job, req: SearchRequest) -> Job | None:
    """Apply Illinois + IT filters. Returns None if the job should be dropped."""
    job.is_illinois = is_illinois(job.location) or (not job.location and job.source in ("usajobs",))
    job.remote = job.remote or is_remote_us(job.location, job.remote)
    if not job.is_illinois and not (req.include_remote and job.remote and is_remote_us(job.location, job.remote)):
        return None
    cat, conf = classify_it(job.title, job.description, job.department)
    if not cat and job.source == "adzuna":  # already restricted to Adzuna's "IT Jobs" category
        cat, conf = "IT (Adzuna category)", 0.6
    if not cat and job.source == "usajobs":  # already restricted to IT occupational series
        cat, conf = "IT (Federal 2210/1550/0854)", 0.8
    if not cat:
        return None
    text = f"{job.title}\n{job.description}".lower()
    if any(x.lower() in text for x in req.exclude if x.strip()):
        return None
    if req.max_age_days and job.posted_at:
        if job.posted_at < (date.today() - timedelta(days=req.max_age_days)).isoformat():
            return None
    job.it_category, job.it_confidence = cat, conf
    job.level = detect_level(job.title)
    job.skills = extract_skills(f"{job.title}\n{job.description}")
    return job


async def run_search(db: DB, req: SearchRequest, run_id: int | None = None) -> int:
    run_id = run_id or db.create_run(req.__dict__)
    st = RUNS.setdefault(run_id, RunState(run_id))
    fetcher = Fetcher()
    ctx = Ctx(fetcher=fetcher, include_remote=req.include_remote, keywords=req.keywords, log=st.add)
    sizes = {_norm_company(c["name"]): c.get("employees") for c in db.list_companies()}

    def keep(jobs: list[Job], label: str, from_aggregator: bool = False):
        st.stats["fetched"] += len(jobs)
        kept = 0
        for j in jobs:
            if not j.title or not j.url:
                continue
            if from_aggregator and req.max_employees and not size_ok(sizes.get(_norm_company(j.company)), req):
                continue  # aggregator job from an employer not known to be small enough
            j = enrich(j, req)
            if j is None:
                continue
            kept += 1
            if db.upsert_job(j, run_id):
                st.stats["new"] += 1
            st.stats["by_source"][j.source] = st.stats["by_source"].get(j.source, 0) + 1
        st.stats["kept"] += kept
        return kept

    async def do_company(row: dict):
        c = Company(**{k: row.get(k) or "" for k in ("name", "website", "ats", "ats_id", "hq", "notes")})
        try:
            discovered_note = ""
            if c.ats not in CONNECTORS:
                res = await discover(c, ctx)
                if res.get("ats") in CONNECTORS:
                    c.ats, c.ats_id = res["ats"], res["ats_id"]
                    discovered_note = f" (discovered {c.ats}:{c.ats_id})"
                elif res.get("jobs"):
                    n = keep(res["jobs"], c.name)
                    db.update_company_crawl(c.name, n, ats="jsonld", ats_id=res["ats_id"])
                    st.add(f"{c.name}: {n} IL IT jobs from career page JSON-LD ({len(res['jobs'])} total)")
                    return
                else:
                    other = res.get("other_ats")
                    msg = f"uses {other} (no public API) - check {res.get('careers_url') or c.website} manually" if other \
                        else "no machine-readable careers page found (re-checked after 7 days)"
                    db.update_company_crawl(c.name, 0, error=msg, ats=other and f"unsupported:{other}" or "")
                    st.add(f"{c.name}: {msg}")
                    return
            jobs = await CONNECTORS[c.ats](c, ctx)
            n = keep(jobs, c.name)
            db.update_company_crawl(c.name, n, ats=c.ats, ats_id=c.ats_id)
            st.add(f"{c.name}: {n} IL IT jobs / {len(jobs)} scanned via {c.ats}{discovered_note}")
        except Exception as e:  # one failing company must never kill the run
            st.stats["errors"] += 1
            db.update_company_crawl(c.name, 0, error=str(e)[:300])
            st.add(f"{c.name}: ERROR {e}")
        finally:
            st.stats["companies_done"] += 1

    async def do_aggregator(name: str):
        if name == "usajobs" and req.max_employees:
            st.add("USAJOBS skipped: federal agencies exceed the employee-size limit")
            return
        try:
            jobs = await AGGREGATORS[name](ctx)
            if jobs:
                st.add(f"{name}: {keep(jobs, name, from_aggregator=True)} IL IT jobs from employers within the size "
                       f"limit / {len(jobs)} scanned")
        except Exception as e:
            st.stats["errors"] += 1
            st.add(f"{name}: ERROR {e}")

    tasks = []
    if "companies" in req.sources:
        companies = db.list_companies()
        if req.company_filter:
            wanted = {x.lower() for x in req.company_filter}
            companies = [c for c in companies if c["name"].lower() in wanted]
        companies = [c for c in companies if not (c.get("ats") or "").startswith("unsupported:")]
        if req.max_employees:
            before = len(companies)
            companies = [c for c in companies if size_ok(c.get("employees"), req)]
            st.add(f"Size filter: {len(companies)} of {before} employers have <= {req.max_employees} employees"
                   + ("" if not req.include_unknown_size else " or unknown size"))
        # Don't re-crawl sites that had no readable careers page in the last 7 days (`discover` re-checks them).
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        recent_miss = [c for c in companies if not c.get("ats") and (c.get("last_crawled") or "") >= cutoff
                       and c.get("last_error")]
        if recent_miss and not req.company_filter:
            companies = [c for c in companies if c not in recent_miss]
            st.add(f"Skipping {len(recent_miss)} employers with no readable careers page found in the last 7 days")
        st.stats["companies_total"] = len(companies)
        st.add(f"Crawling {len(companies)} company career sites...")
        tasks += [do_company(c) for c in companies]
    for name in AGGREGATORS:
        if name in req.sources:
            tasks.append(do_aggregator(name))
    try:
        await asyncio.gather(*tasks)
        st.status = "done"
    except asyncio.CancelledError:
        st.status = "cancelled"
        raise
    finally:
        await fetcher.aclose()
        st.add(f"Finished: {st.stats['kept']} matching jobs ({st.stats['new']} new) from {st.stats['fetched']} scanned.")
        db.finish_run(run_id, st.status, st.stats, st.log)
    return run_id


def ranked_results(db: DB, run_id: int | None, profile: str = "", keywords: list[str] | None = None) -> list[dict]:
    if run_id and not profile:
        run = db.get_run(run_id) or {}
        profile = run.get("params", {}).get("profile", "")
        keywords = keywords or run.get("params", {}).get("keywords", [])
    jobs = db.jobs_for_run(run_id)
    sizes = {_norm_company(c["name"]): c.get("employees") for c in db.list_companies()}
    for j in jobs:
        j["employees"] = sizes.get(_norm_company(j["company"]))
    return Ranker(profile, keywords).rank(jobs)
