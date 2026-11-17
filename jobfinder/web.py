"""FastAPI web dashboard: start searches, watch progress live, browse ranked jobs, manage companies."""
from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings
from .db import DB
from .engine import ALL_SOURCES, RUNS, RunState, SearchRequest, import_companies_csv, ranked_results, run_search, seed_companies
from .models import Company

app = FastAPI(title="Illinois IT Job Finder")
db = DB(settings.db_path)
if not db.list_companies():
    seed_companies(db)
STATIC = Path(__file__).parent / "static"
TASKS: dict[int, asyncio.Task] = {}


class SearchIn(BaseModel):
    profile: str = ""
    keywords: list[str] = []
    exclude: list[str] = []
    include_remote: bool = True
    sources: list[str] = ALL_SOURCES
    max_age_days: int = 0
    company_filter: list[str] = []
    max_employees: int = 300
    include_unknown_size: bool = False


class CompanyIn(BaseModel):
    name: str
    website: str = ""
    ats: str = ""
    ats_id: str = ""
    hq: str = ""
    employees: int | None = None


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    return {
        "sources": {
            "companies": True,
            "usajobs": bool(settings.usajobs_key and settings.usajobs_email),
            "careeronestop": bool(settings.cos_user_id and settings.cos_token),
            "adzuna": bool(settings.adzuna_id and settings.adzuna_key),
        },
        "company_count": len(db.list_companies()),
        "pdl": bool(settings.pdl_key),
        "small_count": sum(1 for c in db.list_companies() if c.get("employees") is not None and c["employees"] <= 300),
    }


@app.post("/api/search")
async def start_search(body: SearchIn):
    req = SearchRequest(**body.model_dump())
    run_id = db.create_run(req.__dict__)
    RUNS[run_id] = RunState(run_id)
    TASKS[run_id] = asyncio.create_task(run_search(db, req, run_id))
    return {"run_id": run_id}


@app.post("/api/runs/{run_id}/cancel")
def cancel(run_id: int):
    t = TASKS.get(run_id)
    if t and not t.done():
        t.cancel()
    return {"ok": True}


@app.get("/api/runs")
def runs():
    return db.list_runs()


@app.get("/api/runs/{run_id}")
def run_status(run_id: int, since: int = 0):
    st = RUNS.get(run_id)
    if st:
        return {"status": st.status, "stats": st.stats, "log": st.log[since:], "log_len": len(st.log)}
    r = db.get_run(run_id)
    if not r:
        raise HTTPException(404)
    log = (r.get("log") or "").splitlines()
    return {"status": r["status"], "stats": r["stats"], "log": log[since:], "log_len": len(log)}


@app.get("/api/jobs")
def jobs(run_id: int = 0, profile: str = "", keywords: str = ""):
    kw = [k.strip() for k in keywords.split(",") if k.strip()] or None
    rows = ranked_results(db, run_id or None, profile, kw)
    for r in rows:
        r["description"] = (r.get("description") or "")[:4000]
    return rows


class StatusIn(BaseModel):
    status: str


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusIn):
    if body.status not in ("new", "saved", "applied", "hidden"):
        raise HTTPException(400, "bad status")
    db.set_status(job_id, body.status)
    return {"ok": True}


@app.get("/api/export.csv")
def export(run_id: int = 0):
    rows = ranked_results(db, run_id or None)
    buf = io.StringIO()
    cols = ["score", "title", "company", "location", "it_category", "level", "posted_at", "salary", "source",
            "status", "matched_skills", "missing_skills", "url"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        if r.get("status") == "hidden":
            continue
        w.writerow([", ".join(r[c]) if isinstance(r.get(c), list) else r.get(c, "") for c in cols])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=illinois-it-jobs.csv"})


@app.get("/api/companies")
def companies():
    return db.list_companies()


@app.post("/api/companies")
def add_company(body: CompanyIn):
    c = Company(**body.model_dump())
    if c.employees is not None:
        c.size_source = "manual"
    db.upsert_company(c, overwrite_ats=bool(body.ats))
    return {"ok": True}


class SizeIn(BaseModel):
    employees: int | None = None


@app.post("/api/companies/{cid}/size")
def set_size(cid: int, body: SizeIn):
    row = next((c for c in db.list_companies() if c["id"] == cid), None)
    if not row:
        raise HTTPException(404)
    db.set_company_size(row["name"], body.employees, "manual" if body.employees is not None else "")
    return {"ok": True}


class FindIn(BaseModel):
    max_employees: int = 300
    min_employees: int = 5
    count: int = 100


@app.post("/api/companies/find")
async def find_companies_api(body: FindIn):
    from .sizes import find_companies
    log: list[str] = []
    try:
        n = await find_companies(db, body.max_employees, min(body.count, 1000), body.min_employees, log=log.append)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"added": n, "log": log}


@app.post("/api/companies/enrich")
async def enrich_api(limit: int = 50):
    from .sizes import enrich_sizes
    log: list[str] = []
    try:
        n = await enrich_sizes(db, limit, log=log.append)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"updated": n, "log": log}


@app.post("/api/companies/import")
def import_csv(body: dict):
    return {"imported": import_companies_csv(db, body.get("csv", ""))}


@app.delete("/api/companies/{cid}")
def delete_company(cid: int):
    db.delete_company(cid)
    return {"ok": True}
