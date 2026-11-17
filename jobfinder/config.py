"""Configuration loaded from environment variables and an optional .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path(os.getenv("JOBFINDER_DB", str(DATA_DIR / "jobs.db")))
    companies_file: Path = DATA_DIR / "companies.yaml"
    user_agent: str = os.getenv(
        "JOBFINDER_USER_AGENT",
        "IllinoisITJobFinder/1.0 (personal job-search tool; respects robots.txt)",
    )
    # USAJOBS (federal jobs) - free key: https://developer.usajobs.gov/APIRequest/Index
    usajobs_key: str = os.getenv("USAJOBS_API_KEY", "")
    usajobs_email: str = os.getenv("USAJOBS_EMAIL", "")
    # Adzuna (aggregates US job boards) - free key: https://developer.adzuna.com/
    adzuna_id: str = os.getenv("ADZUNA_APP_ID", "")
    adzuna_key: str = os.getenv("ADZUNA_APP_KEY", "")
    # CareerOneStop / National Labor Exchange (US Dept. of Labor) - https://www.careeronestop.org/Developers/WebAPI/registration.aspx
    cos_user_id: str = os.getenv("CAREERONESTOP_USER_ID", "")
    cos_token: str = os.getenv("CAREERONESTOP_TOKEN", "")
    # People Data Labs - company headcounts + discovery of small Illinois employers (free tier)
    pdl_key: str = os.getenv("PDL_API_KEY", "")
    per_host_concurrency: int = int(os.getenv("JOBFINDER_HOST_CONCURRENCY", "4"))
    global_concurrency: int = int(os.getenv("JOBFINDER_CONCURRENCY", "24"))
    workday_max_pages: int = int(os.getenv("JOBFINDER_WORKDAY_MAX_PAGES", "6"))


settings = Settings()
