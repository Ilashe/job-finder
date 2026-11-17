from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..filters import is_illinois, is_remote_us, title_maybe_it
from ..http import Fetcher

# Search terms used against keyword-search APIs (Workday, aggregators). User keywords are appended.
IT_QUERIES = ["software", "developer", "data", "IT", "information technology", "security", "cloud", "network",
              "systems", "engineer", "analyst", "devops", "database", "support"]


@dataclass
class Ctx:
    fetcher: Fetcher
    include_remote: bool = True
    keywords: list[str] = field(default_factory=list)
    log: Callable[[str], None] = print

    @property
    def queries(self) -> list[str]:
        extra = [k for k in self.keywords if k.lower() not in {q.lower() for q in IT_QUERIES}]
        return extra + IT_QUERIES

    def location_ok(self, location: str, remote: bool = False) -> bool:
        if is_illinois(location):
            return True
        return self.include_remote and is_remote_us(location, remote)

    def prefilter(self, title: str, location: str = "", remote: bool = False, location_unknown: bool = False) -> bool:
        """Cheap check before fetching job details."""
        if not title_maybe_it(title):
            return False
        return location_unknown or self.location_ok(location, remote)
