"""Connectors for the Applicant Tracking Systems that power most company career pages.

These are the same public endpoints the companies' own career pages call, so results
are the employer's live openings (not stale aggregator copies).
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from ..filters import is_illinois
from ..models import Company, Job
from ..text import html_to_text, to_iso_date
from .base import Ctx


# ------------------------------------------------------------------ Greenhouse
async def greenhouse(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(f"https://boards-api.greenhouse.io/v1/boards/{c.ats_id}/jobs?content=true")
    if not data:
        raise RuntimeError(f"greenhouse board '{c.ats_id}' not found")
    jobs = []
    for j in data.get("jobs", []):
        locs = [j.get("location", {}).get("name", "")] + [o.get("location") or o.get("name", "") for o in j.get("offices", [])]
        location = " | ".join(dict.fromkeys(l for l in locs if l))
        jobs.append(Job(
            source="greenhouse", company=c.name, title=j.get("title", ""), url=j.get("absolute_url", ""),
            location=location, description=html_to_text(j.get("content")), posted_at=to_iso_date(j.get("updated_at")),
            department=", ".join(d.get("name", "") for d in j.get("departments", [])), external_id=str(j.get("id")),
            remote="remote" in location.lower(),
        ))
    return jobs


# ------------------------------------------------------------------ Lever
async def lever(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(f"https://api.lever.co/v0/postings/{c.ats_id}?mode=json")
    if data is None:
        raise RuntimeError(f"lever site '{c.ats_id}' not found")
    jobs = []
    for j in data:
        cat = j.get("categories", {}) or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        location = " | ".join(l for l in locs if l)
        desc = j.get("descriptionPlain", "") + "\n" + "\n".join(
            f"{l.get('text', '')}\n{html_to_text(l.get('content', ''))}" for l in j.get("lists", []))
        jobs.append(Job(
            source="lever", company=c.name, title=j.get("text", ""), url=j.get("hostedUrl", ""), location=location,
            description=desc.strip(), posted_at=to_iso_date(j.get("createdAt")), department=cat.get("team", ""),
            employment_type=cat.get("commitment", ""), external_id=j.get("id", ""),
            remote=j.get("workplaceType") == "remote", salary=_lever_salary(j.get("salaryRange")),
        ))
    return jobs


def _lever_salary(s) -> str:
    if not s:
        return ""
    return f"{s.get('currency', '')} {s.get('min', 0):,}-{s.get('max', 0):,} / {s.get('interval', '')}".strip()


# ------------------------------------------------------------------ Ashby
async def ashby(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{c.ats_id}?includeCompensation=true")
    if not data:
        raise RuntimeError(f"ashby board '{c.ats_id}' not found")
    jobs = []
    for j in data.get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        if addr.get("addressRegion"):
            locs.append(f"{addr.get('addressLocality', '')}, {addr.get('addressRegion')}")
        location = " | ".join(dict.fromkeys(l for l in locs if l))
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        jobs.append(Job(
            source="ashby", company=c.name, title=j.get("title", ""), url=j.get("jobUrl", ""), location=location,
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            posted_at=to_iso_date(j.get("publishedAt")), department=j.get("department", "") or "",
            employment_type=j.get("employmentType", "") or "", external_id=j.get("id", ""),
            remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote", salary=comp,
        ))
    return jobs


# ------------------------------------------------------------------ SmartRecruiters
async def smartrecruiters(c: Company, ctx: Ctx) -> list[Job]:
    base = f"https://api.smartrecruiters.com/v1/companies/{c.ats_id}/postings"
    stubs, offset = [], 0
    while True:
        data = await ctx.fetcher.get_json(f"{base}?limit=100&offset={offset}&country=us")
        if not data:
            if offset == 0:
                raise RuntimeError(f"smartrecruiters company '{c.ats_id}' not found")
            break
        content = data.get("content", [])
        stubs.extend(content)
        offset += len(content)
        if not content or offset >= data.get("totalFound", 0) or offset >= 2000:
            break

    async def detail(s):
        loc = s.get("location", {}) or {}
        location = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country", "").upper()] if x)
        remote = bool(loc.get("remote"))
        if not ctx.prefilter(s.get("name", ""), location, remote):
            return None
        d = await ctx.fetcher.get_json(f"{base}/{s['id']}") or {}
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        desc = "\n\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                           for k in ("jobDescription", "qualifications", "additionalInformation"))
        return Job(
            source="smartrecruiters", company=c.name, title=s.get("name", ""),
            url=d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{c.ats_id}/{s['id']}", location=location,
            description=desc, posted_at=to_iso_date(s.get("releasedDate")),
            department=(s.get("department") or {}).get("label", ""),
            employment_type=(s.get("typeOfEmployment") or {}).get("label", ""), external_id=s["id"], remote=remote,
        )

    return [j for j in await asyncio.gather(*(detail(s) for s in stubs)) if j]


# ------------------------------------------------------------------ Workday
def parse_workday_url(url: str) -> tuple[str, str, str]:
    """https://acme.wd5.myworkdayjobs.com/en-US/External_Careers -> (host, tenant, site)."""
    p = urlparse(url if "://" in url else "https://" + url)
    host = p.netloc
    tenant = host.split(".")[0]
    parts = [x for x in p.path.split("/") if x and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", x)]
    if parts and parts[0] == "wday":  # already an API url: /wday/cxs/{tenant}/{site}
        parts = parts[3:]
    site = parts[0] if parts else "External"
    return host, tenant, site


def workday_location_facets(facets: list) -> tuple[dict, dict]:
    """Find Workday facet values for Illinois (state or IL cities) and for US-remote."""
    from ..filters import is_illinois

    flat: list[tuple[str, str, str]] = []  # (param, id, descriptor)

    def walk(items, param=None):
        for f in items or []:
            p = f.get("facetParameter") or param
            if f.get("values"):
                walk(f["values"], p)
            elif f.get("id") and p:
                flat.append((p, f["id"], f.get("descriptor") or ""))

    walk(facets)
    il: dict[str, list] = {}
    state = [(p, i) for p, i, d in flat if d.strip().lower() == "illinois"]
    if state:
        for p, i in state:
            il.setdefault(p, []).append(i)
    else:
        for p, i, d in flat:
            if "country" not in p.lower() and is_illinois(d):
                il.setdefault(p, []).append(i)
    if il:  # use only the single most specific parameter so facets don't AND each other out
        p = max(il, key=lambda k: len(il[k]))
        il = {p: il[p]}
    remote: dict[str, list] = {}
    for p, i, d in flat:
        if p.lower() == "remotetype" and d.strip().lower() in ("remote", "fully remote", "100% remote"):
            remote.setdefault(p, []).append(i)
        if "country" in p.lower() and d.strip().lower() in ("united states of america", "united states", "usa"):
            remote.setdefault(p, []).append(i)
    if not any(k.lower() == "remotetype" for k in remote):
        remote = {}
    return il, remote


async def workday(c: Company, ctx: Ctx) -> list[Job]:
    from ..config import settings

    host, tenant, site = parse_workday_url(c.ats_id)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen: dict[str, dict] = {}
    probe = await ctx.fetcher.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    if probe is None:
        raise RuntimeError(f"workday site {c.ats_id} not reachable")

    il_paths: set[str] = set()

    async def search(q: str, facets: dict, max_pages: int, is_il: bool = False):
        # Workday caps page size at 20; fetch page 1 to learn the total, then the rest in parallel.
        first = await ctx.fetcher.post_json(
            f"{api}/jobs", {"appliedFacets": facets, "limit": 20, "offset": 0, "searchText": q})
        total = min((first or {}).get("total") or 0, max_pages * 20)
        pages = [first] + await asyncio.gather(*(
            ctx.fetcher.post_json(f"{api}/jobs", {"appliedFacets": facets, "limit": 20, "offset": off, "searchText": q})
            for off in range(20, total, 20)))
        for data in pages:
            for p in (data or {}).get("jobPostings") or []:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p)
                    if is_il:
                        il_paths.add(p["externalPath"])

    il_facets, remote_facets = workday_location_facets(probe.get("facets") or [])
    if il_facets:
        # Server-side Illinois filter: list every Illinois posting (title pre-filter happens below).
        jobs_lists = [search("", il_facets, 60, is_il=True)]
        if ctx.include_remote and remote_facets:
            jobs_lists += [search(q, remote_facets, 3) for q in ctx.queries[:8]]
        await asyncio.gather(*jobs_lists)
    else:
        await asyncio.gather(*(search(q, {}, settings.workday_max_pages) for q in ctx.queries))

    async def detail(path: str, p: dict):
        loc_text = p.get("locationsText", "") or ""
        multi = bool(re.match(r"\d+ Locations", loc_text))
        in_il = path in il_paths
        if not ctx.prefilter(p.get("title", ""), loc_text, location_unknown=multi or in_il):
            return None
        d = await ctx.fetcher.get_json(f"{api}{path}")
        info = (d or {}).get("jobPostingInfo") or {}
        locs = [info.get("location", "") or loc_text] + (info.get("additionalLocations") or [])
        location = " | ".join(l for l in locs if l)
        if in_il and not is_illinois(location):
            location += " | Illinois (employer location filter)"
        return Job(
            source="workday", company=c.name, title=info.get("title") or p.get("title", ""),
            url=info.get("externalUrl") or f"https://{host}/{site}{path}", location=location,
            description=html_to_text(info.get("jobDescription")),
            posted_at=to_iso_date(info.get("startDate") or p.get("postedOn")),
            employment_type=info.get("timeType", ""), external_id=info.get("jobReqId") or path,
            remote="remote" in (info.get("remoteType") or "").lower(),
        )

    return [j for j in await asyncio.gather(*(detail(k, v) for k, v in seen.items())) if j]


# ------------------------------------------------------------------ Small-company ATS
def _place(*parts) -> str:
    return ", ".join(str(p) for p in parts if p)


async def workable(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{c.ats_id}?details=true")
    if not data:
        raise RuntimeError(f"workable account '{c.ats_id}' not found (or rate-limited)")
    jobs = []
    for j in data.get("jobs", []):
        locs = [_place(l.get("city"), l.get("region"), l.get("country")) for l in j.get("locations") or []]
        location = " | ".join(dict.fromkeys([l for l in locs if l] or [_place(j.get("city"), j.get("state"), j.get("country"))]))
        remote = bool(j.get("telecommuting"))
        jobs.append(Job(
            source="workable", company=c.name, title=j.get("title", ""), url=j.get("url") or j.get("shortlink", ""),
            location=location + (" | Remote" if remote and "remote" not in location.lower() else ""),
            description=html_to_text(j.get("description")), posted_at=to_iso_date(j.get("published_on") or j.get("created_at")),
            department=j.get("department", "") or "", employment_type=j.get("employment_type", "") or "",
            external_id=j.get("shortcode", ""), remote=remote,
        ))
    return jobs


async def bamboohr(c: Company, ctx: Ctx) -> list[Job]:
    base = f"https://{c.ats_id}.bamboohr.com/careers"
    data = await ctx.fetcher.get_json(f"{base}/list")
    if not data or not isinstance(data.get("result"), list):
        raise RuntimeError(f"bamboohr site '{c.ats_id}' not found")

    async def detail(s):
        loc = s.get("location") or {}
        location = _place(loc.get("city"), loc.get("state"))
        remote = bool(s.get("isRemote"))
        if not ctx.prefilter(s.get("jobOpeningName", ""), location, remote):
            return None
        d = ((await ctx.fetcher.get_json(f"{base}/{s['id']}/detail")) or {}).get("result", {}).get("jobOpening", {})
        return Job(
            source="bamboohr", company=c.name, title=s.get("jobOpeningName", ""), url=f"{base}/{s['id']}",
            location=location + (" | Remote" if remote else ""), description=html_to_text(d.get("description")),
            posted_at=to_iso_date(d.get("datePosted")), department=s.get("departmentLabel", "") or "",
            employment_type=s.get("employmentStatusLabel", "") or "", external_id=str(s["id"]), remote=remote,
            salary=str(d.get("compensation") or ""),
        )

    return [j for j in await asyncio.gather(*(detail(s) for s in data["result"])) if j]


async def breezy(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(f"https://{c.ats_id}.breezy.hr/json")
    if not isinstance(data, list):
        raise RuntimeError(f"breezy site '{c.ats_id}' not found")
    jobs = []
    for j in data:
        loc = j.get("location") or {}
        state = loc.get("state") or {}
        location = _place(loc.get("city"), state.get("id") if isinstance(state, dict) else state,
                          (loc.get("country") or {}).get("id") if isinstance(loc.get("country"), dict) else "")
        remote = bool(loc.get("is_remote"))
        jobs.append(Job(
            source="breezy", company=c.name, title=j.get("name", ""), url=j.get("url", ""),
            location=location + (" | Remote" if remote else ""), posted_at=to_iso_date(j.get("published_date")),
            department=j.get("department", "") or "", employment_type=(j.get("type") or {}).get("name", ""),
            external_id=j.get("id", ""), remote=remote, salary=j.get("salary", "") or "",
        ))
    return jobs


async def recruitee(c: Company, ctx: Ctx) -> list[Job]:
    data = await ctx.fetcher.get_json(f"https://{c.ats_id}.recruitee.com/api/offers/")
    if not data or not isinstance(data.get("offers"), list):
        raise RuntimeError(f"recruitee site '{c.ats_id}' not found")
    jobs = []
    for j in data["offers"]:
        locs = [_place(l.get("city"), l.get("state"), l.get("country")) for l in j.get("locations") or []]
        location = " | ".join([l for l in locs if l] or [_place(j.get("city"), j.get("state_name"), j.get("country"))])
        remote = bool(j.get("remote"))
        jobs.append(Job(
            source="recruitee", company=c.name, title=j.get("title", ""), url=j.get("careers_url", ""),
            location=location + (" | Remote" if remote else ""),
            description=html_to_text((j.get("description") or "") + (j.get("requirements") or "")),
            posted_at=to_iso_date(j.get("published_at") or j.get("created_at")), department=j.get("department", "") or "",
            employment_type=j.get("employment_type_code", "") or "", external_id=str(j.get("id", "")), remote=remote,
        ))
    return jobs


# ------------------------------------------------------------------ iCIMS
async def icims(c: Company, ctx: Ctx) -> list[Job]:
    from .careerpage import extract_jsonld_jobs

    host = c.ats_id.replace("https://", "").strip("/")
    links: dict[str, str] = {}
    link_re = re.compile(r'<a[^>]+href="(https://[^"]+/jobs/(\d+)/[^"]+/job)[^"]*"[^>]*>(.*?)</a>', re.S | re.I)

    async def search(q: str):
        for page in range(5):
            got = await ctx.fetcher.get_text(
                f"https://{host}/jobs/search?ss=1&searchKeyword={q}&pr={page}&in_iframe=1")
            if not got:
                return
            found = link_re.findall(got[1])
            before = len(links)
            for url, jid, inner in found:
                links.setdefault(url, html_to_text(inner))
            if not found or len(links) == before:
                return

    probe = await ctx.fetcher.get_text(f"https://{host}/jobs/search?ss=1&in_iframe=1")
    if not probe:
        if not await ctx.fetcher.allowed(f"https://{host}/jobs/search"):
            raise RuntimeError(f"career site https://{host} disallows automated access (robots.txt) - "
                               "check it manually; its jobs usually also appear via Adzuna/CareerOneStop")
        raise RuntimeError(f"iCIMS site {host} not reachable")
    await asyncio.gather(*(search(q) for q in ctx.queries[:10]))

    async def detail(url: str, title: str):
        slug_title = re.sub(r"[-_]+", " ", url.rstrip("/").split("/")[-2])
        if not ctx.prefilter(title or slug_title, location_unknown=True):
            return []
        got = await ctx.fetcher.get_text(url + "?in_iframe=1")
        if not got:
            return []
        jobs = extract_jsonld_jobs(got[1], url, c.name)
        for j in jobs:
            j.source, j.url, j.company = "icims", url, c.name
        return jobs

    results = await asyncio.gather(*(detail(u, t) for u, t in links.items()))
    return [j for rs in results for j in rs]


CONNECTORS = {
    "icims": icims,
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "workable": workable,
    "bamboohr": bamboohr,
    "breezy": breezy,
    "recruitee": recruitee,
}
