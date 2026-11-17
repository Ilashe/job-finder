"""Illinois location matching, remote detection, IT-job classification, seniority + skill extraction."""
from __future__ import annotations

import re

# ---------------------------------------------------------------- Illinois location
# Cities whose name is (practically) unique to Illinois -> match even without ", IL".
IL_CITIES_UNIQUE = [
    "chicago", "naperville", "joliet", "schaumburg", "evanston", "oak brook", "oakbrook", "oakbrook terrace",
    "downers grove", "lisle", "deerfield", "northbrook", "lake forest", "itasca", "rosemont",
    "hoffman estates", "arlington heights", "lincolnshire", "mettawa", "rock island", "carbondale", "skokie",
    "vernon hills", "libertyville", "waukegan", "elk grove village", "warrenville", "bolingbrook",
    "romeoville", "lombard", "glenview", "des plaines", "park ridge", "orland park", "tinley park",
    "mokena", "lemont", "urbana", "champaign", "edwardsville", "kankakee", "dekalb", "de kalb",
    "crystal lake", "st. charles il", "riverwoods", "lake zurich", "buffalo grove", "mount prospect",
    "mt. prospect", "palatine", "rolling meadows", "bannockburn", "abbott park", "north chicago",
    "westmont", "oak lawn", "berwyn", "wilmette", "winnetka", "highland park", "gurnee", "mundelein",
    "algonquin", "huntley", "st. charles, illinois", "bloomingdale", "carol stream", "glendale heights",
    "addison", "wood dale", "bensenville", "franklin park", "melrose park", "hinsdale", "burr ridge",
    "willowbrook", "darien", "woodridge", "plainfield", "oswego", "yorkville", "montgomery il",
    "matteson", "homewood", "harvey", "calumet city", "chicago heights", "east moline", "peoria heights",
    "pekin", "east peoria", "belvidere", "loves park", "machesney park", "sterling il", "ottawa il",
    "la salle", "lasalle", "peru il", "streator", "pontiac il", "effingham", "mattoon", "charleston il",
    "mount vernon il", "marion il", "alton il", "collinsville", "granite city", "fairview heights",
    "schiller park", "northlake", "niles il", "morton grove", "lincolnwood", "glencoe", "northfield il",
    "fermilab", "argonne", "moline", "bloomington-normal", "normal, il",
]
# Cities that also exist in other states -> only match with IL/Illinois context.
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}
OTHER_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "florida", "georgia", "hawaii", "idaho", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
    "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota",
    "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming",
]
_IL_EXPLICIT = re.compile(r"(?i:\billinois\b)|(?:,|-|\s)\s*IL\b(?!\w)|\bIL\s*,?\s*(?:US|USA|United States)\b|\bUS-IL\b|\bUSA?\s*-\s*IL\b")
_CITY_RE = re.compile(r"\b(" + "|".join(re.escape(c) for c in sorted(IL_CITIES_UNIQUE, key=len, reverse=True)) + r")\b", re.I)
_STATE_CODE_RE = re.compile(r"(?:,|-|\s)\s*([A-Z]{2})\b(?:\s*,|\s*$|\s+\d{5}|\s*\)|\s*-|\s*\|)")
_OTHER_STATE_RE = re.compile(r"\b(" + "|".join(OTHER_STATE_NAMES) + r")\b", re.I)
_SPLIT_RE = re.compile(r"\s*(?:\||;|\n|\bor\b|/)\s*")


def _part_is_illinois(part: str) -> bool:
    if _IL_EXPLICIT.search(part):
        return True
    if _CITY_RE.search(part):
        codes = {c for c in _STATE_CODE_RE.findall(part) if c in STATE_CODES}
        if codes - {"IL"}:
            return False
        low = part.lower()
        if _OTHER_STATE_RE.search(low) and "chicago" not in low:
            return False
        return True
    return False


def is_illinois(location: str) -> bool:
    if not location:
        return False
    if _part_is_illinois(location) and not _SPLIT_RE.search(location):
        return True
    return any(_part_is_illinois(p) for p in _SPLIT_RE.split(location) if p) or _part_is_illinois(location)


_NON_US = re.compile(
    r"\b(canada|uk|united kingdom|england|london|india|bangalore|bengaluru|hyderabad|pune|germany|berlin|"
    r"france|paris|ireland|dublin|poland|mexico|brazil|philippines|singapore|australia|spain|netherlands|"
    r"emea|apac|latam|europe|toronto|vancouver|israel|argentina|colombia|costa rica|romania|ukraine|japan)\b",
    re.I,
)


def is_remote_us(location: str, flagged_remote: bool = False, description: str = "") -> bool:
    text = location or ""
    remote = flagged_remote or bool(re.search(r"\b(remote|work from home|wfh|anywhere|virtual|telework)\b", text, re.I))
    if not remote:
        return False
    if _NON_US.search(text) and not re.search(r"\b(us|usa|united states)\b", text, re.I):
        return False
    return True


# ---------------------------------------------------------------- IT classification
IT_TITLE_RULES: list[tuple[str, re.Pattern]] = [(name, re.compile(rx, re.I)) for name, rx in [
    ("Cybersecurity", r"\b(cyber|infosec|information security|security (engineer|analyst|architect|operations|specialist|consultant|administrator|researcher)|soc (analyst|engineer)|penetration|pen ?test|ethical hack|identity (and|&) access|\biam\b|grc analyst|vulnerability|threat (intel|hunt)|incident response|appsec|application security|cloud security|network security|ciso|devsecops)\b"),
    ("Cloud / DevOps / SRE", r"\b(devops|sre|site reliability|cloud (engineer|architect|developer|administrator|operations|consultant|specialist)|aws|azure|gcp|kubernetes|platform engineer|infrastructure engineer|build engineer|release engineer|mlops|finops)\b"),
    ("Data & AI", r"\b(data scien|machine learning|deep learning|\bml\b|\bai\b|artificial intelligence|\bnlp\b|computer vision|\bllm\b|generative ai|(data|analytics|bi|business intelligence|etl|big data|reporting) (engineer|analyst|architect|developer|specialist|manager|lead|modeler|scientist|consultant)|analytics engineer|data governance|data warehouse|power bi|tableau)\b"),
    ("Software Engineering", r"\b(software|full[- ]?stack|front[- ]?end|back[- ]?end|web|mobile|ios|android|java|python|\.net|dotnet|c#|c\+\+|golang|ruby|php|javascript|typescript|react|angular|node(\.js)?|application|applications|game|integration|api) (engineer|developer|programmer|architect|engineering)|\b(developer|programmer|swe|sde|software engineer|software development|coder)\b"),
    ("Database", r"\b(database|\bdba\b|sql (developer|server|administrator|analyst)|oracle (dba|developer)|data ?base)\b"),
    ("QA & Testing", r"\b(\bqa\b|quality assurance (engineer|analyst|tester|automation)|(software )?test(ing)? (engineer|analyst|automation|lead)|sdet|automation tester|software tester|test automation)\b"),
    ("Network & Systems", r"\b(network (engineer|administrator|analyst|architect|technician|specialist)|systems? (administrator|engineer|analyst|integrator)|sysadmin|linux|unix|windows (server|administrator|engineer)|vmware|citrix|active directory|storage (engineer|administrator)|telecom(munications)? (engineer|analyst)|voip|wireless (engineer|network)|infrastructure (analyst|administrator|specialist|architect)|noc (technician|engineer|analyst)|m365|office 365|intune)\b"),
    ("IT Support & Operations", r"\b(help ?desk|service ?desk|desktop support|it support|technical support (engineer|specialist|analyst)|it technician|support technician|end[- ]user|field (it|service) technician|it specialist|it analyst|it coordinator|it associate|it operations|computer (technician|support)|it administrator|information technology|\bit\b (intern|generalist|consultant|auditor|asset))\b"),
    ("Enterprise Apps (ERP/CRM)", r"\b(sap|oracle (ebs|cloud|fusion|hcm|financials)|workday (analyst|consultant|integration|hcm|financials|developer|administrator)|salesforce|servicenow|dynamics 365|netsuite|peoplesoft|epic (analyst|application|certified)|\berp\b|crm (analyst|developer|administrator)|sharepoint|guidewire|mainframe|cobol)\b"),
    ("Architecture", r"\b(solutions? architect|enterprise architect|technical architect|it architect|technology architect|software architect)\b"),
    ("IT Leadership & PM", r"\b(\bcio\b|\bcto\b|chief (information|technology|digital|data) officer|it (director|manager|project manager|program manager|business partner|lead)|director,? (of )?(it|information technology|engineering|technology|infrastructure)|(vp|vice president),? (of )?(engineering|technology|it|information technology)|engineering manager|technical (program|project|product) manager|technology (manager|lead|consultant)|scrum master|agile coach|product owner|release manager)\b"),
    ("UX / UI", r"\b(\bux\b|\bui\b|user experience|product designer|interaction designer|ui/ux|ux/ui)\b"),
    ("Hardware / Embedded", r"\b(firmware|embedded (software|systems|engineer)|fpga|hardware (engineer|design)|asic)\b"),
    ("IT Audit & Compliance", r"\b(it audit|it risk|it compliance|technology risk|it governance|sox it)\b"),
]]

EXCLUDE_TITLE = re.compile(
    r"\b(security (officer|guard|patrol)|armed|unarmed|nurse|\brn\b|\blpn\b|\bcna\b|driver|\bcdl\b|warehouse|forklift|"
    r"cashier|barista|cook|chef|janitor|custodian|housekeep|mechanic|hvac|electrician|plumber|welder|teacher|"
    r"physician|pharmac|dental|therapist|medical assistant|phlebotom|caregiver|"
    r"mechanical engineer|civil engineer|chemical engineer|structural engineer|construction|"
    r"data entry|retail|store (manager|associate)|sales associate|loan officer|insurance agent|"
    r"maintenance (technician|tech|mechanic)|lab technician|laboratory|veterinar|surgical)\b",
    re.I,
)
GENERIC_TITLE = re.compile(
    r"\b(engineer|developer|analyst|architect|consultant|administrator|specialist|scientist|programmer|"
    r"technologist|technician|manager|lead|associate|intern|designer|product manager|business analyst)\b",
    re.I,
)

# ---------------------------------------------------------------- skills lexicon
SKILLS: dict[str, str] = {
    "Python": r"python", "Java": r"java(?!script)", "JavaScript": r"javascript|\bjs\b", "TypeScript": r"typescript",
    "C#": r"c#|c sharp", "C++": r"c\+\+", "Go": r"golang|\bgo\b(?= (language|developer|programming))",
    "Rust": r"\brust\b", "Ruby": r"\bruby\b", "PHP": r"\bphp\b", "Scala": r"\bscala\b", "Kotlin": r"kotlin",
    "Swift": r"\bswift\b", "R": r"\bR\b(?= (programming|language|studio))|\brstudio\b", "SQL": r"\bsql\b",
    "NoSQL": r"nosql", "PostgreSQL": r"postgres", "MySQL": r"mysql", "SQL Server": r"sql server|mssql|t-sql",
    "Oracle DB": r"oracle (database|db|pl/sql)|pl/sql", "MongoDB": r"mongo", "Redis": r"redis",
    "Snowflake": r"snowflake", "Databricks": r"databricks", "Spark": r"\bspark\b|pyspark", "Hadoop": r"hadoop",
    "Kafka": r"kafka", "Airflow": r"airflow", "dbt": r"\bdbt\b", "Tableau": r"tableau", "Power BI": r"power ?bi",
    "Looker": r"looker", "Excel/VBA": r"\bvba\b|advanced excel", "AWS": r"\baws\b|amazon web services",
    "Azure": r"azure", "GCP": r"\bgcp\b|google cloud", "Docker": r"docker|container", "Kubernetes": r"kubernetes|\bk8s\b|\beks\b|\baks\b|\bgke\b",
    "Terraform": r"terraform", "Ansible": r"ansible", "Jenkins": r"jenkins", "GitHub Actions": r"github actions",
    "CI/CD": r"ci/cd|continuous integration|continuous delivery|continuous deployment", "Git": r"\bgit\b|github|gitlab|bitbucket",
    "Linux": r"linux|\bunix\b|rhel|ubuntu", "Windows Server": r"windows server", "Active Directory": r"active directory|entra id|azure ad",
    "VMware": r"vmware|vsphere|esxi", "Cisco": r"cisco|ccna|ccnp", "Networking": r"tcp/ip|\bdns\b|dhcp|\bvlan|routing|switching|\bbgp\b|ospf|\bsd-wan",
    "Firewalls": r"firewall|palo alto|fortinet|fortigate", "SIEM": r"\bsiem\b|splunk|qradar|sentinel", "EDR": r"\bedr\b|crowdstrike|defender for endpoint|carbon black",
    "IAM": r"\biam\b|okta|sailpoint|cyberark|identity and access|\bsso\b|saml|oauth", "React": r"react", "Angular": r"angular",
    "Vue": r"vue(\.js)?\b", "Node.js": r"node\.?js|\bnode\b", ".NET": r"\.net|dotnet|asp\.net", "Spring": r"spring boot|\bspring\b",
    "Django": r"django", "Flask": r"flask", "FastAPI": r"fastapi", "REST APIs": r"\brest(ful)?\b|\bapis?\b", "GraphQL": r"graphql",
    "Microservices": r"microservice", "Machine Learning": r"machine learning|\bml\b", "Deep Learning": r"deep learning|neural net",
    "NLP": r"\bnlp\b|natural language", "LLMs / GenAI": r"\bllms?\b|generative ai|genai|large language model|rag\b|prompt engineering",
    "PyTorch": r"pytorch", "TensorFlow": r"tensorflow|keras", "scikit-learn": r"scikit|sklearn", "Pandas": r"pandas|numpy",
    "ETL": r"\betl\b|\belt\b|data pipeline", "Data Warehousing": r"data warehouse|data lake|lakehouse|dimensional model",
    "Salesforce": r"salesforce|apex|lightning web", "ServiceNow": r"servicenow", "SAP": r"\bsap\b|s/4hana|abap", "Workday": r"workday",
    "Jira": r"jira|confluence", "Agile/Scrum": r"agile|scrum|kanban|safe\b", "ITIL": r"\bitil\b", "Microsoft 365": r"office 365|microsoft 365|\bm365\b|exchange online",
    "Intune/SCCM": r"intune|sccm|mecm|endpoint manager", "PowerShell": r"powershell", "Bash/Shell": r"\bbash\b|shell script",
    "HTML/CSS": r"\bhtml|\bcss\b", "Selenium": r"selenium", "Cypress/Playwright": r"cypress|playwright", "Test Automation": r"test automation|automated test",
    "Penetration Testing": r"penetration test|pen test|ethical hack|burp", "NIST/ISO": r"\bnist\b|iso 27001|soc 2|hipaa|pci", "CISSP/Security+": r"cissp|security\+|cism|cisa\b",
    "Mainframe/COBOL": r"mainframe|cobol|\bjcl\b|db2", "SharePoint": r"sharepoint", "Dynamics 365": r"dynamics 365|dynamics crm",
    "BigQuery": r"bigquery", "Redshift": r"redshift", "Elasticsearch": r"elasticsearch|opensearch|\belk\b",
    "Observability": r"prometheus|grafana|datadog|new relic|dynatrace|splunk observability", "Epic (EHR)": r"\bepic\b (systems|certif|analyst|ehr)",
    "Help Desk": r"help ?desk|service desk|ticketing|troubleshoot", "Hardware Support": r"hardware (support|troubleshooting)|imaging|printers|peripheral",
}
_SKILL_RES = {name: re.compile(rx, re.I) for name, rx in SKILLS.items()}


def extract_skills(text: str) -> list[str]:
    if not text:
        return []
    return [name for name, rx in _SKILL_RES.items() if rx.search(text)]


def classify_it(title: str, description: str = "", department: str = "") -> tuple[str, float]:
    """Return (category, confidence). Empty category => not an IT job."""
    t = title or ""
    if EXCLUDE_TITLE.search(t):
        return "", 0.0
    for name, rx in IT_TITLE_RULES:
        if rx.search(t):
            return name, 0.95
    dept = department or ""
    if re.search(r"\b(it|information technology|technology|engineering|software|data|digital|security|infrastructure)\b", dept, re.I) and GENERIC_TITLE.search(t):
        return "IT (by department)", 0.75
    if GENERIC_TITLE.search(t) and description:
        n = len(extract_skills(description))
        if n >= 6:
            return "IT-adjacent", 0.6
    return "", 0.0


def title_maybe_it(title: str) -> bool:
    """Cheap pre-filter before fetching job details."""
    if EXCLUDE_TITLE.search(title or ""):
        return False
    return bool(classify_it(title)[0]) or bool(GENERIC_TITLE.search(title or ""))


LEVELS = [
    ("Intern", r"\bintern(ship)?\b|co-?op\b"),
    ("Executive", r"\b(chief|cio|cto|ciso|vp|vice president|head of)\b"),
    ("Director", r"\bdirector\b"),
    ("Manager", r"\bmanager\b|\bmgr\b"),
    ("Lead / Principal", r"\b(lead|principal|staff|distinguished|architect)\b"),
    ("Senior", r"\b(senior|sr\.?|iii|iv|level 3|l3)\b"),
    ("Entry / Junior", r"\b(junior|jr\.?|entry|associate|new grad|graduate|apprentice|i)\b(?!\w)"),
    ("Mid", r"\b(ii|mid|level 2)\b"),
]
_LEVEL_RES = [(n, re.compile(rx, re.I)) for n, rx in LEVELS]


def detect_level(title: str) -> str:
    for name, rx in _LEVEL_RES:
        if rx.search(title or ""):
            return name
    return "Mid"
