"""SQLite persistence: jobs (with first/last seen + user status), companies, search runs."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Company, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, fingerprint TEXT, source TEXT, title TEXT, company TEXT, url TEXT, location TEXT,
  description TEXT, posted_at TEXT, department TEXT, employment_type TEXT, salary TEXT, remote INTEGER,
  is_illinois INTEGER, it_category TEXT, it_confidence REAL, level TEXT, skills TEXT,
  first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS ix_jobs_fp ON jobs(fingerprint);
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, website TEXT, ats TEXT, ats_id TEXT,
  hq TEXT, notes TEXT, last_crawled TEXT, last_count INTEGER DEFAULT 0, last_error TEXT,
  UNIQUE(name)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, status TEXT, params TEXT,
  stats TEXT, log TEXT
);
CREATE TABLE IF NOT EXISTS run_jobs (run_id INTEGER, job_id TEXT, PRIMARY KEY(run_id, job_id));
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(companies)")}
            for col, typ in (("employees", "INTEGER"), ("size_source", "TEXT")):
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {typ}")
            self.conn.commit()

    # ------------------------------------------------------------ jobs
    def upsert_job(self, job: Job, run_id: int | None = None) -> bool:
        """Insert or refresh a job. Returns True if it is brand new."""
        d = job.to_dict()
        ts = now()
        with self.lock:
            cur = self.conn.execute("SELECT id FROM jobs WHERE id=?", (d["id"],))
            is_new = cur.fetchone() is None
            if is_new:
                self.conn.execute(
                    """INSERT INTO jobs (id,fingerprint,source,title,company,url,location,description,posted_at,
                    department,employment_type,salary,remote,is_illinois,it_category,it_confidence,level,skills,
                    first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["id"], d["fingerprint"], d["source"], d["title"], d["company"], d["url"], d["location"],
                     d["description"], d["posted_at"], d["department"], d["employment_type"], d["salary"],
                     int(d["remote"]), int(d["is_illinois"]), d["it_category"], d["it_confidence"], d["level"],
                     json.dumps(d["skills"]), ts, ts),
                )
            else:
                self.conn.execute(
                    """UPDATE jobs SET title=?,location=?,description=CASE WHEN length(?)>length(coalesce(description,''))
                    THEN ? ELSE description END,posted_at=coalesce(?,posted_at),salary=coalesce(nullif(?,''),salary),
                    it_category=?,level=?,skills=?,last_seen=? WHERE id=?""",
                    (d["title"], d["location"], d["description"], d["description"], d["posted_at"], d["salary"],
                     d["it_category"], d["level"], json.dumps(d["skills"]), ts, d["id"]),
                )
            if run_id is not None:
                self.conn.execute("INSERT OR IGNORE INTO run_jobs VALUES (?,?)", (run_id, d["id"]))
            self.conn.commit()
        return is_new

    def jobs_for_run(self, run_id: int | None) -> list[dict]:
        with self.lock:
            if run_id:
                rows = self.conn.execute(
                    "SELECT j.* FROM jobs j JOIN run_jobs r ON r.job_id=j.id WHERE r.run_id=?", (run_id,)).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM jobs").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["skills"] = json.loads(d["skills"] or "[]")
            d["remote"] = bool(d["remote"])
            d["is_illinois"] = bool(d["is_illinois"])
            out.append(d)
        return dedupe(out)

    def set_status(self, job_id: str, status: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
            self.conn.commit()

    # ------------------------------------------------------------ companies
    def list_companies(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM companies ORDER BY name").fetchall()]

    def upsert_company(self, c: Company, overwrite_ats: bool = False) -> None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM companies WHERE name=?", (c.name,)).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO companies (name,website,ats,ats_id,hq,notes,employees,size_source) VALUES (?,?,?,?,?,?,?,?)",
                    (c.name, c.website, c.ats, c.ats_id, c.hq, c.notes, c.employees, c.size_source))
            else:
                ats = c.ats if (overwrite_ats or not row["ats"]) and c.ats else row["ats"]
                ats_id = c.ats_id if (overwrite_ats or not row["ats_id"]) and c.ats_id else row["ats_id"]
                self.conn.execute(
                    """UPDATE companies SET website=coalesce(nullif(?,''),website), ats=?, ats_id=?,
                    hq=coalesce(nullif(?,''),hq), employees=coalesce(?,employees),
                    size_source=coalesce(nullif(?,''),size_source) WHERE name=?""",
                    (c.website, ats, ats_id, c.hq, c.employees, c.size_source, c.name))
            self.conn.commit()

    def update_company_crawl(self, name: str, count: int, error: str = "", ats: str = "", ats_id: str = "") -> None:
        with self.lock:
            self.conn.execute(
                """UPDATE companies SET last_crawled=?, last_count=?, last_error=?,
                ats=CASE WHEN ?!='' THEN ? ELSE ats END, ats_id=CASE WHEN ?!='' THEN ? ELSE ats_id END WHERE name=?""",
                (now(), count, error, ats, ats, ats_id, ats_id, name))
            self.conn.commit()

    def set_company_size(self, name: str, employees: int | None, source: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE companies SET employees=?, size_source=? WHERE name=?", (employees, source, name))
            self.conn.commit()

    def delete_company(self, cid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM companies WHERE id=?", (cid,))
            self.conn.commit()

    # ------------------------------------------------------------ runs
    def create_run(self, params: dict) -> int:
        with self.lock:
            cur = self.conn.execute("INSERT INTO runs (started,status,params,stats,log) VALUES (?,?,?,?,?)",
                                    (now(), "running", json.dumps(params), "{}", ""))
            self.conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, stats: dict, log: list[str]) -> None:
        with self.lock:
            self.conn.execute("UPDATE runs SET finished=?, status=?, stats=?, log=? WHERE id=?",
                              (now(), status, json.dumps(stats), "\n".join(log[-2000:]), run_id))
            self.conn.commit()

    def get_run(self, run_id: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params"] or "{}")
        d["stats"] = json.loads(d["stats"] or "{}")
        return d

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT id, started, finished, status, stats FROM runs ORDER BY id DESC LIMIT ?",
                                     (limit,)).fetchall()
        return [{**dict(r), "stats": json.loads(r["stats"] or "{}")} for r in rows]


SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "workday": 0, "smartrecruiters": 0, "careerpage": 1,
                   "usajobs": 0, "careeronestop": 2, "adzuna": 3}


def dedupe(jobs: list[dict]) -> list[dict]:
    """Collapse cross-source duplicates, preferring direct employer sources over aggregators."""
    import re

    def key(j):  # same employer + same title = one opening, even if posted per city
        n = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
        return f"{n(j['company'])}|{n(j['title'])}"

    best: dict[str, dict] = {}
    for j in jobs:
        fp = key(j)
        cur = best.get(fp)
        if cur is None or (SOURCE_PRIORITY.get(j["source"], 5), -len(j.get("description") or "")) < \
                (SOURCE_PRIORITY.get(cur["source"], 5), -len(cur.get("description") or "")):
            if cur is not None:
                j = {**j, "also_on": sorted(set(cur.get("also_on", []) + [cur["source"]]) - {j["source"]})}
                if cur["location"] and cur["location"] not in j["location"]:
                    j["location"] = f"{j['location']} | {cur['location']}"
                    j["is_illinois"] = j["is_illinois"] or cur["is_illinois"]
            best[fp] = j
        else:
            if cur["location"] and j["location"] and j["location"] not in cur["location"]:
                cur["location"] = f"{cur['location']} | {j['location']}"
                cur["is_illinois"] = cur["is_illinois"] or j["is_illinois"]
            cur.setdefault("also_on", [])
            if j["source"] not in cur["also_on"] and j["source"] != cur["source"]:
                cur["also_on"].append(j["source"])
    return list(best.values())
