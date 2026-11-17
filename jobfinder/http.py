"""Polite async HTTP fetcher: per-host concurrency, retries with backoff, robots.txt checks."""
from __future__ import annotations

import asyncio
import random
from urllib import robotparser
from urllib.parse import urlparse

import httpx

from .config import settings


class Fetcher:
    def __init__(self, user_agent: str | None = None, timeout: float = 25.0, max_retries: int = 3):
        self.user_agent = user_agent or settings.user_agent
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=settings.global_concurrency * 2),
        )
        self.max_retries = max_retries
        self._global = asyncio.Semaphore(settings.global_concurrency)
        self._hosts: dict[str, asyncio.Semaphore] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()

    # Hosts with strict rate limits get fewer parallel requests.
    HOST_LIMITS = {"apply.workable.com": 1, "api.peopledatalabs.com": 2}

    def _host_sem(self, url: str) -> asyncio.Semaphore:
        host = urlparse(url).netloc
        if host not in self._hosts:
            self._hosts[host] = asyncio.Semaphore(self.HOST_LIMITS.get(host, settings.per_host_concurrency))
        return self._hosts[host]

    async def allowed(self, url: str) -> bool:
        """robots.txt check, used for generic website crawling (not for public JSON APIs)."""
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        async with self._robots_lock:
            if base not in self._robots:
                rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
                try:
                    r = await self.client.get(base + "/robots.txt", timeout=10)
                    if r.status_code == 200:
                        rp.parse(r.text.splitlines())
                    else:
                        rp = None  # no robots.txt -> allowed
                except httpx.HTTPError:
                    rp = None
                self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    async def request(self, method: str, url: str, *, respect_robots: bool = False, **kw) -> httpx.Response | None:
        if respect_robots and not await self.allowed(url):
            return None
        async with self._global, self._host_sem(url):
            for attempt in range(self.max_retries + 1):
                try:
                    r = await self.client.request(method, url, **kw)
                except (httpx.HTTPError, OSError, ValueError):  # incl. raw ssl.SSLError from some CDNs
                    if attempt == self.max_retries:
                        return None
                    await asyncio.sleep(1.5 ** attempt + random.random())
                    continue
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    retry_after = r.headers.get("Retry-After", "")
                    delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt + random.random()
                    await asyncio.sleep(min(delay, 30))
                    continue
                return r
        return None

    async def get_json(self, url: str, **kw):
        r = await self.request("GET", url, **kw)
        if r is None or r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def post_json(self, url: str, payload: dict, **kw):
        r = await self.request("POST", url, json=payload, **kw)
        if r is None or r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def get_text(self, url: str, respect_robots: bool = True, **kw) -> tuple[str, str] | None:
        """Returns (final_url, html) or None."""
        r = await self.request("GET", url, respect_robots=respect_robots, **kw)
        if r is None or r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype and ctype:
            return None
        return str(r.url), r.text

    async def aclose(self):
        await self.client.aclose()
