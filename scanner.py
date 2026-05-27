"""
scanner.py — FailSignal core scan engine
Serper API (news search) + Groq (signal analysis)
stdlib only — no pip installs needed beyond streamlit
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

RISK_LOGIC = "CRITICAL = going_concern OR fraud detected, OR 5+ tags | HIGH = 3-4 tags | MEDIUM = 1-2 tags | LOW = 0 tags"


def _serper_search(query: str, api_key: str, num: int = 8) -> list:
    try:
        body = json.dumps({"q": query, "num": num}).encode("utf-8")
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection("google.serper.dev", context=ctx)
        conn.request("POST", "/search", body, {
            "X-API-KEY": api_key, "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status != 200:
            return []
        data = json.loads(raw)
        return [
            {"title": r.get("title",""), "snippet": r.get("snippet",""),
             "link": r.get("link",""), "date": r.get("date","N/A")}
            for r in data.get("organic", []) if r.get("snippet")
        ]
    except Exception:
        return []


def _groq_call(system: str, user: str, api_key: str) -> str:
    try:
        body = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role":"system","content":system},
                         {"role":"user","content":user}],
            "max_tokens": 1400, "temperature": 0.1,
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
        # try to find JSON object in the text
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
        return {}


def run_scan(company_name: str, serper_key: str, groq_key: str,
             library_context: str) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")

    queries = [
        f'"{company_name}" "going concern" OR restructuring OR "covenant waiver" OR default OR bankruptcy',
        f'"{company_name}" CEO OR CFO OR COO resigned OR departed OR "stepped down" OR fired',
        f'"{company_name}" vendors OR suppliers refused OR "cash on delivery" OR "payment terms" OR default',
        f'"{company_name}" layoffs OR "job cuts" OR "financial distress" OR fraud OR investigation',
    ]

    snippets, sources = [], []
    for q in queries:
        for r in _serper_search(q, serper_key, num=6):
            snippets.append(r)
            sources.append(r["link"])

    if len(snippets) < 2:
        return {
            "company": company_name, "scan_date": today,
            "risk_tier": "LOW", "signal_count": 0,
            "detected_tags": [], "evidence": {}, "raw_signals": [],
            "summary": "Insufficient public data found. No distress signals identified in search results.",
            "data_quality": "insufficient",
            "recommended_actions": [
                "Monitor periodically with manual news search.",
                "Request financial statements directly from the supplier.",
                "Check court records for any filed judgments or liens.",
            ],
            "sources_searched": sources,
        }

    snippets_text = "\n\n".join([
        f"[{i+1}] {s['title']} ({s['date']})\n{s['snippet']}\nURL: {s['link']}"
        for i, s in enumerate(snippets[:20])
    ])

    system = (
        "You are a corporate distress intelligence analyst. "
        "Analyze ONLY the search results provided. "
        "Do not use prior knowledge or training data about this company. "
        "Only report signals explicitly supported by the text below. "
        "Return ONLY valid JSON — no markdown, no explanation outside the JSON."
    )

    user = f"""Company: {company_name}
Scan date: {today}

SEARCH RESULTS:
{snippets_text}

PATTERN TAGS TO DETECT (only if evidence exists in results above):
{PATTERN_TAGS_DESC}

RISK TIER: {RISK_LOGIC}

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
  "summary": "2-3 sentence assessment based ONLY on search results.",
  "data_quality": "sufficient|limited|insufficient",
  "recommended_actions": ["action1","action2","action3"]
}}

Rules:
- detected_tags: only tag_ids with direct evidence in results
- evidence: tag_id -> quote/paraphrase from search result + source URL
- raw_signals: [{{"description":"...","source_url":"...","severity":"High|Medium|Low"}}]
- recommended_actions: supply chain procurement specific, 3 items"""

    raw    = _groq_call(system, user, groq_key)
    result = _parse_json(raw)

    if not result:
        result = {
            "company": company_name, "scan_date": today,
            "risk_tier": "LOW", "signal_count": 0,
            "detected_tags": [], "evidence": {}, "raw_signals": [],
            "summary": "Analysis could not be completed. Verify your Groq API key in secrets.toml.",
            "data_quality": "insufficient",
            "recommended_actions": ["Retry the scan.", "Check API key configuration."],
        }

    result.setdefault("detected_tags", [])
    result.setdefault("evidence", {})
    result.setdefault("raw_signals", [])
    result["signal_count"]    = len(result["detected_tags"])
    result["sources_searched"] = list(set(sources))[:10]
    return result
