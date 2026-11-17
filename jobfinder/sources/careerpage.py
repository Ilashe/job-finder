"""Generic company-website crawler.

1. Starts at the company homepage (respecting robots.txt).
2. Follows links that look like a careers/jobs page (up to a small depth).
3. Detects the Applicant Tracking System behind it (Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters) so future runs can use the fast structured API.
4. Extracts schema.org JobPosting JSON-LD (used by most other career sites for Google Jobs).
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import Company, Job
from ..text import html_to_text, to_iso_date
from .base import Ctx

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workday", re.compile(r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v\d/widget/accounts/)?([A-Za-z0-9_-]+)", re.I)),
    ("bamboohr", re.compile(r"//([a-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)", re.I)),
    ("breezy", re.compile(r"//([a-z0-9-]+)\.breezy\.hr", re.I)),
    ("recruitee", re.compile(r"//([a-z0-9-]+)\.recruitee\.com", re.I)),
]
# Detected but no public API connector -> reported so the user knows where to look.
OTHER_ATS = {"taleo": r"taleo\.net", "successfactors": r"successfactors|jobs\.sap\.com",
             "oracle-hcm": r"oraclecloud\.com/hcmUI", "ukg": r"ultipro\.com|ukg\.net", "adp": r"workforcenow\.adp\.com",
             "paylocity": r"recruiting\.paylocity\.com", "jobvite": r"jobs\.jobvite\.com", "bamboohr": r"bamboohr\.com/careers"}
BAD_SLUGS = {"embed", "js", "jobs", "job_board", "v1", "api", "careers", "en-us", "search", "wday",
             "my-applications", "oneclick-ui", "sign-in", "login", "static", "assets", "www", "app", "j"}
CAREER_LINK = re.compile(r"career|jobs?\b|join[- ]?(us|our)|work[- ]?(with|for|at)[- ]?us|opportunit|hiring|employment|vacanc|openings", re.I)


_ICIMS_RE = re.compile(r"https?://([a-z0-9-]+\.icims\.com)", re.I)


def _icims_score(host: str) -> int:
    h = host.lower()
    if h.startswith(("www.", "cookie", "events", "login", "fdcnm", "cdcm")) or h.count(".") != 2:
        return -99
    return 2 * ("-" in h) + 2 * ("career" in h) + ("external" in h) + ("unitedstates" in h or h.startswith("us-"))


def detect_ats(html: str) -> tuple[str, str] | None:
    for ats, rx in ATS_PATTERNS:
        for m in rx.finditer(html):
            slug = m.group(1).rstrip("/")
            if slug.lower() not in BAD_SLUGS:
                return ats, slug
    hosts = {h.lower() for h in _ICIMS_RE.findall(html)}
    if hosts:
        best = max(hosts, key=_icims_score)
        if _icims_score(best) > -99:
            return "icims", best
    return None


def detect_other_ats(html: str) -> str:
    return next((name for name, rx in OTHER_ATS.items() if re.search(rx, html, re.I)), "")


def extract_jsonld_jobs(html: str, page_url: str, company: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if "@graph" in node:
                stack.extend(node["@graph"] if isinstance(node["@graph"], list) else [node["@graph"]])
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                jobs.append(_jsonld_to_job(node, page_url, company))
            elif t == "ItemList":
                stack.extend(i.get("item", i) for i in node.get("itemListElement", []) if isinstance(i, dict))
    return jobs


def _jsonld_to_job(n: dict, page_url: str, company: str) -> Job:
    locs = n.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    parts = []
    for l in locs:
        a = (l or {}).get("address", {}) if isinstance(l, dict) else {}
        if isinstance(a, str):
            parts.append(a)
        elif isinstance(a, dict):
            parts.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry") if isinstance(a.get("addressCountry"), str) else ""] if x))
    remote = n.get("jobLocationType") == "TELECOMMUTE"
    org = n.get("hiringOrganization") or {}
    sal = n.get("baseSalary") or {}
    salary = ""
    if isinstance(sal, dict) and isinstance(sal.get("value"), dict):
        v = sal["value"]
        salary = f"{sal.get('currency', '')} {v.get('minValue', '')}-{v.get('maxValue', '')} {v.get('unitText', '')}".strip()
    ident = n.get("identifier")
    return Job(
        source="careerpage", company=(org.get("name") if isinstance(org, dict) else "") or company,
        title=html_to_text(n.get("title", "")), url=n.get("url") or page_url,
        location=" | ".join(p for p in parts if p) + (" | Remote" if remote else ""),
        description=html_to_text(n.get("description", "")), posted_at=to_iso_date(n.get("datePosted")),
        employment_type=str(n.get("employmentType", "")), salary=salary, remote=remote,
        external_id=str(ident.get("value", "")) if isinstance(ident, dict) else str(ident or n.get("url") or ""),
    )


_SUFFIX = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|llc|ltd|group|holdings|the|of|and|financial|"
                     r"international|technologies|technology|bank|insurance|health|medical center|university)\b", re.I)
WORKDAY_PODS = ["wd1", "wd5", "wd3", "wd12", "wd501", "wd503", "wd10", "wd103", "wd105", "wd108", "wd2", "wd4"]
_BAD_SITE = re.compile(r"universit|intern(?!ational)|featured|rise|campus|alumni|"
                       r"(^|[_-])(can|uk|emea|apac|india|mexico|ca)($|[_-])", re.I)


def slug_candidates(c: Company) -> list[str]:
    out = []
    host = urlparse(c.website if "://" in (c.website or "") else "https://" + (c.website or "")).netloc
    if host:
        base = host.removeprefix("www.").split(".")
        core = base[-2] if len(base) >= 2 else base[0]
        if core in ("myworkdayjobs", "icims"):
            core = base[0]
        out.append(re.sub(r"(jobs|careers)$", "", core))
        out.append(core)
    name = re.sub(r"\(.*?\)|['’.&/]", "", c.name)
    out.append(re.sub(r"[^a-z0-9]", "", name.lower()))
    stripped = _SUFFIX.sub(" ", name).split()
    if stripped:
        out.append("".join(stripped).lower())
        out.append(stripped[0].lower())
    return [s for s in dict.fromkeys(x for x in out if x and len(x) >= 2)]


def _site_score(site: str, slug: str) -> int:
    s = site.lower()
    score = 3 * ("external" in s) + 2 * ("career" in s) + 2 * (slug[:5] in s) + ("search" in s)
    return score - 4 * bool(_BAD_SITE.search(site))


async def guess_ats(c: Company, ctx: Ctx) -> tuple[str, str] | None:
    """When a website blocks crawlers, find the employer's ATS directly from name/domain-derived slugs."""
    slugs = slug_candidates(c)
    name_tokens = {t for t in re.findall(r"[a-z0-9]+", _SUFFIX.sub(" ", c.name.lower())) if len(t) > 2}

    async def workday_sites(slug: str, pod: str):
        r = await ctx.fetcher.request("GET", f"https://{slug}.{pod}.myworkdayjobs.com/robots.txt", follow_redirects=False)
        if r is None or r.status_code != 200 or "User-agent" not in r.text:
            return None
        sites = re.findall(r"myworkdayjobs\.com/([A-Za-z0-9_-]+)/siteMap\.xml", r.text) or \
            [s for s in re.findall(r"Disallow:\s*/([A-Za-z0-9_-]+)/", r.text) if s not in ("refreshFacet", "wday", "api")]
        if not sites:
            return None
        best = max(sites, key=lambda s: _site_score(s, slug))
        return f"https://{slug}.{pod}.myworkdayjobs.com/{best}"

    results = await asyncio.gather(*(workday_sites(s, p) for s in slugs[:3] for p in WORKDAY_PODS))
    for url in results:
        if url:
            return "workday", url

    phrase = " ".join(re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)|['’]|\b(inc|llc|corp|the)\b", "", c.name.lower())))
    if len(phrase) < 4:
        return None

    def norm(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"['’]", "", (text or "").lower())))

    def mentions(text: str) -> bool:
        return phrase in norm(text)

    def same_name(board_name: str) -> bool:  # board names are often "Acme" or "Acme, Inc." -> require full name
        b = norm(re.sub(r"\b(inc|llc|corp|ltd|co)\b\.?", "", board_name or ""))
        return bool(b) and (b == phrase or b.startswith(phrase + " ") or phrase.startswith(b + " ") and len(b) >= 6)

    for slug in slugs:
        meta = await ctx.fetcher.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
        if meta and same_name(meta.get("name", "")):
            return "greenhouse", slug
        wk = await ctx.fetcher.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}") \
            if slug in slugs[:2] else None  # Workable rate-limits hard: only try the two likeliest slugs
        # Workable has dormant accounts for almost any name: require live jobs tied to Illinois.
        if wk and same_name(wk.get("name", "")) and wk.get("jobs") and (
                re.search(r"chicago|illinois", wk.get("description") or "", re.I)
                or any(re.search(r"illinois|chicago|\bIL\b", f"{j.get('city')} {j.get('state')}") for j in wk["jobs"])):
            return "workable", slug
    # Lever/Ashby/BambooHR/Breezy/Recruitee carry no reliable company name: require the name in the job text.
    for slug in slugs:
        jobs = await ctx.fetcher.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=5")
        if jobs and sum(mentions(j.get("descriptionPlain", "") + j.get("additionalPlain", "")) for j in jobs) >= 2:
            return "lever", slug
        board = await ctx.fetcher.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if board and sum(mentions(j.get("descriptionPlain") or "") for j in (board.get("jobs") or [])[:5]) >= 2:
            return "ashby", slug
        rc = await ctx.fetcher.get_json(f"https://{slug}.recruitee.com/api/offers/")
        if rc and sum(mentions((o.get("description") or "") + (o.get("company_name") or "")) for o in (rc.get("offers") or [])[:5]) >= 1:
            return "recruitee", slug
    return None


async def discover(c: Company, ctx: Ctx, max_pages: int = 8) -> dict:
    """Crawl a company site. Returns {ats, ats_id, jobs, careers_url, other_ats}."""
    start = c.ats_id if c.ats == "jsonld" and c.ats_id else c.website
    if not start:
        guessed = await guess_ats(c, ctx)
        return {"jobs": [], "ats": guessed[0], "ats_id": guessed[1]} if guessed else {"jobs": []}
    if "://" not in start:
        start = "https://" + start
    root_host = urlparse(start).netloc.removeprefix("www.")
    queue, visited = [(start, 0)], set()
    result = {"ats": "", "ats_id": "", "jobs": [], "careers_url": "", "other_ats": ""}
    while queue and len(visited) < max_pages:
        queue.sort(key=lambda x: x[1])
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        got = await ctx.fetcher.get_text(url)
        if not got:
            continue
        final_url, html = got
        found = detect_ats(html) or detect_ats(final_url)
        if found:
            result["ats"], result["ats_id"] = found
            result["careers_url"] = final_url
            return result
        jobs = extract_jsonld_jobs(html, final_url, c.name)
        if jobs:
            result["jobs"].extend(jobs)
            result["careers_url"] = result["careers_url"] or final_url
        result["other_ats"] = result["other_ats"] or detect_other_ats(html)
        if depth >= 2:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(final_url, a["href"].strip())
            if not href.startswith("http"):
                continue
            label = f"{a.get_text(' ', strip=True)} {href}"
            host = urlparse(href).netloc.removeprefix("www.")
            same_site = host.endswith(root_host) or root_host.endswith(host)
            if detect_ats(href):
                queue.insert(0, (href, depth + 1))
            elif CAREER_LINK.search(label) and (same_site or "career" in host or "jobs" in host):
                queue.append((href.split("#")[0], depth + 1))
            elif depth >= 1 and same_site and re.search(r"/(job|position|opening|requisition)s?/", href, re.I):
                queue.append((href.split("#")[0], depth + 2))  # individual posting pages (JSON-LD), lowest priority
    if result["jobs"]:
        result["ats"], result["ats_id"] = "jsonld", result["careers_url"]
        return result
    guessed = await guess_ats(c, ctx)
    if guessed:
        result["ats"], result["ats_id"] = guessed
    return result
