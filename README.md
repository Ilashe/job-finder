# Illinois IT Job Finder

Deep-searches **employer career sites** and **US job databases** for IT jobs located in Illinois
(plus optional US-remote roles), then ranks every job against *your* job description or resume.

## Installation (first time only)

Requires **Python 3.10+** (check with `python --version`).

1. Open PowerShell and go to the project folder:
   ```powershell
   cd C:\Users\HomePC\Documents\Workflows\Nexucentri\job-finder
   ```
2. Install the required packages:
   ```powershell
   pip install -r requirements.txt
   ```
   This installs httpx, FastAPI, Uvicorn, PyYAML and BeautifulSoup. You only need to do it once per computer
   (run it again if `requirements.txt` changes).
3. *(Optional)* Copy `.env.example` to `.env` and add your API keys (People Data Labs, Adzuna,
   CareerOneStop, USAJOBS). The program works without them.

## Starting the program

```powershell
cd C:\Users\HomePC\Documents\Workflows\Nexucentri\job-finder
python -m jobfinder serve          # then open http://127.0.0.1:8000 in your browser
```

Keep the PowerShell window open while you use the dashboard; press **Ctrl + C** in it to stop.
If port 8000 is busy, use `python -m jobfinder serve --port 8765`.

Paste your job description/resume, add must-have keywords, click **Search Illinois IT jobs**.
A full crawl takes ~10–15 minutes; results stream in while it runs.

Terminal-only alternative:

```powershell
python -m jobfinder search --profile examples\profile-cloud-engineer.txt --keywords "aws,kubernetes" --max-age 30 --csv jobs.csv
```

## Where it searches

| Source | What it is | Setup |
|---|---|---|
| **Employer career sites** (110 Illinois employers seeded) | Reads the same live job feeds the companies' own career pages use: Workday, Greenhouse, Lever, Ashby, SmartRecruiters, iCIMS, plus schema.org JobPosting data on any other careers page | none |
| **USAJOBS** | Every federal IT job in Illinois (OPM series 2210, 1550, 0854…) | free key → `.env` |
| **CareerOneStop / National Labor Exchange** | US Dept. of Labor job bank (includes Illinois JobLink postings) | free key → `.env` |
| **Adzuna** | Aggregates thousands of US job boards, IT category, Illinois | free key → `.env` |

Copy `.env.example` to `.env` to enable the three databases.

### How the deep crawl works
1. **Auto-discovery** – add any company with just its website. The crawler finds its careers page,
   detects the hiring system behind it, and remembers it. If the main site blocks bots, it derives
   likely IDs from the company name/domain and verifies them against the hiring platforms directly
   (e.g. Workday tenants are confirmed via their published `robots.txt` sitemaps).
2. **Server-side Illinois filtering** – on Workday it applies the employer's own "Illinois" location
   filter, so even companies with 2,000+ global openings are covered completely.
3. **Filtering** – Illinois location matcher (150+ IL cities, handles `Springfield, MO` vs `Springfield, IL`),
   IT classifier (14 categories: software, data/AI, cloud/DevOps, cybersecurity, networking, help desk,
   ERP/CRM, QA, IT leadership…), seniority detection, keyword exclusion, posting age.
4. **Ranking** – each job gets a 0–100 match score: TF-IDF text similarity to your profile (45%),
   skill overlap from a 100+ skill lexicon (35%), title similarity (20%), plus keyword boosts.
   Each card shows the skills you match ✓ and the gaps.
5. **Tracking** – jobs are stored in SQLite (`data/jobs.db`); mark Saved / Applied / Hidden, see
   what's new since the last run, export CSV. Duplicates across sources are merged.

## Small and midsize employers (≤ 300 employees) — the default

Searches only include employers whose headcount is **300 or fewer** (change it with the *Company size*
dropdown — 100 / 300 / 500 / any — or `--max-employees N`; `0` = any size). Employers of unknown size are skipped unless you tick
*Include employers of unknown size* (`--include-unknown-size`). USAJOBS is skipped under a size limit
(federal agencies are large), and Adzuna/CareerOneStop jobs are kept only from employers known to be within the limit.

Headcounts come from:
- **Built In Chicago "Best Places to Work" 2023–2026** (startup 0–100 and midsize categories) — 120 seeded employers
  with ≤300 staff, each with its published headcount and the year (older counts may have grown).
- **People Data Labs** — the way to add hundreds more Illinois employers of the size you want:
  1. Get a free API key at https://www.peopledatalabs.com/signup
  2. Copy `.env.example` to `.env` and set `PDL_API_KEY=...`
  3. Dashboard → **Companies** → *Find more Illinois employers by size* (choose 5–300, how many) — or
     `python -m jobfinder find-companies --min-employees 5 --max-employees 300 --count 200`
  4. *Look up unknown sizes* / `python -m jobfinder enrich-sizes` fills in headcounts for companies you added.
  Each company returned uses 1 credit of your PDL plan; career sites are discovered on the next search.
- **You**: type the number in the Companies tab, `python -m jobfinder set-size "Acme" 40`, or an `employees` CSV column.

Small companies also use lighter hiring systems; Workable, BambooHR, Breezy and Recruitee are supported.

## Adding employers
- Dashboard → **Companies** tab → name + website (or paste a `name,website` CSV).
- Or `python -m jobfinder import my_companies.csv`, then `python -m jobfinder discover`.
- `python -m jobfinder save-companies` writes discovered career systems back to `data/companies.yaml`.

Good sources of Illinois employer lists: Crain's Chicago "largest employers", Built In Chicago,
Illinois Dept. of Commerce, Chicago chamber directories.

## Limits (by design)
- **BBB is not a source**: the Better Business Bureau holds no job listings, and its terms forbid scraping.
  Use business directories only to *find employers* to add via the Companies tab.
- The tool **respects robots.txt**. Some employers (e.g. State Farm, Aon, Exelon on iCIMS) disallow
  automated access; they're reported in the log so you can check them manually, and their postings
  usually also arrive via Adzuna/CareerOneStop.
- Sites on Taleo, SuccessFactors, or ADP (e.g. United Airlines, Grainger) have no public feed; same
  handling as above.
- LinkedIn/Indeed are not scraped (their terms prohibit it); Adzuna covers much of the same market legally.
