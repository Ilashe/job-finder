from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


@dataclass
class Company:
    name: str
    website: str = ""
    ats: str = ""          # greenhouse | lever | ashby | smartrecruiters | workday | jsonld | ""
    ats_id: str = ""       # board token / slug / workday careers URL / careers page URL
    hq: str = ""
    notes: str = ""
    employees: int | None = None   # headcount; None = unknown
    size_source: str = ""          # where the headcount came from
    id: int | None = None


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: str | None = None
    department: str = ""
    employment_type: str = ""
    salary: str = ""
    remote: bool = False
    external_id: str = ""
    # computed by the pipeline
    is_illinois: bool = False
    it_category: str = ""
    it_confidence: float = 0.0
    level: str = ""
    skills: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        key = f"{self.source}|{self.external_id or self.url}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    @property
    def fingerprint(self) -> str:
        """Cross-source duplicate key: same company + title + first location."""
        loc = _norm(self.location.split("|")[0])[:40]
        return hashlib.sha1(f"{_norm(self.company)}|{_norm(self.title)}|{loc}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["fingerprint"] = self.fingerprint
        return d
