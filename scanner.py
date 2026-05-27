"""
scanner.py — FailSignal v2
Adds: tariff/geo risk layer, ESG signals, lightweight scan for bulk scorecard,
      sector supplier discovery
"""
import http.client, ssl, json, re
from datetime import datetime

GROQ_MODEL = "llama-3.3-70b-versatile"

PATTERN_TAGS_DESC = """
c_suite_exodus: 3+ VP-level departures (CEO/CFO/COO/CTO) within 12 months
going_concern_language: Filing or news explicitly mentions "going concern" or "substantial doubt about ability to continue"
debt_language_spike: News about debt restructuring, covenant waiver, credit default, forbearance, lender negotiations
vendor_payment_failure: Vendors/suppliers demanding cash-on-delivery, refusing to ship, or lenders issuing default notices
insider_stock_sales: CEO or CFO sold significant personal stock shortly before bad news
fraud_transparency: SEC investigation, fraud allegations, missing audits, regulatory order to halt misleading claims
employee_sentiment_collapse: Employee reports of unpaid wages, mass layoffs, benefit cuts, payroll delays
merger_rescue_blocked: Planned acquisition or merger blocked by regulators, eliminating last strategic option
asset_strip_related_party: Key assets sold to related-party entities owned by executives or controlling shareholders
operational_shortfall: Production, delivery, or revenue misses stated targets by 50%+ in a single period
"""

RISK_LOGIC = "CRITICAL = going_concern OR fraud detected OR 5+ tags | HIGH = 3-4 | MEDIUM = 1-2 | LOW = 0"

# Known high-tariff exposure countries as of 2025 — used as a fallback signal
TARIFF_HIGH_EXPOSURE = ["china", "prc", "hong kong"]
TARIFF_MEDIUM_EXPOSURE = ["vietnam", "taiwan", "india", "bangladesh", "cambodia", "myanmar"]
TARIFF_LOW_EXPOSURE = ["mexico", "canada", "germany", "uk", "japan", "south korea", "france", "netherlands"]


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _serper_search(query: str, api_key: str, num: int = 8) -> list:
    try:
        body = json.dumps({"q": query, "num": num}).encode("utf-8")
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection("google.serper.dev", context=ctx)
        conn.request("POST", "/search", body, {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status != 200:
            return []
        return [
            {"title": r.get("title",""), "snippet": r.get("snippet",""),
             "link": r.get("link",""), "date": r.get("date","N/A")}
            for r in json.loads(raw).get("organic", []) if r.get("snippet")
        ]
    except Exception:
        return []


def _groq_call(system: str, user: str, api_key: str, max_tokens: int = 1400) -> str:
    try:
        body = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role":"system","content":system},
                         {"role":"user","content":user}],
            "max_tokens": max_tokens, "temperature": 0.1,
        }, ensure_ascii=True).encode("ascii")
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection("api.groq.com", context=ctx)
        conn.request("POST", "/openai/v1/chat/completions", body, {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status != 200:
            return ""
        return json.loads(raw)["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return {}


def _parse_json_array(text: str) -> list:
    text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except Exception:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return []


# ── Full scan (6 queries) ─────────────────────────────────────────────────────
def run_scan(company_name: str, serper_key: str, groq_key: str,
             library_context: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    queries = [
        # 1-4: financial distress signals
        f'"{company_name}" "going concern" OR restructuring OR "covenant waiver" OR default OR bankruptcy',
        f'"{company_name}" CEO OR CFO OR COO resigned OR departed OR "stepped down" OR fired',
        f'"{company_name}" vendors OR suppliers refused OR "cash on delivery" OR "payment terms" OR default',
        f'"{company_name}" layoffs OR "job cuts" OR "financial distress" OR fraud OR investigation',
        # 5: geo/tariff exposure
        f'"{company_name}" manufacturing OR factory OR headquarters location country operations',
        # 6: ESG violations
        f'"{company_name}" "labor violation" OR "environmental fine" OR sanctions OR "human rights" OR OSHA OR EPA OR "SEC investigation" OR "compliance violation"',
    ]

    snippets, sources = [], []
    for q in queries:
        for r in _serper_search(q, serper_key, num=5):
            snippets.append(r)
            sources.append(r["link"])

    if len(snippets) < 2:
        return _empty_result(company_name, today, sources)

    snippets_text = "\n\n".join([
        f"[{i+1}] {s['title']} ({s['date']})\n{s['snippet']}\nURL: {s['link']}"
        for i, s in enumerate(snippets[:24])
    ])

    system = (
        "You are a corporate distress and supply chain risk analyst. "
        "Analyze ONLY the search results provided. "
        "Never use prior knowledge about the company. "
        "Only report what is explicitly in the text. "
        "Return ONLY valid JSON. No markdown. No explanation."
    )

    user = f"""Company: {company_name}
Date: {today}

SEARCH RESULTS:
{snippets_text}

PATTERN TAGS (only mark found if evidence exists in results above):
{PATTERN_TAGS_DESC}

RISK LOGIC: {RISK_LOGIC}

COMPARABLE FAILURES (context only):
{library_context[:800]}

Return this exact JSON:
{{
  "company": "{company_name}",
  "scan_date": "{today}",
  "risk_tier": "LOW",
  "signal_count": 0,
  "detected_tags": [],
  "evidence": {{}},
  "raw_signals": [],
  "summary": "2-3 sentences based ONLY on search results.",
  "data_quality": "sufficient|limited|insufficient",
  "recommended_actions": ["action1","action2","action3"],
  "tariff_risk": {{
    "country": "country name or Unknown",
    "exposure": "HIGH|MEDIUM|LOW|UNKNOWN",
    "evidence": "quote from search results or N/A",
    "active_tariffs": "specific tariff description or N/A"
  }},
  "esg_flags": {{
    "violations_found": false,
    "severity": "High|Medium|Low|None",
    "evidence": "quote from search results or N/A",
    "categories": []
  }}
}}

Rules:
- detected_tags: only where evidence exists in results
- evidence: tag_id -> quote from result + URL
- raw_signals: [{{"description":"...","source_url":"...","severity":"High|Medium|Low"}}]
- tariff_risk.exposure: base on country found. HIGH if China/HK. MEDIUM if Vietnam/India/Taiwan. LOW if Mexico/Canada/EU/Japan. UNKNOWN if not found.
- esg_flags.categories: subset of ["labor","environmental","governance","sanctions","safety"]"""

    raw    = _groq_call(system, user, groq_key)
    result = _parse_json(raw)

    if not result:
        return _empty_result(company_name, today, sources)

    result.setdefault("detected_tags", [])
    result.setdefault("evidence", {})
    result.setdefault("raw_signals", [])
    result.setdefault("tariff_risk", {"country":"Unknown","exposure":"UNKNOWN","evidence":"N/A","active_tariffs":"N/A"})
    result.setdefault("esg_flags", {"violations_found":False,"severity":"None","evidence":"N/A","categories":[]})
    result["signal_count"]     = len(result["detected_tags"])
    result["sources_searched"] = list(set(sources))[:10]
    return result


# ── Lightweight scan (2 queries — for bulk sector scorecard) ──────────────────
def run_scan_lightweight(company_name: str, serper_key: str, groq_key: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    queries = [
        f'"{company_name}" "going concern" OR restructuring OR default OR bankruptcy OR layoffs',
        f'"{company_name}" CEO OR CFO resigned OR departed OR fraud OR investigation',
    ]

    snippets = []
    for q in queries:
        snippets.extend(_serper_search(q, serper_key, num=4))

    if not snippets:
        return {"company": company_name, "risk_tier": "LOW", "signal_count": 0,
                "summary": "No distress signals found in public news.",
                "data_quality": "insufficient", "detected_tags": [],
                "tariff_risk": {"exposure": "UNKNOWN", "country": "Unknown"},
                "esg_flags": {"violations_found": False, "severity": "None"}}

    snippets_text = "\n\n".join([
        f"[{i+1}] {s['title']}: {s['snippet']}" for i, s in enumerate(snippets[:10])
    ])

    system = "You are a risk analyst. Analyze ONLY the search results. Return ONLY valid JSON."
    user = f"""Company: {company_name}
Results: {snippets_text}

Return JSON:
{{
  "company": "{company_name}",
  "risk_tier": "LOW",
  "signal_count": 0,
  "detected_tags": [],
  "summary": "one sentence only",
  "data_quality": "sufficient|limited|insufficient",
  "key_concern": "single biggest concern found or None"
}}"""

    raw    = _groq_call(system, user, groq_key, max_tokens=400)
    result = _parse_json(raw)

    if not result:
        return {"company": company_name, "risk_tier": "LOW", "signal_count": 0,
                "summary": "Analysis unavailable.", "data_quality": "insufficient",
                "detected_tags": [], "key_concern": "None"}

    result["tariff_risk"] = {"exposure": "UNKNOWN", "country": "Unknown"}
    result["esg_flags"]   = {"violations_found": False, "severity": "None"}
    return result


# ── Sector supplier discovery ─────────────────────────────────────────────────
def discover_sector_suppliers(category: str, search_term: str,
                               serper_key: str, groq_key: str) -> list:
    """
    Search for top suppliers in a sector category.
    Returns a list of company name strings.
    """
    results = _serper_search(
        f'top {search_term} suppliers manufacturers companies 2024 2025',
        serper_key, num=10
    )
    results += _serper_search(
        f'major {search_term} industry players key suppliers list',
        serper_key, num=8
    )

    if not results:
        return []

    snippets_text = "\n".join([
        f"{r['title']}: {r['snippet']}" for r in results[:15]
    ])

    system = "Extract company names. Return ONLY a JSON array of strings. No explanation."
    user = f"""From these search results about {category} suppliers, extract up to 8 distinct COMPANY NAMES.
Only include actual company names (not generic descriptions).
Exclude vague terms like "major manufacturers" or "leading suppliers".

Results:
{snippets_text}

Return: ["Company A", "Company B", ...]"""

    raw   = _groq_call(system, user, groq_key, max_tokens=200)
    names = _parse_json_array(raw)

    # Clean: keep only strings, strip whitespace, deduplicate
    seen = set()
    clean = []
    for n in names:
        if isinstance(n, str) and n.strip() and n.strip().lower() not in seen:
            seen.add(n.strip().lower())
            clean.append(n.strip())
    return clean[:8]


# ── Helpers ───────────────────────────────────────────────────────────────────
def _empty_result(company_name: str, today: str, sources: list) -> dict:
    return {
        "company": company_name, "scan_date": today,
        "risk_tier": "LOW", "signal_count": 0,
        "detected_tags": [], "evidence": {}, "raw_signals": [],
        "summary": "Insufficient public data found. No distress signals identified.",
        "data_quality": "insufficient",
        "recommended_actions": [
            "Monitor with manual news search periodically.",
            "Request financial statements directly from the supplier.",
            "Check court records for any filed judgments or liens.",
        ],
        "tariff_risk": {"country":"Unknown","exposure":"UNKNOWN","evidence":"N/A","active_tariffs":"N/A"},
        "esg_flags": {"violations_found":False,"severity":"None","evidence":"N/A","categories":[]},
        "sources_searched": sources,
    }
