"""Company headcounts + discovery of Illinois employers by size, via People Data Labs (optional).

People Data Labs (https://www.peopledatalabs.com) has a free API tier. Set PDL_API_KEY in .env.
  - find_companies(): adds Illinois tech/IT employers with <= N employees (1 credit per company returned)
  - enrich_sizes():   fills in missing employee counts for tracked companies (1 credit per match)
API docs: https://docs.peopledatalabs.com/docs/company-search-api
"""
from __future__ import annotations

from .config import settings
from .db import DB
from .http import Fetcher
from .models import Company

PDL = "https://api.peopledatalabs.com/v5/company"

IT_INDUSTRIES = ["computer software", "information technology and services", "internet", "computer & network security",
                 "computer networking", "computer hardware", "financial services", "hospital & health care",
                 "marketing and advertising", "insurance", "telecommunications", "management consulting",
                 "computer games", "e-learning", "staffing and recruiting"]


def _headers() -> dict:
    if not settings.pdl_key:
        raise RuntimeError("PDL_API_KEY is not set. Get a free key at https://www.peopledatalabs.com/signup "
                           "and add PDL_API_KEY=... to the .env file.")
    return {"X-Api-Key": settings.pdl_key}


def _employees(d: dict) -> int | None:
    if d.get("employee_count") is not None:
        return int(d["employee_count"])
    size = str(d.get("size") or "")  # e.g. "11-50" -> upper bound (conservative)
    digits = size.split("-")[-1].replace("+", "").strip()
    return int(digits) if digits.isdigit() else None


async def find_companies(db: DB, max_employees: int = 300, count: int = 100, min_employees: int = 5,
                         log=print) -> int:
    """Add Illinois employers (HQ region = Illinois) with min..max employees in tech-heavy industries."""
    headers = _headers()
    inds = ", ".join(f"'{i}'" for i in IT_INDUSTRIES)
    sql = (f"SELECT * FROM company WHERE location.region='illinois' "
           f"AND employee_count>={int(min_employees)} AND employee_count<={int(max_employees)} "
           f"AND industry IN ({inds}) AND website IS NOT NULL")
    f = Fetcher()
    added, scroll = 0, None
    try:
        while added < count:
            body = {"sql": sql, "size": min(100, count - added), "titlecase": True}
            if scroll:
                body["scroll_token"] = scroll
            r = await f.request("POST", f"{PDL}/search", json=body, headers=headers)
            if r is None:
                log("People Data Labs: network error")
                break
            if r.status_code == 404:  # no (more) matches
                break
            if r.status_code != 200:
                log(f"People Data Labs error {r.status_code}: {r.text[:300]}")
                break
            d = r.json()
            for row in d.get("data", []):
                site = row.get("website") or ""
                loc = row.get("location") or {}
                db.upsert_company(Company(
                    name=(row.get("display_name") or row.get("name") or site).strip(),
                    website=site if "://" in site or not site else "https://" + site,
                    hq=(loc.get("locality") or "").title(), employees=_employees(row),
                    size_source="People Data Labs", notes=row.get("industry", "") or ""))
                added += 1
            log(f"People Data Labs: {added} employers added so far (of {d.get('total', '?')} matching)")
            scroll = d.get("scroll_token")
            if not scroll or not d.get("data"):
                break
    finally:
        await f.aclose()
    log(f"Added/updated {added} Illinois employers with {min_employees}-{max_employees} employees. "
        "Their career sites are discovered automatically on the next search.")
    return added


async def enrich_sizes(db: DB, limit: int = 50, log=print) -> int:
    """Look up headcounts for tracked companies whose size is unknown."""
    headers = _headers()
    f = Fetcher()
    n = 0
    try:
        for c in [c for c in db.list_companies() if c.get("employees") is None][:limit]:
            params = {"name": c["name"], "min_likelihood": 4 if c.get("website") else 6}
            if c.get("website"):
                params["website"] = c["website"]
            else:
                params["region"] = "illinois"
            r = await f.request("GET", f"{PDL}/enrich", params=params, headers=headers)
            if r is None or r.status_code != 200:
                log(f"{c['name']}: no match ({r.status_code if r is not None else 'network error'})")
                continue
            emp = _employees(r.json())
            if emp is not None:
                db.set_company_size(c["name"], emp, "People Data Labs")
                n += 1
                log(f"{c['name']}: {emp} employees")
    finally:
        await f.aclose()
    log(f"Looked up {n} company sizes.")
    return n
