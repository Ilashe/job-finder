from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    if "&lt;" in s and "<" not in s:  # Greenhouse returns escaped HTML
        s = _html.unescape(s)
    if "<" in s:
        s = BeautifulSoup(s, "html.parser").get_text("\n")
    s = _html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def to_iso_date(value) -> str | None:
    """Accepts epoch ms/s, ISO strings, or Workday-style 'Posted 3 Days Ago'."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        s = str(value).strip()
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            return m.group(1)
        low = s.lower()
        today = datetime.now(timezone.utc).date()
        if "today" in low:
            return today.isoformat()
        if "yesterday" in low:
            return (today.fromordinal(today.toordinal() - 1)).isoformat()
        m = re.search(r"(\d+)\+?\s*day", low)
        if m:
            return today.fromordinal(today.toordinal() - int(m.group(1))).isoformat()
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
    except (ValueError, OSError, OverflowError):
        return None
    return None
