"""US job databases: USAJOBS (federal), CareerOneStop / National Labor Exchange (US Dept. of Labor), Adzuna."""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from ..config import settings
from ..models import Job
from ..text import html_to_text, to_iso_date
from .base import Ctx

# OPM occupational series for IT: 2210 IT Management, 1550 Computer Science, 0854 Computer Engineering,
# 0391 Telecommunications, 1515 Operations Research, 0332 Computer Operation, 0335 Computer Clerk/Assistant
USAJOBS_SERIES = "2210;1550;0854;0391;0332;0335;1560"


async def usajobs(ctx: Ctx) -> list[Job]:
    if not (settings.usajobs_key and settings.usajobs_email):
        ctx.log("USAJOBS skipped (set USAJOBS_API_KEY and USAJOBS_EMAIL in .env)")
        return []
    headers = {"Host": "data.usajobs.gov", "User-Agent": settings.usajobs_email, "Authorization-Key": settings.usajobs_key}
    jobs, page = [], 1
    while page <= 10:
        url = (f"https://data.usajobs.gov/api/search?LocationName=Illinois&JobCategoryCode={USAJOBS_SERIES}"
               f"&ResultsPerPage=500&Page={page}")
        data = await ctx.fetcher.get_json(url, headers=headers)
        if not data:
            break
        items = data.get("SearchResult", {}).get("SearchResultItems", [])
        for it in items:
            m = it.get("MatchedObjectDescriptor", {})
            locs = [l.get("LocationName", "") for l in m.get("PositionLocation", [])]
            pay = (m.get("PositionRemuneration") or [{}])[0]
            details = (m.get("UserArea") or {}).get("Details") or {}
            desc = "\n\n".join(x for x in [details.get("JobSummary", ""), m.get("QualificationSummary", ""),
                                           "\n".join(details.get("MajorDuties") or [])] if x)
            jobs.append(Job(
                source="usajobs", company=f"{m.get('OrganizationName', '')} ({m.get('DepartmentName', '')})",
                title=m.get("PositionTitle", ""), url=(m.get("ApplyURI") or [m.get("PositionURI", "")])[0],
                location=" | ".join(locs), description=desc, posted_at=to_iso_date(m.get("PublicationStartDate")),
                department="Federal Government",
                employment_type=", ".join(s.get("Name", "") for s in m.get("PositionSchedule", [])),
                salary=f"${pay.get('MinimumRange', '')}-${pay.get('MaximumRange', '')} {pay.get('Description', '')}".strip(),
                external_id=m.get("PositionID", "") or it.get("MatchedObjectId", ""),
                remote=bool(details.get("RemoteIndicator")),
            ))
        if len(items) < 500:
            break
        page += 1
    return jobs


async def careeronestop(ctx: Ctx) -> list[Job]:
    """National Labor Exchange listings via CareerOneStop (sponsored by the US Dept. of Labor)."""
    if not (settings.cos_user_id and settings.cos_token):
        ctx.log("CareerOneStop skipped (set CAREERONESTOP_USER_ID and CAREERONESTOP_TOKEN in .env)")
        return []
    headers = {"Authorization": f"Bearer {settings.cos_token}"}
    queries = ctx.keywords + ["software developer", "information technology", "network administrator",
                              "data analyst", "cyber security", "help desk", "systems administrator", "devops"]

    async def run(q: str) -> list[Job]:
        out = []
        for start in (0, 100, 200):
            url = (f"https://api.careeronestop.org/v2/jobsearch/{settings.cos_user_id}/{quote(q)}/IL/0/0/0/"
                   f"{start}/100/30?showFilters=false")
            data = await ctx.fetcher.get_json(url, headers=headers)
            rows = (data or {}).get("Jobs") or []
            for r in rows:
                out.append(Job(
                    source="careeronestop", company=r.get("Company", ""), title=r.get("JobTitle", ""),
                    url=r.get("URL", ""), location=r.get("Location", ""), posted_at=to_iso_date(r.get("DatePosted")),
                    external_id=r.get("JvId", ""),
                ))
            if len(rows) < 100:
                break
        return out

    results = await asyncio.gather(*(run(q) for q in dict.fromkeys(queries)))
    return [j for rs in results for j in rs]


async def adzuna(ctx: Ctx) -> list[Job]:
    """Adzuna aggregates thousands of US job boards; restricted to the 'IT Jobs' category in Illinois."""
    if not (settings.adzuna_id and settings.adzuna_key):
        ctx.log("Adzuna skipped (set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env)")
        return []
    jobs = []
    for page in range(1, 21):  # up to 1,000 results
        url = (f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?app_id={settings.adzuna_id}"
               f"&app_key={settings.adzuna_key}&results_per_page=50&where=Illinois&category=it-jobs"
               f"&max_days_old=45&content-type=application/json")
        data = await ctx.fetcher.get_json(url)
        rows = (data or {}).get("results") or []
        for r in rows:
            smin, smax = r.get("salary_min"), r.get("salary_max")
            jobs.append(Job(
                source="adzuna", company=(r.get("company") or {}).get("display_name", ""), title=html_to_text(r.get("title")),
                url=r.get("redirect_url", ""),
                location=", ".join((r.get("location") or {}).get("area", [])[::-1][:3]) or (r.get("location") or {}).get("display_name", ""),
                description=html_to_text(r.get("description")), posted_at=to_iso_date(r.get("created")),
                employment_type=r.get("contract_time", "") or "", external_id=str(r.get("id", "")),
                salary=f"${smin:,.0f}-${smax:,.0f}" if smin and smax else "",
            ))
        if len(rows) < 50:
            break
    return jobs


AGGREGATORS = {"usajobs": usajobs, "careeronestop": careeronestop, "adzuna": adzuna}
