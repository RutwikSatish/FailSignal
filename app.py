# ============================================================
#  FailSignal — Supplier Failure Early Warning Agent
#  Single-file version — no external module dependencies
#
#  Repo needs exactly 3 files:
#    app.py              (this file)
#    failure_library.json
#    requirements.txt
#
#  secrets.toml:
#    SERPER_API_KEY   = "..."
#    GROQ_API_KEY     = "..."
#    ALERT_EMAIL_FROM = "..."   # optional
#    ALERT_EMAIL_PASS = "..."   # optional
# ============================================================
import streamlit as st
import json, http.client, ssl, re, smtplib, csv, io
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

st.set_page_config(page_title="FailSignal", page_icon="🔴",
                   layout="wide", initial_sidebar_state="expanded")

# ══════════════════════════════════════════════════════════════
#  LIBRARY LOADER
# ══════════════════════════════════════════════════════════════
_LIBRARY_CACHE = None

def load_library():
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE: return _LIBRARY_CACHE
    path = Path(__file__).parent / "failure_library.json"
    with open(path, encoding="utf-8") as f:
        _LIBRARY_CACHE = json.load(f)
    return _LIBRARY_CACHE

def match_patterns(lib, detected_tags, top_n=3):
    scores = []
    for co in lib["companies"]:
        overlap = set(co["pattern_tags"]) & set(detected_tags)
        if overlap:
            score = len(overlap) / max(len(co["pattern_tags"]), 1)
            scores.append({"company":co,"overlap_tags":list(overlap),"score":score})
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_n]

def build_context(lib, max_companies=12):
    lines = ["FAILURE PATTERN LIBRARY\n"]
    for co in lib["companies"][:max_companies]:
        lines.append(f"COMPANY: {co['name']} ({co['failure_date']}, {co['failure_type']})")
        lines.append(f"  Tags: {', '.join(co['pattern_tags'])}")
        lines.append(f"  Key: {co['key_summary'][:150]}")
        lines.append("")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
#  SCANNER — HTTP helpers
# ══════════════════════════════════════════════════════════════
GROQ_MODEL = "llama-3.3-70b-versatile"

PATTERN_TAGS_DESC = """
c_suite_exodus: 3+ VP-level departures (CEO/CFO/COO/CTO) within 12 months
going_concern_language: Filing or news mentions "going concern" or "substantial doubt about ability to continue"
debt_language_spike: Debt restructuring, covenant waiver, credit default, forbearance, lender negotiations
vendor_payment_failure: Vendors/suppliers demanding cash-on-delivery, refusing to ship, lenders issuing default notices
insider_stock_sales: CEO or CFO sold significant personal stock shortly before bad news
fraud_transparency: SEC investigation, fraud allegations, missing audits, regulatory halt of misleading claims
employee_sentiment_collapse: Unpaid wages, mass layoffs, benefit cuts, payroll delays reported by employees
merger_rescue_blocked: Planned acquisition or merger blocked by regulators
asset_strip_related_party: Key assets sold to related-party entities owned by executives
operational_shortfall: Production, delivery, or revenue misses targets by 50%+ in a single period
"""

RISK_LOGIC = "CRITICAL=going_concern OR fraud OR 5+ tags | HIGH=3-4 | MEDIUM=1-2 | LOW=0"

TARIFF_HIGH   = ["china","prc","hong kong"]
TARIFF_MEDIUM = ["vietnam","taiwan","india","bangladesh","cambodia","myanmar"]
TARIFF_LOW    = ["mexico","canada","germany","uk","japan","south korea","france","netherlands"]

def _serper(query, api_key, num=6):
    try:
        body = json.dumps({"q":query,"num":num}).encode("utf-8")
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection("google.serper.dev", context=ctx)
        conn.request("POST","/search",body,{
            "X-API-KEY":api_key,"Content-Type":"application/json",
            "Content-Length":str(len(body))})
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8",errors="replace")
        conn.close()
        if resp.status != 200: return []
        return [{"title":r.get("title",""),"snippet":r.get("snippet",""),
                 "link":r.get("link",""),"date":r.get("date","N/A")}
                for r in json.loads(raw).get("organic",[]) if r.get("snippet")]
    except: return []

def _groq(system, user, api_key, max_tokens=1400):
    try:
        body = json.dumps({"model":GROQ_MODEL,
            "messages":[{"role":"system","content":system},{"role":"user","content":user}],
            "max_tokens":max_tokens,"temperature":0.1},
            ensure_ascii=True).encode("ascii")
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection("api.groq.com",context=ctx)
        conn.request("POST","/openai/v1/chat/completions",body,{
            "Authorization":"Bearer "+api_key,"Content-Type":"application/json",
            "Content-Length":str(len(body))})
        resp = conn.getresponse()
        raw  = resp.read().decode("utf-8",errors="replace")
        conn.close()
        if resp.status != 200: return ""
        return json.loads(raw)["choices"][0]["message"]["content"].strip()
    except: return ""

def _parse_json(text):
    text = re.sub(r"```(?:json)?","",text).strip().rstrip("`").strip()
    try: return json.loads(text)
    except:
        m = re.search(r"\{.*\}",text,re.DOTALL)
        if m:
            try: return json.loads(m.group())
            except: pass
        return {}

def _parse_json_array(text):
    text = re.sub(r"```(?:json)?","",text).strip().rstrip("`").strip()
    try:
        r = json.loads(text)
        return r if isinstance(r,list) else []
    except:
        m = re.search(r"\[.*\]",text,re.DOTALL)
        if m:
            try: return json.loads(m.group())
            except: pass
        return []

def _empty(company, today, sources=[]):
    return {"company":company,"scan_date":today,"risk_tier":"LOW","signal_count":0,
            "detected_tags":[],"evidence":{},"raw_signals":[],
            "summary":"Insufficient public data. No distress signals identified.",
            "data_quality":"insufficient",
            "recommended_actions":["Monitor with manual search periodically.",
                "Request financial statements directly.","Check court records for liens."],
            "tariff_risk":{"country":"Unknown","exposure":"UNKNOWN","evidence":"N/A","active_tariffs":"N/A"},
            "esg_flags":{"violations_found":False,"severity":"None","evidence":"N/A","categories":[]},
            "sources_searched":sources}

# ══════════════════════════════════════════════════════════════
#  SCAN FUNCTIONS
# ══════════════════════════════════════════════════════════════
def run_scan(company_name, serper_key, groq_key, library_context):
    today   = datetime.now().strftime("%Y-%m-%d")
    queries = [
        f'"{company_name}" "going concern" OR restructuring OR "covenant waiver" OR default OR bankruptcy',
        f'"{company_name}" CEO OR CFO OR COO resigned OR departed OR "stepped down" OR fired',
        f'"{company_name}" vendors OR suppliers refused OR "cash on delivery" OR "payment terms"',
        f'"{company_name}" layoffs OR "job cuts" OR "financial distress" OR fraud OR investigation',
        f'"{company_name}" manufacturing OR factory OR headquarters location country operations',
        f'"{company_name}" "labor violation" OR "environmental fine" OR sanctions OR "human rights" OR OSHA OR EPA',
    ]
    snippets, sources = [], []
    for q in queries:
        for r in _serper(q, serper_key, num=5):
            snippets.append(r); sources.append(r["link"])

    if len(snippets) < 2:
        return _empty(company_name, today, sources)

    snip_text = "\n\n".join([
        f"[{i+1}] {s['title']} ({s['date']})\n{s['snippet']}\nURL: {s['link']}"
        for i,s in enumerate(snippets[:22])])

    system = ("You are a corporate distress analyst. Analyze ONLY the search results provided. "
              "Never use prior knowledge. Only report what is in the text. "
              "Return ONLY valid JSON. No markdown. No explanation.")

    user = f"""Company: {company_name}
Date: {today}

SEARCH RESULTS:
{snip_text}

PATTERN TAGS (only mark found if evidence exists in results above):
{PATTERN_TAGS_DESC}

RISK LOGIC: {RISK_LOGIC}

COMPARABLE FAILURES (context only):
{library_context[:700]}

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
    "country": "country or Unknown",
    "exposure": "HIGH|MEDIUM|LOW|UNKNOWN",
    "evidence": "quote from results or N/A",
    "active_tariffs": "tariff description or N/A"
  }},
  "esg_flags": {{
    "violations_found": false,
    "severity": "High|Medium|Low|None",
    "evidence": "quote from results or N/A",
    "categories": []
  }}
}}"""

    raw    = _groq(system, user, groq_key)
    result = _parse_json(raw)
    if not result: return _empty(company_name, today, sources)

    result.setdefault("detected_tags",[])
    result.setdefault("evidence",{})
    result.setdefault("raw_signals",[])
    result.setdefault("tariff_risk",{"country":"Unknown","exposure":"UNKNOWN","evidence":"N/A","active_tariffs":"N/A"})
    result.setdefault("esg_flags",{"violations_found":False,"severity":"None","evidence":"N/A","categories":[]})
    result["signal_count"]     = len(result["detected_tags"])
    result["sources_searched"] = list(set(sources))[:10]
    return result

def run_scan_lightweight(company_name, serper_key, groq_key):
    today   = datetime.now().strftime("%Y-%m-%d")
    queries = [
        f'"{company_name}" "going concern" OR restructuring OR default OR bankruptcy OR layoffs',
        f'"{company_name}" CEO OR CFO resigned OR departed OR fraud OR investigation',
    ]
    snippets = []
    for q in queries:
        snippets.extend(_serper(q, serper_key, num=4))

    if not snippets:
        return {"company":company_name,"risk_tier":"LOW","signal_count":0,
                "summary":"No distress signals found.","data_quality":"insufficient",
                "detected_tags":[],"key_concern":"None"}

    snip_text = "\n\n".join([f"[{i+1}] {s['title']}: {s['snippet']}"
                             for i,s in enumerate(snippets[:10])])
    system = "You are a risk analyst. Analyze ONLY the results. Return ONLY valid JSON."
    user   = f"""Company: {company_name}
Results: {snip_text}
Return JSON: {{"company":"{company_name}","risk_tier":"LOW","signal_count":0,
"detected_tags":[],"summary":"one sentence only","data_quality":"sufficient|limited|insufficient",
"key_concern":"single biggest concern or None"}}"""

    raw    = _groq(system, user, groq_key, max_tokens=300)
    result = _parse_json(raw)
    if not result:
        return {"company":company_name,"risk_tier":"LOW","signal_count":0,
                "summary":"Analysis unavailable.","data_quality":"insufficient",
                "detected_tags":[],"key_concern":"None"}
    return result

def discover_sector_suppliers(category, search_term, serper_key, groq_key):
    results  = _serper(f'top {search_term} suppliers manufacturers companies 2024 2025', serper_key, num=10)
    results += _serper(f'major {search_term} industry players key suppliers list', serper_key, num=8)
    if not results: return []

    snip_text = "\n".join([f"{r['title']}: {r['snippet']}" for r in results[:14]])
    system = "Extract company names. Return ONLY a JSON array of strings."
    user   = f"""From these results about {category} suppliers, extract up to 8 COMPANY NAMES.
Only actual company names. No generic descriptions.
Results: {snip_text}
Return: ["Company A", "Company B", ...]"""

    raw   = _groq(system, user, groq_key, max_tokens=200)
    names = _parse_json_array(raw)
    seen, clean = set(), []
    for n in names:
        if isinstance(n,str) and n.strip() and n.strip().lower() not in seen:
            seen.add(n.strip().lower()); clean.append(n.strip())
    return clean[:8]

# ══════════════════════════════════════════════════════════════
#  CONSTANTS & HELPERS
# ══════════════════════════════════════════════════════════════
SECTORS = {
    "Automotive Components":  "automotive parts components stampings castings",
    "Electronic Components":  "electronic components PCB semiconductor passive",
    "Packaging Materials":    "industrial packaging corrugated flexible containers",
    "Chemical Intermediates": "chemical intermediates raw materials specialty",
    "Logistics & Freight":    "freight forwarding logistics carriers trucking",
    "Medical Devices":        "medical device surgical instruments diagnostics",
    "Aerospace Parts":        "aerospace components machined parts fasteners",
    "Food Ingredients":       "food ingredients flavors additives raw materials",
    "Textiles & Apparel":     "textile fabric yarn apparel manufacturing",
    "IT Hardware & OEM":      "IT hardware server storage networking OEM",
}

TIER_ORDER = ["CRITICAL","HIGH","MEDIUM","LOW","UNKNOWN"]
TIER_COLOR = {"CRITICAL":"#ef4444","HIGH":"#f97316","MEDIUM":"#f59e0b","LOW":"#10b981","UNKNOWN":"#6b7280"}
TIER_BADGE = {"CRITICAL":"fs-badge-critical","HIGH":"fs-badge-high",
              "MEDIUM":"fs-badge-medium","LOW":"fs-badge-low","UNKNOWN":"fs-badge-unknown"}

def badge(tier):
    return f'<span class="{TIER_BADGE.get(tier,"fs-badge-unknown")}">{tier}</span>'

def send_email(to_addr, subject, body, email_from, email_pass):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject; msg["From"] = email_from; msg["To"] = to_addr
        msg.attach(MIMEText(body,"plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com",465) as s:
            s.login(email_from, email_pass)
            s.sendmail(email_from, to_addr, msg.as_string())
        return True, "Sent"
    except Exception as e:
        return False, str(e)

def alert_body(changes, watchlist):
    lines = ["FailSignal Watchlist Report","="*40,""]
    if changes:
        lines.append("RISK TIER CHANGES:")
        for c in changes:
            lines.append(f"  {c['company']}: {c['old_tier']} → {c['new_tier']}")
        lines.append("")
    lines.append("FULL WATCHLIST:")
    for w in watchlist:
        lines.append(f"  {w['company']:30s} {w['risk_tier']:10s} ({w['signal_count']} signals)")
    lines += ["","─"*40,"FailSignal · Supplier Failure Early Warning"]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
#  SECRETS & SESSION STATE
# ══════════════════════════════════════════════════════════════
def _s(k):
    try:    return st.secrets[k]
    except: return ""

SERPER_KEY = _s("SERPER_API_KEY")
GROQ_KEY   = _s("GROQ_API_KEY")
EMAIL_FROM = _s("ALERT_EMAIL_FROM")
EMAIL_PASS = _s("ALERT_EMAIL_PASS")

for k,v in [("result",None),("watchlist",[]),("scorecard_results",[]),
            ("scorecard_category",""),("alert_email","")]:
    if k not in st.session_state: st.session_state[k] = v

# ══════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');
html,body,[class*="css"]{background:#08090f !important;color:#f9fafb !important;font-family:'DM Sans',sans-serif}
section[data-testid="stSidebar"]{background:#0d1117 !important;border-right:1px solid #1f2937 !important}
section[data-testid="stSidebar"] *{color:#f9fafb !important}
div[data-testid="metric-container"]{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px 18px !important}
div[data-testid="metric-container"] label{color:#6b7280 !important;font-size:11px !important}
div[data-testid="metric-container"] [data-testid="stMetricValue"]{color:#f9fafb !important;font-family:'Space Mono',monospace !important;font-size:1.4rem !important}
button[data-baseweb="tab"]{background:transparent !important;color:#6b7280 !important;font-family:'Space Mono',monospace !important;font-size:10px !important;border-bottom:2px solid transparent !important}
button[data-baseweb="tab"][aria-selected="true"]{color:#ef4444 !important;border-bottom:2px solid #ef4444 !important}
.stButton>button{background:#1d4ed8 !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-size:13px !important}
.stButton>button:hover{background:#2563eb !important}
.stTextInput>div>div>input{background:#111827 !important;border:1px solid #374151 !important;color:#f9fafb !important;border-radius:8px !important;font-size:14px !important}
.stSelectbox>div>div{background:#111827 !important;border:1px solid #374151 !important;color:#f9fafb !important;border-radius:8px !important}
.fs-card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:16px 20px;margin-bottom:12px}
.fs-label{font-family:'Space Mono',monospace;font-size:10px;color:#ef4444;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px;display:block}
.fs-hero{background:linear-gradient(135deg,#08090f,#0d1520);border:1px solid #1f293780;border-radius:14px;padding:24px 28px;margin-bottom:20px}
.fs-tag{display:inline-block;background:#ef444420;color:#f87171;border:1px solid #ef444440;border-radius:5px;padding:2px 9px;font-size:11px;font-family:'Space Mono',monospace;margin:2px}
.fs-tag-green{background:#10b98120;color:#34d399;border-color:#10b98140}
.fs-tag-amber{background:#f59e0b20;color:#fbbf24;border-color:#f59e0b40}
.fs-badge-critical{background:#7f1d1d;color:#fca5a5;padding:5px 14px;border-radius:6px;font-family:'Space Mono',monospace;font-size:12px;font-weight:700;display:inline-block}
.fs-badge-high{background:#78350f;color:#fed7aa;padding:5px 14px;border-radius:6px;font-family:'Space Mono',monospace;font-size:12px;font-weight:700;display:inline-block}
.fs-badge-medium{background:#713f12;color:#fef08a;padding:5px 14px;border-radius:6px;font-family:'Space Mono',monospace;font-size:12px;font-weight:700;display:inline-block}
.fs-badge-low{background:#064e3b;color:#a7f3d0;padding:5px 14px;border-radius:6px;font-family:'Space Mono',monospace;font-size:12px;font-weight:700;display:inline-block}
.fs-badge-unknown{background:#1f2937;color:#9ca3af;padding:5px 14px;border-radius:6px;font-family:'Space Mono',monospace;font-size:12px;font-weight:700;display:inline-block}
.alert-r{background:#1a0808;border-left:4px solid #ef4444;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px}
.alert-y{background:#1a1108;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px}
.alert-g{background:#081a0e;border-left:4px solid #10b981;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px}
.alert-b{background:#080d1a;border-left:4px solid #3b82f6;border-radius:0 8px 8px 0;padding:12px 16px;margin-bottom:10px}
.sc-row{display:flex;align-items:center;gap:14px;padding:11px 15px;background:#111827;border:1px solid #1f2937;border-radius:8px;margin-bottom:6px}
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  LOAD LIBRARY
# ══════════════════════════════════════════════════════════════
@st.cache_resource
def get_lib():
    return load_library()

lib         = get_lib()
lib_context = build_context(lib)

# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:14px 0 10px'>
        <div style='font-family:Space Mono,monospace;font-size:18px;font-weight:700;color:#ef4444;letter-spacing:-1px'>🔴 FailSignal</div>
        <div style='font-size:10px;color:#6b7280;letter-spacing:3px;text-transform:uppercase;margin-top:3px'>Supplier Early Warning</div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")
    if SERPER_KEY and GROQ_KEY:
        st.markdown('<div style="font-size:12px;color:#10b981">✅ APIs ready</div>', unsafe_allow_html=True)
    else:
        missing = [k for k,v in [("SERPER_API_KEY",SERPER_KEY),("GROQ_API_KEY",GROQ_KEY)] if not v]
        st.markdown(f'<div style="font-size:11px;color:#f59e0b">⚠️ Missing: {", ".join(missing)}</div>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("**Alert Email**")
    st.session_state.alert_email = st.text_input("Email", value=st.session_state.alert_email,
        placeholder="you@email.com", label_visibility="collapsed", key="alert_input")
    if EMAIL_FROM: st.caption(f"Sending from: {EMAIL_FROM}")
    else: st.caption("Add ALERT_EMAIL_FROM + ALERT_EMAIL_PASS to secrets.toml")
    st.markdown("---")
    st.markdown(f"""<div style='font-size:11px;color:#6b7280;line-height:1.9'>
    <b style='color:#f9fafb'>Library v{lib["version"]}</b><br>
    {lib["total_companies"]} companies · {lib["total_signals"]} signals<br>
    {lib["total_pattern_tags"]} pattern tags · verified sources</div>""", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown('<div style="font-size:10px;color:#374151;line-height:1.7">Rutwik Satish<br>MS Eng. Mgmt · Northeastern<br>Serper + Groq + Failure Library</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  HERO
# ══════════════════════════════════════════════════════════════
st.markdown("""<div class='fs-hero'>
    <div style='font-size:22px;font-weight:700;letter-spacing:-.5px;margin-bottom:4px'>
        🔴 Fail<span style='color:#ef4444'>Signal</span>
    </div>
    <div style='font-size:12px;color:#6b7280;margin-bottom:10px'>
        Failure risk · Tariff exposure · ESG flags · Sector scorecard · Watchlist monitoring
    </div>
    <div style='display:flex;flex-wrap:wrap;gap:6px'>
        <span style='font-size:11px;background:#ef444415;color:#f87171;border:1px solid #ef444430;border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>Financial distress</span>
        <span style='font-size:11px;background:#3b82f615;color:#93c5fd;border:1px solid #3b82f630;border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>Tariff/geo risk</span>
        <span style='font-size:11px;background:#10b98115;color:#34d399;border:1px solid #10b98130;border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>ESG flags</span>
        <span style='font-size:11px;background:#f59e0b15;color:#fbbf24;border:1px solid #f59e0b30;border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>Sector scorecard</span>
        <span style='font-size:11px;background:#8b5cf615;color:#c4b5fd;border:1px solid #8b5cf630;border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>Alerts</span>
    </div>
</div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
#  TABS
# ══════════════════════════════════════════════════════════════
t_scan, t_result, t_scorecard, t_backtest, t_watchlist, t_library = st.tabs([
    "🔍  Scan","📋  Result","🏭  Sector Scorecard","🔬  Back-test","👁  Watchlist","📚  Library"])

# ────────────────────────────── TAB: SCAN ─────────────────────────────────────
with t_scan:
    st.markdown('<span class="fs-label">Enter supplier or company name</span>', unsafe_allow_html=True)
    ci, cb = st.columns([3,1])
    with ci:
        company_input = st.text_input("c", label_visibility="collapsed",
            placeholder="e.g. Acme Parts Co. · Spirit Airlines", key="scan_input")
    with cb:
        scan_btn = st.button("Scan →", use_container_width=True, disabled=(not SERPER_KEY or not GROQ_KEY))

    if not SERPER_KEY or not GROQ_KEY:
        st.markdown("""<div class='alert-y'><b>Setup required</b><br>
        Add to <code>.streamlit/secrets.toml</code>:<br>
        <code>SERPER_API_KEY = "..."</code><br><code>GROQ_API_KEY = "..."</code></div>""",
        unsafe_allow_html=True)

    if scan_btn and company_input.strip():
        with st.spinner(f"Scanning {company_input.strip()} — 6 queries…"):
            result = run_scan(company_input.strip(), SERPER_KEY, GROQ_KEY, lib_context)
            st.session_state.result = result
            existing = [w["company"].lower() for w in st.session_state.watchlist]
            if result["company"].lower() not in existing:
                st.session_state.watchlist.append({
                    "company":result["company"],"risk_tier":result["risk_tier"],
                    "scan_date":result["scan_date"],"signal_count":result["signal_count"],
                    "prev_tier":result["risk_tier"],
                    "tariff_exposure":result.get("tariff_risk",{}).get("exposure","UNKNOWN"),
                    "esg_violation":result.get("esg_flags",{}).get("violations_found",False)})
        tier = result["risk_tier"]; c = TIER_COLOR.get(tier,"#6b7280")
        st.markdown(f"""<div style='background:{c}15;border:1px solid {c}40;border-left:4px solid {c};
            border-radius:0 10px 10px 0;padding:14px 18px;margin-top:10px'>
            <span style='font-family:Space Mono,monospace;font-size:13px;font-weight:700;color:{c}'>
            {tier} RISK — {result["signal_count"]} signals</span><br>
            <span style='font-size:12px;color:#9ca3af'>{result.get("summary","")}</span>
        </div>""", unsafe_allow_html=True)
        st.caption("→ Full report in **Result** tab · Added to **Watchlist**")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<span class="fs-label">What is scanned</span>', unsafe_allow_html=True)
    cols = st.columns(3)
    for i,(title,desc) in enumerate([
        ("💸 Financial distress","Restructuring · default · going concern · bankruptcy"),
        ("👤 Leadership signals","CEO/CFO/COO departures · replacements"),
        ("🚚 Vendor signals","Supplier refusals · cash-on-delivery demands"),
        ("👷 Employee signals","Layoffs · fraud · regulatory investigations"),
        ("🌍 Geo/Tariff risk","Manufacturing country · active tariff exposure"),
        ("🌿 ESG flags","Labor violations · environmental fines · sanctions"),
    ]):
        with cols[i%3]:
            st.markdown(f"""<div class='fs-card' style='margin-bottom:8px'>
                <div style='font-size:12px;font-weight:600;color:#f9fafb;margin-bottom:3px'>{title}</div>
                <div style='font-size:11px;color:#6b7280'>{desc}</div>
            </div>""", unsafe_allow_html=True)

# ────────────────────────────── TAB: RESULT ───────────────────────────────────
with t_result:
    r = st.session_state.result
    if r is None:
        st.markdown('<div class="fs-card" style="text-align:center;padding:36px"><div style="font-size:13px;color:#6b7280">Run a scan first</div></div>', unsafe_allow_html=True)
    else:
        tier = r.get("risk_tier","LOW"); c = TIER_COLOR.get(tier,"#6b7280"); bc = TIER_BADGE.get(tier,"fs-badge-low")
        h1,h2 = st.columns([2,1])
        with h1:
            st.markdown(f"""<div style='font-size:21px;font-weight:700;color:#f9fafb;margin-bottom:3px'>{r["company"]}</div>
            <div style='font-size:12px;color:#6b7280'>Scanned {r.get("scan_date","today")} · Data: {r.get("data_quality","N/A")}</div>""", unsafe_allow_html=True)
        with h2:
            st.markdown(f'<div class="{bc}">{tier} RISK<br><span style="font-size:11px;font-weight:400">{r["signal_count"]}/10 signals</span></div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        as_ = "r" if tier in ("CRITICAL","HIGH") else "y" if tier=="MEDIUM" else "g"
        st.markdown(f"""<div class='alert-{as_}'>
            <span class='fs-label'>Assessment</span>
            <div style='font-size:13px;color:#f9fafb;line-height:1.7'>{r.get("summary","")}</div>
        </div>""", unsafe_allow_html=True)

        detected = r.get("detected_tags",[])
        all_tags = ["c_suite_exodus","going_concern_language","debt_language_spike","vendor_payment_failure",
                    "insider_stock_sales","fraud_transparency","employee_sentiment_collapse",
                    "merger_rescue_blocked","asset_strip_related_party","operational_shortfall"]
        st.markdown('<span class="fs-label" style="margin-top:16px">Pattern tags</span>', unsafe_allow_html=True)
        th = "".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' if t in detected
                      else f'<span style="display:inline-block;background:#1f293760;color:#374151;border:1px solid #1f2937;border-radius:5px;padding:2px 9px;font-size:11px;font-family:Space Mono,monospace;margin:2px">{t.replace("_"," ")}</span>'
                      for t in all_tags])
        st.markdown(f'<div>{th}</div>', unsafe_allow_html=True)

        # Tariff card
        tr = r.get("tariff_risk",{}); tr_exp = tr.get("exposure","UNKNOWN")
        tr_c = {"HIGH":"#ef4444","MEDIUM":"#f59e0b","LOW":"#10b981","UNKNOWN":"#6b7280"}.get(tr_exp,"#6b7280")
        tr_b = {"HIGH":"fs-badge-critical","MEDIUM":"fs-badge-medium","LOW":"fs-badge-low","UNKNOWN":"fs-badge-unknown"}.get(tr_exp,"fs-badge-unknown")
        st.markdown('<span class="fs-label" style="margin-top:16px">🌍 Tariff & Geopolitical Risk</span>', unsafe_allow_html=True)
        st.markdown(f"""<div class='fs-card' style='border-left:3px solid {tr_c}'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>
                <div><div style='font-size:14px;font-weight:600;color:#f9fafb'>Country: {tr.get("country","Unknown")}</div></div>
                <div class='{tr_b}'>{tr_exp} EXPOSURE</div>
            </div>
            <div style='font-size:12px;color:#6b7280;line-height:1.6'>
                <b style='color:#9ca3af'>Active tariffs:</b> {tr.get("active_tariffs","N/A")}<br>
                <b style='color:#9ca3af'>Evidence:</b> {tr.get("evidence","N/A")[:180]}
            </div></div>""", unsafe_allow_html=True)

        # ESG card
        esg = r.get("esg_flags",{}); ef = esg.get("violations_found",False); es = esg.get("severity","None")
        ec  = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981","None":"#10b981"}.get(es,"#10b981")
        eb  = {"High":"fs-badge-critical","Medium":"fs-badge-medium","Low":"fs-badge-low","None":"fs-badge-low"}.get(es,"fs-badge-low")
        cats_html = " ".join([f'<span class="fs-tag-amber">{c2}</span>' for c2 in esg.get("categories",[])])
        st.markdown('<span class="fs-label" style="margin-top:4px">🌿 ESG Flags</span>', unsafe_allow_html=True)
        st.markdown(f"""<div class='fs-card' style='border-left:3px solid {ec}'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>
                <div><div style='font-size:14px;font-weight:600;color:#f9fafb'>
                    {"⚠️ ESG Violations Found" if ef else "✅ No ESG Violations Found"}</div>
                <div style='margin-top:4px'>{cats_html}</div></div>
                <div class='{eb}'>{es.upper()}</div>
            </div>
            <div style='font-size:12px;color:#6b7280'>{esg.get("evidence","No violations in recent news.")[:220]}</div>
        </div>""", unsafe_allow_html=True)

        # Evidence
        if r.get("evidence"):
            st.markdown('<span class="fs-label" style="margin-top:4px">Evidence from search results</span>', unsafe_allow_html=True)
            for tid, text in r["evidence"].items():
                st.markdown(f"""<div class='fs-card' style='border-left:3px solid {c}'>
                    <div style='font-size:10px;font-family:Space Mono,monospace;color:{c};margin-bottom:5px'>{tid.replace("_"," ").upper()}</div>
                    <div style='font-size:12px;color:#d1d5db;line-height:1.6'>{text}</div>
                </div>""", unsafe_allow_html=True)

        for sig in r.get("raw_signals",[]):
            sc2 = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sig.get("severity","Medium"),"#6b7280")
            url = sig.get("source_url","")
            up  = f'<a href="{url}" target="_blank" style="color:#3b82f6;font-size:11px">{url[:55]}…</a>' if url else ""
            st.markdown(f"""<div style='display:flex;gap:10px;padding:9px 12px;background:#0d1117;border-radius:8px;margin-bottom:5px;align-items:flex-start'>
                <span style='color:{sc2};font-size:10px;font-family:Space Mono,monospace;min-width:44px;margin-top:2px'>{sig.get("severity","")}</span>
                <div><div style='font-size:12px;color:#f9fafb;line-height:1.5'>{sig.get("description","")}</div>{up}</div>
            </div>""", unsafe_allow_html=True)

        if detected:
            comparables = match_patterns(lib, detected, top_n=2)
            if comparables:
                st.markdown('<span class="fs-label" style="margin-top:4px">Comparable historical failures</span>', unsafe_allow_html=True)
                for m in comparables:
                    co2 = m["company"]
                    st.markdown(f"""<div class='fs-card' style='border-left:3px solid #ef4444'>
                        <div style='display:flex;justify-content:space-between'>
                            <div><div style='font-size:14px;font-weight:600;color:#f87171'>{co2["name"]}</div>
                            <div style='font-size:11px;color:#6b7280'>{co2["failure_type"]} · {co2["failure_date"]} · {co2["industry"]}</div></div>
                            <div style='font-family:Space Mono,monospace;font-size:13px;color:#f87171;font-weight:700'>{m["score"]:.0%}</div>
                        </div>
                        <div style='font-size:12px;color:#9ca3af;margin-top:7px;line-height:1.5'>{co2["key_summary"][:200]}…</div>
                        <div style='margin-top:7px'>{"".join([f"<span class=\'fs-tag\'>{t.replace(chr(95),' ')}</span>" for t in m["overlap_tags"]])}</div>
                    </div>""", unsafe_allow_html=True)

        if r.get("recommended_actions"):
            st.markdown('<span class="fs-label" style="margin-top:4px">Recommended actions</span>', unsafe_allow_html=True)
            for i,a in enumerate(r["recommended_actions"],1):
                st.markdown(f"""<div style='display:flex;gap:10px;padding:9px 12px;background:#111827;border-radius:8px;margin-bottom:5px'>
                    <span style='font-family:Space Mono,monospace;font-size:11px;color:#6b7280;min-width:18px'>{i}</span>
                    <span style='font-size:12px;color:#f9fafb'>{a}</span>
                </div>""", unsafe_allow_html=True)

        if r.get("sources_searched"):
            with st.expander(f"Sources searched ({len(r['sources_searched'])})", expanded=False):
                for s in r["sources_searched"]:
                    st.markdown(f'<a href="{s}" target="_blank" style="font-size:11px;color:#3b82f6">{s}</a><br>', unsafe_allow_html=True)

# ─────────────────────────── TAB: SECTOR SCORECARD ────────────────────────────
with t_scorecard:
    st.markdown('<span class="fs-label">Select a sector to risk-score key suppliers</span>', unsafe_allow_html=True)
    sel_sector = st.selectbox("Sector", list(SECTORS.keys()), key="sector_select")
    sc1, sc2 = st.columns([2,1])
    with sc1:
        st.markdown("""<div class='alert-b' style='margin-top:4px'>
            <div style='font-size:12px;color:#93c5fd;line-height:1.6'>
                Discovers major players in this sector via live search, then runs a lightweight
                FailSignal scan on each. Shows which suppliers show current distress signals.
            </div></div>""", unsafe_allow_html=True)
    with sc2:
        run_sc = st.button("Run Sector Scan →", use_container_width=True, disabled=(not SERPER_KEY or not GROQ_KEY))

    if run_sc:
        st.session_state.scorecard_category = sel_sector
        st.session_state.scorecard_results  = []
        with st.spinner(f"Discovering {sel_sector} suppliers…"):
            companies = discover_sector_suppliers(sel_sector, SECTORS[sel_sector], SERPER_KEY, GROQ_KEY)
        if not companies:
            st.warning("Could not identify suppliers for this sector. Try again.")
        else:
            st.markdown(f'<div style="font-size:12px;color:#6b7280;margin:8px 0">Found {len(companies)} — scanning each…</div>', unsafe_allow_html=True)
            prog = st.progress(0)
            results = []
            for i,co in enumerate(companies):
                prog.progress((i+1)/len(companies), text=f"Scanning {co}…")
                results.append(run_scan_lightweight(co, SERPER_KEY, GROQ_KEY))
            prog.empty()
            st.session_state.scorecard_results = results

    sc_results = st.session_state.scorecard_results
    if sc_results:
        st.markdown(f'<div style="font-size:16px;font-weight:600;color:#f9fafb;margin:16px 0 8px">{st.session_state.scorecard_category} — {len(sc_results)} suppliers</div>', unsafe_allow_html=True)
        tc = {t:0 for t in TIER_ORDER}
        for res in sc_results: tc[res.get("risk_tier","UNKNOWN")] = tc.get(res.get("risk_tier","UNKNOWN"),0)+1
        mc = st.columns(5)
        for i,t in enumerate(TIER_ORDER): mc[i].metric(t, tc.get(t,0))
        st.markdown("<br>", unsafe_allow_html=True)

        sorted_sc = sorted(sc_results, key=lambda x: TIER_ORDER.index(x.get("risk_tier","UNKNOWN")) if x.get("risk_tier","UNKNOWN") in TIER_ORDER else 99)
        for res in sorted_sc:
            t2 = res.get("risk_tier","UNKNOWN"); c2 = TIER_COLOR.get(t2,"#6b7280"); b2 = TIER_BADGE.get(t2,"fs-badge-unknown")
            st.markdown(f"""<div class='sc-row'>
                <div style='flex:1'>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>{res["company"]}</div>
                    <div style='font-size:11px;color:#6b7280;margin-top:2px'>{res.get("summary","")[:110]}</div>
                </div>
                <div style='text-align:center;min-width:80px'><div class='{b2}'>{t2}</div>
                    <div style='font-size:10px;color:#6b7280;margin-top:3px;font-family:Space Mono,monospace'>{res.get("signal_count",0)} signals</div>
                </div>
                <div style='min-width:160px;font-size:11px;color:#9ca3af'>
                    <b style='color:#6b7280'>Concern:</b><br>{res.get("key_concern","—") or "—"}
                </div>
            </div>""", unsafe_allow_html=True)

        high_risk = [r["company"] for r in sc_results if r.get("risk_tier") in ("CRITICAL","HIGH")]
        safe      = [r["company"] for r in sc_results if r.get("risk_tier") == "LOW"]
        if high_risk:
            st.markdown(f"""<div class='alert-r'><b>⚠️ Elevated risk:</b> {", ".join(high_risk)}</div>""", unsafe_allow_html=True)
        if safe:
            st.markdown(f"""<div class='alert-g'><b>✅ Lower risk alternatives:</b> {", ".join(safe)}</div>""", unsafe_allow_html=True)

        csv_buf = io.StringIO()
        w2 = csv.DictWriter(csv_buf, fieldnames=["company","risk_tier","signal_count","summary","key_concern","data_quality"])
        w2.writeheader()
        for res in sorted_sc:
            w2.writerow({k:res.get(k,"") for k in ["company","risk_tier","signal_count","summary","key_concern","data_quality"]})
        st.download_button("⬇️  Export CSV", csv_buf.getvalue().encode(),
                           f"scorecard_{sel_sector.replace(' ','_')}.csv", "text/csv")

# ──────────────────────────── TAB: BACK-TEST ──────────────────────────────────
with t_backtest:
    st.markdown('<span class="fs-label">Back-test: Bed Bath & Beyond — 18 months before collapse</span>', unsafe_allow_html=True)
    st.markdown("""<div class='alert-y'><div class='fs-label'>What this shows</div>
        <div style='font-size:12px;color:#f9fafb;line-height:1.7'>
        BBBY filed Chapter 11 April 23 2023. All signals below were publicly available
        from October 2021 — 18 months before collapse.
        </div></div>""", unsafe_allow_html=True)
    bbby = next((c for c in lib["companies"] if c["id"]=="bbby"), None)
    if bbby:
        st.markdown(f"""<div style='display:flex;justify-content:space-between;align-items:center;margin:12px 0'>
            <div><div style='font-size:18px;font-weight:700;color:#f9fafb'>{bbby["name"]}</div>
            <div style='font-size:12px;color:#6b7280'>{bbby["failure_type"]} · {bbby["failure_date"]}</div></div>
            <div class='fs-badge-critical'>HIGH RISK<br><span style='font-size:10px;font-weight:400'>{len(bbby["pattern_tags"])}/10 signals</span></div>
        </div>""", unsafe_allow_html=True)
        st.markdown("".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' for t in bbby["pattern_tags"]]), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        for sig in sorted(bbby["signals"], key=lambda x: -x["months_before"]):
            sc3 = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sig["severity"],"#6b7280")
            st.markdown(f"""<div class='fs-card' style='border-left:3px solid {sc3};margin-bottom:7px'>
                <div style='display:flex;gap:12px'>
                    <div style='font-family:Space Mono,monospace;font-size:11px;color:{sc3};min-width:58px;margin-top:1px'>T−{sig["months_before"]}mo</div>
                    <div><div style='font-size:12px;color:#f9fafb;line-height:1.5'>{sig["signal_description"]}</div>
                    <a href='{sig["source_url"]}' target='_blank' style='font-size:11px;color:#3b82f6'>source →</a></div>
                </div></div>""", unsafe_allow_html=True)

# ──────────────────────────── TAB: WATCHLIST ──────────────────────────────────
with t_watchlist:
    wl = st.session_state.watchlist
    if not wl:
        st.markdown('<div class="fs-card" style="text-align:center;padding:36px"><div style="font-size:13px;color:#6b7280">Run a scan — it auto-adds here.</div></div>', unsafe_allow_html=True)
    else:
        m1,m2,m3,m4 = st.columns(4)
        m1.metric("Monitored", len(wl))
        hr = sum(1 for w in wl if w["risk_tier"] in ("CRITICAL","HIGH"))
        m2.metric("High/Critical", hr)
        hp = round(hr/len(wl)*100) if wl else 0
        icon = "🔴" if hp>=40 else "🟡" if hp>=20 else "🟢"
        m3.metric("Portfolio Risk", f"{hp}%", f"{icon} concentration")
        te = sum(1 for w in wl if w.get("tariff_exposure") in ("HIGH","MEDIUM"))
        m4.metric("Tariff Exposed", te)
        if hp>=40:
            st.markdown(f"""<div class='alert-r'><b>⚠️ High concentration risk:</b> {hp}% of monitored
            suppliers show HIGH/CRITICAL risk. Diversification required.</div>""", unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        for w in sorted(wl, key=lambda x: TIER_ORDER.index(x["risk_tier"]) if x["risk_tier"] in TIER_ORDER else 99):
            t3 = w["risk_tier"]; b3 = TIER_BADGE.get(t3,"fs-badge-unknown")
            tariff_icon = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢","UNKNOWN":"⚪"}.get(w.get("tariff_exposure","UNKNOWN"),"⚪")
            st.markdown(f"""<div class='sc-row'>
                <div style='flex:1'>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>{w["company"]}</div>
                    <div style='font-size:10px;color:#6b7280;font-family:Space Mono,monospace;margin-top:2px'>
                        {w["scan_date"]} · {w["signal_count"]} signals</div>
                </div>
                <div class='{b3}'>{t3}</div>
                <div style='font-size:11px;color:#9ca3af;min-width:120px'>
                    {tariff_icon} Tariff: {w.get("tariff_exposure","UNKNOWN")}<br>
                    {"⚠️ ESG concern" if w.get("esg_violation") else "✅ ESG clean"}
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown('<span class="fs-label">Run watchlist check & alert</span>', unsafe_allow_html=True)
        if st.button("▶  Rescan All Now"):
            changes = []; updated = []; prog2 = st.progress(0)
            for i,w in enumerate(wl):
                prog2.progress((i+1)/len(wl), text=f"Rescanning {w['company']}…")
                res = run_scan_lightweight(w["company"], SERPER_KEY, GROQ_KEY)
                new_tier = res.get("risk_tier", w["risk_tier"])
                if new_tier != w["risk_tier"]:
                    changes.append({"company":w["company"],"old_tier":w["risk_tier"],"new_tier":new_tier})
                updated.append({**w,"prev_tier":w["risk_tier"],"risk_tier":new_tier,
                                 "signal_count":res.get("signal_count",w["signal_count"]),
                                 "scan_date":res.get("scan_date",w["scan_date"])})
            prog2.empty(); st.session_state.watchlist = updated
            if changes:
                for ch in changes:
                    oc2 = TIER_COLOR.get(ch["old_tier"],"#6b7280"); nc2 = TIER_COLOR.get(ch["new_tier"],"#6b7280")
                    st.markdown(f"""<div class='alert-r'><b>{ch["company"]}</b>:
                        <span style='color:{oc2}'>{ch["old_tier"]}</span> →
                        <span style='color:{nc2};font-weight:700'>{ch["new_tier"]}</span>
                    </div>""", unsafe_allow_html=True)
                alert_to = st.session_state.alert_email
                if alert_to and EMAIL_FROM and EMAIL_PASS:
                    ok, msg = send_email(alert_to, f"FailSignal — {len(changes)} tier change(s)",
                                         alert_body(changes, st.session_state.watchlist), EMAIL_FROM, EMAIL_PASS)
                    st.success(f"Email sent to {alert_to}") if ok else st.warning(f"Email failed: {msg}")
            else:
                st.success("✅ No risk tier changes detected.")

        st.markdown("---")
        ex1, ex2 = st.columns(2)
        with ex1:
            st.download_button("⬇️  Export JSON", json.dumps(st.session_state.watchlist, indent=2).encode(),
                               "failsignal_watchlist.json", "application/json")
        with ex2:
            up_wl = st.file_uploader("Import watchlist", type=["json"], key="wl_import", label_visibility="collapsed")
            if up_wl:
                try:
                    imp = json.load(up_wl)
                    if isinstance(imp, list):
                        st.session_state.watchlist = imp; st.success(f"Imported {len(imp)} companies."); st.rerun()
                except Exception as e: st.error(f"Import failed: {e}")
        if st.button("🗑  Clear watchlist"):
            st.session_state.watchlist = []; st.rerun()

# ──────────────────────────── TAB: LIBRARY ────────────────────────────────────
with t_library:
    st.markdown('<span class="fs-label">Failure library — verified sources only</span>', unsafe_allow_html=True)
    st.markdown("""<div class='fs-card'><div style='font-size:12px;color:#9ca3af;line-height:1.7'>
    All signals sourced from real articles, SEC EDGAR filings, and court documents.
    Every URL was fetched and confirmed. No fabricated data.</div></div>""", unsafe_allow_html=True)
    for co in sorted(lib["companies"], key=lambda x: x["failure_date"], reverse=True):
        with st.expander(f'{co["name"]}  ·  {co["failure_type"]}  ·  {co["failure_date"]}  ·  {len(co["signals"])} signals', expanded=False):
            st.markdown(f"""<div style='display:flex;gap:20px;flex-wrap:wrap;font-size:12px;margin-bottom:8px'>
                <span><b style='color:#9ca3af'>Industry</b> {co["industry"]}</span>
                <span><b style='color:#9ca3af'>Cause</b> {co["primary_cause"]}</span>
            </div>
            <div style='font-size:12px;color:#d1d5db;line-height:1.7;margin-bottom:8px'>{co["key_summary"]}</div>
            <div>{"".join([f"<span class=\'fs-tag\'>{t.replace(chr(95),' ')}</span>" for t in co["pattern_tags"]])}</div>""",
            unsafe_allow_html=True)
            for sig in sorted(co["signals"], key=lambda x: -x["months_before"]):
                st.markdown(f"""<div style='padding:7px 0;border-bottom:1px solid #1f2937;font-size:11px;color:#9ca3af'>
                    <span style='font-family:Space Mono,monospace;color:#4b5563;margin-right:10px'>T−{sig["months_before"]}mo</span>
                    {sig["signal_description"][:120]}…
                    <a href='{sig["source_url"]}' target='_blank' style='color:#3b82f6;margin-left:6px'>source</a>
                </div>""", unsafe_allow_html=True)
