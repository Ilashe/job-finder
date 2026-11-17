"""Rank jobs against the user's job description / resume.

Score (0-100) = 45% TF-IDF text similarity + 35% skill overlap + 20% title similarity,
plus boosts for must-have keywords. Explains matched and missing skills per job.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .filters import extract_skills

STOP = set("""a an and are as at be by for from has have in is it its of on or that the to was were will with you your
we our us they their this these those i me my not but if then than so such can may should would could all any
each other into over under about above after again more most some no nor only own same too very s t just don
role job work team teams position candidate candidates company including include ability strong experience
years year plus preferred required requirements responsibilities qualifications skills knowledge using use
new well also within across etc e g ie per must able""".split())
_TOKEN = re.compile(r"[a-z][a-z0-9+#.\-]*[a-z0-9+#]|[a-z]")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 1]


class Ranker:
    def __init__(self, profile: str, keywords: list[str] | None = None, title_hint: str = ""):
        self.profile = profile or ""
        self.keywords = [k.strip().lower() for k in (keywords or []) if k.strip()]
        self.profile_skills = set(extract_skills(self.profile + " " + " ".join(self.keywords)))
        first_line = next((l for l in self.profile.splitlines() if l.strip()), "")
        self.title_tokens = set(tokenize(title_hint or first_line[:120])) | set(tokenize(" ".join(self.keywords)))

    def rank(self, jobs: list[dict]) -> list[dict]:
        if not jobs:
            return []
        docs = [tokenize(f"{j['title']} {j['title']} {j['title']} {j.get('description', '')[:6000]}") for j in jobs]
        ptoks = tokenize(self.profile + " " + " ".join(self.keywords))
        df = Counter()
        for d in docs + [ptoks]:
            df.update(set(d))
        n = len(docs) + 1
        idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

        def vec(toks):
            tf = Counter(toks)
            v = {t: (1 + math.log(c)) * idf.get(t, 0) for t, c in tf.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            return {t: x / norm for t, x in v.items()}

        pv = vec(ptoks) if ptoks else {}
        sims = []
        for d in docs:
            dv = vec(d)
            sims.append(sum(w * dv.get(t, 0) for t, w in pv.items()) if pv else 0.0)
        max_sim = max(max(sims), 0.15)

        out = []
        for j, sim in zip(jobs, sims):
            job_skills = set(j.get("skills") or extract_skills(f"{j['title']} {j.get('description', '')}"))
            matched = sorted(job_skills & self.profile_skills)
            missing = sorted(job_skills - self.profile_skills)
            if job_skills and self.profile_skills:
                coverage = len(matched) / len(job_skills)          # how qualified you are
                relevance = len(matched) / len(self.profile_skills)  # how much of your stack it uses
                skill_score = 0.6 * coverage + 0.4 * min(1.0, relevance * 2)
            else:
                skill_score = 0.0
            ttoks = set(tokenize(j["title"]))
            title_score = len(ttoks & self.title_tokens) / max(1, len(ttoks)) if self.title_tokens else 0.0
            score = 100 * (0.45 * (sim / max_sim) + 0.35 * skill_score + 0.20 * min(1.0, title_score * 1.5))
            low_title, low_desc = j["title"].lower(), (j.get("description") or "").lower()
            for k in self.keywords:
                if k in low_title:
                    score += 6
                elif k in low_desc:
                    score += 2
            r = dict(j)
            r.update(score=round(min(score, 100.0), 1), matched_skills=matched, missing_skills=missing[:12])
            out.append(r)
        out.sort(key=lambda r: (r["score"], r.get("posted_at") or ""), reverse=True)
        return out
