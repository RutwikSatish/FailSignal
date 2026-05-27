# ============================================================
#  FailSignal v2 — Supplier Failure Early Warning Agent
#
#  New in v2:
#  1. Tariff & Geopolitical Risk Layer — country exposure card in results
#  2. ESG Red Flag Scanner — ESG violation card in results
#  3. Supplier Sector Scorecard — new tab, category dropdown, bulk scan
#  4. Single-Source Concentration Score — portfolio risk in watchlist
#  5. Watchlist Alerts — rescan all + email report + CSV export
#
#  secrets.toml:
#    SERPER_API_KEY   = "..."
#    GROQ_API_KEY     = "..."
#    ALERT_EMAIL_FROM = "yourapp@gmail.com"         # optional
#    ALERT_EMAIL_PASS = "xxxx xxxx xxxx xxxx"       # Gmail app password
# ============================================================
import streamlit as st
import json, smtplib, csv, io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from scanner import run_scan, run_scan_lightweight, discover_sector_suppliers
from library_loader import load_library, build_context, match_patterns

st.set_page_config(page_title="FailSignal", page_icon="🔴", layout="wide",
                   initial_sidebar_state="expanded")

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');
html,body,[class*="css"]{background:#08090f !important;color:#f9fafb !important;font-family:'DM Sans',sans-serif}
section[data-testid="stSidebar"]{background:#0d1117 !important;border-right:1px solid #1f2937 !important}
section[data-testid="stSidebar"] *{color:#f9fafb !important}
div[data-testid="metric-container"]{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px 18px !important}
div[data-testid="metric-container"] label{color:#6b7280 !important;font-size:11px !important}
div[data-testid="metric-container"] [data-testid="stMetricValue"]{color:#f9fafb !important;font-family:'Space Mono',monospace !important;font-size:1.4rem !important}
button[data-baseweb="tab"]{background:transparent !important;color:#6b7280 !important;font-family:'Space Mono',monospace !important;font-size:10px !important;border-bottom:2px solid transparent !important;letter-spacing:.3px}
button[data-baseweb="tab"][aria-selected="true"]{color:#ef4444 !important;border-bottom:2px solid #ef4444 !important}
.stButton>button{background:#1d4ed8 !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-size:13px !important}
.stButton>button:hover{background:#2563eb !important}
.stTextInput>div>div>input{background:#111827 !important;border:1px solid #374151 !important;color:#f9fafb !important;border-radius:8px !important;font-size:14px !important}
.stSelectbox>div>div{background:#111827 !important;border:1px solid #374151 !important;color:#f9fafb !important;border-radius:8px !important}
.fs-card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:16px 20px;margin-bottom:12px}
.fs-label{font-family:'Space Mono',monospace;font-size:10px;color:#ef4444;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px}
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
.sc-row{display:flex;align-items:center;gap:16px;padding:12px 16px;background:#111827;border:1px solid #1f2937;border-radius:8px;margin-bottom:6px}
</style>
""", unsafe_allow_html=True)

# ── Sector categories ─────────────────────────────────────────────────────────
SECTORS = {
    "Automotive Components":    "automotive parts components stampings castings",
    "Electronic Components":    "electronic components PCB semiconductor passive",
    "Packaging Materials":      "industrial packaging corrugated flexible containers",
    "Chemical Intermediates":   "chemical intermediates raw materials specialty chemicals",
    "Logistics & Freight":      "freight forwarding logistics carriers trucking shipping",
    "Medical Devices":          "medical device surgical instruments diagnostics equipment",
    "Aerospace Parts":          "aerospace components machined parts fasteners structures",
    "Food Ingredients":         "food ingredients flavors additives raw materials processing",
    "Textiles & Apparel":       "textile fabric yarn apparel cut-and-sew manufacturing",
    "IT Hardware & OEM":        "IT hardware server storage networking OEM components",
}

# ── Load library ──────────────────────────────────────────────────────────────
@st.cache_resource
def get_library():
    return load_library("failure_library.json")

lib         = get_library()
lib_context = build_context(lib)

def _key(k): 
    try:    return st.secrets[k]
    except: return ""

SERPER_KEY = _key("SERPER_API_KEY")
GROQ_KEY   = _key("GROQ_API_KEY")
EMAIL_FROM = _key("ALERT_EMAIL_FROM")
EMAIL_PASS = _key("ALERT_EMAIL_PASS")

# ── Session state ─────────────────────────────────────────────────────────────
for k, v in [("result", None), ("watchlist", []), ("scorecard_results", []),
             ("scorecard_category", ""), ("alert_email", "")]:
    if k not in st.session_state:
        st.session_state[k] = v

# ── Email helper ──────────────────────────────────────────────────────────────
def send_alert_email(to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    if not EMAIL_FROM or not EMAIL_PASS:
        return False, "Email not configured in secrets.toml"
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = EMAIL_FROM
        msg["To"]      = to_addr
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASS)
            server.sendmail(EMAIL_FROM, to_addr, msg.as_string())
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)


def build_alert_email_body(changes: list, watchlist: list) -> str:
    lines = ["FailSignal Watchlist Report", "=" * 40, ""]
    if changes:
        lines.append("⚠️  RISK TIER CHANGES DETECTED:")
        for c in changes:
            lines.append(f"  {c['company']}: {c['old_tier']} → {c['new_tier']}")
        lines.append("")
    lines.append("FULL WATCHLIST STATUS:")
    for w in watchlist:
        lines.append(f"  {w['company']:30s} {w['risk_tier']:10s} ({w['signal_count']} signals)")
    lines += ["", "─" * 40, "Sent by FailSignal · Supplier Failure Early Warning"]
    return "\n".join(lines)


# ── Tier helpers ──────────────────────────────────────────────────────────────
TIER_ORDER = ["CRITICAL","HIGH","MEDIUM","LOW","UNKNOWN"]
TIER_COLOR = {"CRITICAL":"#ef4444","HIGH":"#f97316","MEDIUM":"#f59e0b",
              "LOW":"#10b981","UNKNOWN":"#6b7280"}
TIER_BADGE = {"CRITICAL":"fs-badge-critical","HIGH":"fs-badge-high",
              "MEDIUM":"fs-badge-medium","LOW":"fs-badge-low","UNKNOWN":"fs-badge-unknown"}

def tier_html(tier):
    bc = TIER_BADGE.get(tier, "fs-badge-unknown")
    return f'<span class="{bc}">{tier}</span>'


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:14px 0 10px'>
        <div style='font-family:Space Mono,monospace;font-size:18px;font-weight:700;
                    color:#ef4444;letter-spacing:-1px;'>🔴 FailSignal</div>
        <div style='font-size:10px;color:#6b7280;letter-spacing:3px;text-transform:uppercase;margin-top:3px'>
            v2 · Supplier Early Warning
        </div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")

    if SERPER_KEY and GROQ_KEY:
        st.markdown('<div style="font-size:12px;color:#10b981">✅ APIs ready</div>',
                    unsafe_allow_html=True)
    else:
        missing = [k for k,v in [("SERPER_API_KEY",SERPER_KEY),("GROQ_API_KEY",GROQ_KEY)] if not v]
        st.markdown(f'<div style="font-size:11px;color:#f59e0b">⚠️ Missing: {", ".join(missing)}</div>',
                    unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("**Alert Email**")
    st.session_state.alert_email = st.text_input(
        "Email for watchlist alerts", value=st.session_state.alert_email,
        placeholder="you@email.com", label_visibility="collapsed", key="alert_input")

    if EMAIL_FROM:
        st.caption(f"Sending from: {EMAIL_FROM}")
    else:
        st.caption("Add ALERT_EMAIL_FROM + ALERT_EMAIL_PASS to secrets.toml to enable email")

    st.markdown("---")
    st.markdown(f"""<div style='font-size:11px;color:#6b7280;line-height:1.9'>
        <b style='color:#f9fafb'>Library v{lib["version"]}</b><br>
        {lib["total_companies"]} companies · {lib["total_signals"]} signals<br>
        {lib["total_pattern_tags"]} pattern tags<br>
        <span style='color:#374151'>Verified sources only</span></div>""",
        unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div style="font-size:10px;color:#374151;line-height:1.7">Rutwik Satish<br>MS Eng. Mgmt · Northeastern<br>Serper + Groq + Failure Library</div>',
                unsafe_allow_html=True)

# ── Hero ──────────────────────────────────────────────────────────────────────
st.markdown("""
<div class='fs-hero'>
    <div style='font-size:22px;font-weight:700;letter-spacing:-.5px;margin-bottom:4px'>
        🔴 Fail<span style='color:#ef4444'>Signal</span> <span style='font-size:14px;color:#374151;font-weight:400'>v2</span>
    </div>
    <div style='font-size:12px;color:#6b7280;margin-bottom:10px'>
        Failure risk · Tariff exposure · ESG flags · Sector scorecard · Watchlist monitoring
    </div>
    <div style='display:flex;flex-wrap:wrap;gap:6px'>
        <span style='font-size:11px;background:#ef444415;color:#f87171;border:1px solid #ef444430;
                     border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>
            Financial distress</span>
        <span style='font-size:11px;background:#3b82f615;color:#93c5fd;border:1px solid #3b82f630;
                     border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>
            Tariff/geo risk</span>
        <span style='font-size:11px;background:#10b98115;color:#34d399;border:1px solid #10b98130;
                     border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>
            ESG flags</span>
        <span style='font-size:11px;background:#f59e0b15;color:#fbbf24;border:1px solid #f59e0b30;
                     border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>
            Sector scorecard</span>
        <span style='font-size:11px;background:#8b5cf615;color:#c4b5fd;border:1px solid #8b5cf630;
                     border-radius:4px;padding:2px 9px;font-family:Space Mono,monospace'>
            Monitoring alerts</span>
    </div>
</div>""", unsafe_allow_html=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_scan, tab_result, tab_scorecard, tab_backtest, tab_watchlist, tab_library = st.tabs([
    "🔍  Scan",
    "📋  Result",
    "🏭  Sector Scorecard",
    "🔬  Back-test",
    "👁  Watchlist",
    "📚  Library",
])


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — SCAN
# ═════════════════════════════════════════════════════════════════════════════
with tab_scan:
    st.markdown('<p class="fs-label">Enter supplier or company name</p>', unsafe_allow_html=True)
    c_in, c_btn = st.columns([3,1])
    with c_in:
        company_input = st.text_input("Company", label_visibility="collapsed",
                                       placeholder="e.g. Acme Parts Co. · Spirit Airlines",
                                       key="scan_input")
    with c_btn:
        scan_btn = st.button("Scan →", use_container_width=True,
                              disabled=(not SERPER_KEY or not GROQ_KEY))

    if not SERPER_KEY or not GROQ_KEY:
        st.markdown("""<div class='alert-y'>
        <b>Setup required</b><br>
        Add to <code>.streamlit/secrets.toml</code>:<br>
        <code>SERPER_API_KEY = "..."</code><br>
        <code>GROQ_API_KEY   = "..."</code>
        </div>""", unsafe_allow_html=True)

    if scan_btn and company_input.strip():
        with st.spinner(f"Scanning {company_input.strip()} — 6 queries across news, geo, ESG…"):
            result = run_scan(company_input.strip(), SERPER_KEY, GROQ_KEY, lib_context)
            st.session_state.result = result

            existing = [w["company"].lower() for w in st.session_state.watchlist]
            if result["company"].lower() not in existing:
                st.session_state.watchlist.append({
                    "company":      result["company"],
                    "risk_tier":    result["risk_tier"],
                    "scan_date":    result["scan_date"],
                    "signal_count": result["signal_count"],
                    "prev_tier":    result["risk_tier"],
                    "tariff_exposure": result.get("tariff_risk",{}).get("exposure","UNKNOWN"),
                    "esg_violation":   result.get("esg_flags",{}).get("violations_found", False),
                })

        tier = result["risk_tier"]
        c    = TIER_COLOR.get(tier,"#6b7280")
        st.markdown(f"""
        <div style='background:{c}15;border:1px solid {c}40;border-left:4px solid {c};
                    border-radius:0 10px 10px 0;padding:14px 18px;margin-top:10px'>
            <span style='font-family:Space Mono,monospace;font-size:13px;font-weight:700;color:{c}'>
                {tier} RISK — {result["signal_count"]} signals</span><br>
            <span style='font-size:12px;color:#9ca3af'>{result.get("summary","")}</span>
        </div>""", unsafe_allow_html=True)
        st.caption("→ Full report in **Result** tab · Added to **Watchlist**")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<p class="fs-label">6 sources per scan</p>', unsafe_allow_html=True)
    cols = st.columns(3)
    sources_info = [
        ("💸 Financial distress", "Restructuring · default · going concern · bankruptcy"),
        ("👤 Leadership signals", "CEO/CFO/COO departures · replacements"),
        ("🚚 Vendor signals",     "Supplier refusals · cash-on-delivery demands"),
        ("👷 Employee signals",   "Layoffs · fraud · regulatory investigations"),
        ("🌍 Geo/Tariff risk",    "Manufacturing country · active tariff exposure"),
        ("🌿 ESG flags",          "Labor violations · environmental fines · sanctions"),
    ]
    for i, (title, desc) in enumerate(sources_info):
        with cols[i % 3]:
            st.markdown(f"""<div class='fs-card' style='margin-bottom:8px'>
                <div style='font-size:12px;font-weight:600;color:#f9fafb;margin-bottom:3px'>{title}</div>
                <div style='font-size:11px;color:#6b7280'>{desc}</div>
            </div>""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — RESULT
# ═════════════════════════════════════════════════════════════════════════════
with tab_result:
    r = st.session_state.result
    if r is None:
        st.markdown('<div class="fs-card" style="text-align:center;padding:36px">'
                    '<div style="font-size:13px;color:#6b7280">Run a scan first</div></div>',
                    unsafe_allow_html=True)
    else:
        tier = r.get("risk_tier","LOW")
        c    = TIER_COLOR.get(tier,"#6b7280")
        bc   = TIER_BADGE.get(tier,"fs-badge-low")

        h1, h2 = st.columns([2,1])
        with h1:
            st.markdown(f"""
            <div style='font-size:21px;font-weight:700;color:#f9fafb;margin-bottom:3px'>{r["company"]}</div>
            <div style='font-size:12px;color:#6b7280'>Scanned {r.get("scan_date","today")} ·
                Data: {r.get("data_quality","N/A")}</div>""", unsafe_allow_html=True)
        with h2:
            st.markdown(f'<div class="{bc}">{tier} RISK<br>'
                        f'<span style="font-size:11px;font-weight:400">{r["signal_count"]}/10 signals</span></div>',
                        unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Assessment ──
        alert_style = "r" if tier in ("CRITICAL","HIGH") else "y" if tier=="MEDIUM" else "g"
        st.markdown(f"""<div class='alert-{alert_style}'>
            <div class='fs-label'>Assessment</div>
            <div style='font-size:13px;color:#f9fafb;line-height:1.7'>{r.get("summary","")}</div>
        </div>""", unsafe_allow_html=True)

        # ── Pattern tags ──
        detected = r.get("detected_tags",[])
        all_tags = ["c_suite_exodus","going_concern_language","debt_language_spike",
                    "vendor_payment_failure","insider_stock_sales","fraud_transparency",
                    "employee_sentiment_collapse","merger_rescue_blocked",
                    "asset_strip_related_party","operational_shortfall"]

        st.markdown('<p class="fs-label" style="margin-top:16px">Distress pattern tags</p>',
                    unsafe_allow_html=True)
        tag_html = ""
        for t in all_tags:
            if t in detected:
                tag_html += f'<span class="fs-tag">{t.replace("_"," ")}</span>'
            else:
                tag_html += (f'<span style="display:inline-block;background:#1f293760;color:#374151;'
                             f'border:1px solid #1f2937;border-radius:5px;padding:2px 9px;'
                             f'font-size:11px;font-family:Space Mono,monospace;margin:2px">'
                             f'{t.replace("_"," ")}</span>')
        st.markdown(f'<div>{tag_html}</div>', unsafe_allow_html=True)

        # ── NEW: Tariff / Geo Risk card ──
        tr = r.get("tariff_risk", {})
        tr_exp     = tr.get("exposure","UNKNOWN")
        tr_country = tr.get("country","Unknown")
        tr_color   = {"HIGH":"#ef4444","MEDIUM":"#f59e0b","LOW":"#10b981","UNKNOWN":"#6b7280"}.get(tr_exp,"#6b7280")
        tr_badge   = {"HIGH":"fs-badge-critical","MEDIUM":"fs-badge-medium",
                      "LOW":"fs-badge-low","UNKNOWN":"fs-badge-unknown"}.get(tr_exp,"fs-badge-unknown")

        st.markdown('<p class="fs-label" style="margin-top:16px">🌍 Tariff & Geopolitical Risk</p>',
                    unsafe_allow_html=True)
        st.markdown(f"""
        <div class='fs-card' style='border-left:3px solid {tr_color}'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>
                <div>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>Country of Operations</div>
                    <div style='font-size:13px;color:#9ca3af;margin-top:2px'>{tr_country}</div>
                </div>
                <div class='{tr_badge}'>{tr_exp} EXPOSURE</div>
            </div>
            <div style='font-size:12px;color:#6b7280;line-height:1.6'>
                <b style='color:#9ca3af'>Active tariffs:</b> {tr.get("active_tariffs","N/A")}<br>
                <b style='color:#9ca3af'>Evidence:</b> {tr.get("evidence","N/A")[:200]}
            </div>
        </div>""", unsafe_allow_html=True)

        # ── NEW: ESG Flags card ──
        esg         = r.get("esg_flags", {})
        esg_found   = esg.get("violations_found", False)
        esg_sev     = esg.get("severity","None")
        esg_color   = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981","None":"#10b981"}.get(esg_sev,"#10b981")
        esg_badge   = {"High":"fs-badge-critical","Medium":"fs-badge-medium",
                       "Low":"fs-badge-low","None":"fs-badge-low"}.get(esg_sev,"fs-badge-low")

        st.markdown('<p class="fs-label" style="margin-top:4px">🌿 ESG Flags</p>',
                    unsafe_allow_html=True)
        cats     = esg.get("categories",[])
        cats_html = " ".join([f'<span class="fs-tag-amber">{c}</span>' for c in cats]) if cats else ""
        st.markdown(f"""
        <div class='fs-card' style='border-left:3px solid {esg_color}'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>
                <div>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>
                        {"⚠️ ESG Violations Found" if esg_found else "✅ No ESG Violations Found"}</div>
                    <div style='margin-top:4px'>{cats_html}</div>
                </div>
                <div class='{esg_badge}'>{esg_sev.upper()}</div>
            </div>
            <div style='font-size:12px;color:#6b7280;line-height:1.6'>
                {esg.get("evidence","No violations detected in recent public news.")[:250]}
            </div>
        </div>""", unsafe_allow_html=True)

        # ── Evidence ──
        evidence = r.get("evidence",{})
        if evidence:
            st.markdown('<p class="fs-label" style="margin-top:4px">Evidence from search results</p>',
                        unsafe_allow_html=True)
            for tag_id, text in evidence.items():
                st.markdown(f"""
                <div class='fs-card' style='border-left:3px solid {c}'>
                    <div style='font-size:10px;font-family:Space Mono,monospace;color:{c};margin-bottom:5px'>
                        {tag_id.replace("_"," ").upper()}</div>
                    <div style='font-size:12px;color:#d1d5db;line-height:1.6'>{text}</div>
                </div>""", unsafe_allow_html=True)

        # ── Raw signals ──
        for sig in r.get("raw_signals",[]):
            sev   = sig.get("severity","Medium")
            sc    = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sev,"#6b7280")
            url   = sig.get("source_url","")
            upart = f'<a href="{url}" target="_blank" style="color:#3b82f6;font-size:11px">{url[:55]}…</a>' if url else ""
            st.markdown(f"""
            <div style='display:flex;gap:10px;padding:9px 12px;background:#0d1117;
                        border-radius:8px;margin-bottom:5px;align-items:flex-start'>
                <span style='color:{sc};font-size:10px;font-family:Space Mono,monospace;
                             min-width:44px;margin-top:2px'>{sev}</span>
                <div>
                    <div style='font-size:12px;color:#f9fafb;line-height:1.5'>{sig.get("description","")}</div>
                    {upart}
                </div>
            </div>""", unsafe_allow_html=True)

        # ── Comparable failures ──
        if detected:
            comparables = match_patterns(lib, detected, top_n=2)
            if comparables:
                st.markdown('<p class="fs-label" style="margin-top:4px">Comparable historical failures</p>',
                            unsafe_allow_html=True)
                for m in comparables:
                    co      = m["company"]
                    overlap = m["overlap_tags"]
                    score   = m["score"]
                    st.markdown(f"""
                    <div class='fs-card' style='border-left:3px solid #ef4444'>
                        <div style='display:flex;justify-content:space-between'>
                            <div>
                                <div style='font-size:14px;font-weight:600;color:#f87171'>{co["name"]}</div>
                                <div style='font-size:11px;color:#6b7280'>{co["failure_type"]} · {co["failure_date"]} · {co["industry"]}</div>
                            </div>
                            <div style='font-family:Space Mono,monospace;font-size:13px;color:#f87171;font-weight:700'>{score:.0%}</div>
                        </div>
                        <div style='font-size:12px;color:#9ca3af;margin-top:7px;line-height:1.5'>{co["key_summary"][:200]}…</div>
                        <div style='margin-top:7px'>{"".join([f"<span class=\'fs-tag\'>{t.replace(chr(95),' ')}</span>" for t in overlap])}</div>
                    </div>""", unsafe_allow_html=True)

        # ── Actions ──
        actions = r.get("recommended_actions",[])
        if actions:
            st.markdown('<p class="fs-label" style="margin-top:4px">Recommended actions</p>',
                        unsafe_allow_html=True)
            for i, a in enumerate(actions, 1):
                st.markdown(f"""
                <div style='display:flex;gap:10px;padding:9px 12px;background:#111827;
                            border-radius:8px;margin-bottom:5px'>
                    <span style='font-family:Space Mono,monospace;font-size:11px;
                                 color:#6b7280;min-width:18px'>{i}</span>
                    <span style='font-size:12px;color:#f9fafb'>{a}</span>
                </div>""", unsafe_allow_html=True)

        # ── Sources ──
        sources = r.get("sources_searched",[])
        if sources:
            with st.expander(f"Sources searched ({len(sources)})", expanded=False):
                for s in sources:
                    st.markdown(f'<a href="{s}" target="_blank" style="font-size:11px;color:#3b82f6">{s}</a><br>',
                                unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — SECTOR SCORECARD (NEW)
# ═════════════════════════════════════════════════════════════════════════════
with tab_scorecard:
    st.markdown('<p class="fs-label">Select a sector to identify and risk-score key suppliers</p>',
                unsafe_allow_html=True)

    sel_sector = st.selectbox("Sector", list(SECTORS.keys()), key="sector_select")

    c1, c2 = st.columns([2,1])
    with c1:
        st.markdown(f"""
        <div class='alert-b' style='margin-top:4px'>
            <div style='font-size:12px;color:#93c5fd;line-height:1.6'>
                <b>What this does:</b> Discovers the major players in this sector via live search,
                then runs a lightweight FailSignal scan on each. Results show which suppliers
                in this space currently show distress signals — before you commit to them.
            </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        run_scorecard = st.button("Run Sector Scan →", use_container_width=True,
                                   disabled=(not SERPER_KEY or not GROQ_KEY))

    if run_scorecard:
        search_term = SECTORS[sel_sector]
        st.session_state.scorecard_category = sel_sector
        st.session_state.scorecard_results  = []

        with st.spinner(f"Discovering {sel_sector} suppliers…"):
            companies = discover_sector_suppliers(sel_sector, search_term, SERPER_KEY, GROQ_KEY)

        if not companies:
            st.warning("Could not identify suppliers for this sector. Try again or choose another category.")
        else:
            st.markdown(f'<div style="font-size:12px;color:#6b7280;margin:8px 0">'
                        f'Found {len(companies)} companies — scanning each…</div>',
                        unsafe_allow_html=True)
            progress = st.progress(0)
            results  = []
            for i, co in enumerate(companies):
                progress.progress((i+1)/len(companies), text=f"Scanning {co}…")
                res = run_scan_lightweight(co, SERPER_KEY, GROQ_KEY)
                results.append(res)
            progress.empty()
            st.session_state.scorecard_results = results

    # ── Display results ──
    sc_results = st.session_state.scorecard_results
    if sc_results:
        st.markdown(f"""
        <div style='font-size:16px;font-weight:600;color:#f9fafb;margin:16px 0 8px'>
            {st.session_state.scorecard_category} — {len(sc_results)} suppliers scanned
        </div>""", unsafe_allow_html=True)

        # Summary counts
        tier_counts = {t:0 for t in TIER_ORDER}
        for r in sc_results:
            tier_counts[r.get("risk_tier","UNKNOWN")] = tier_counts.get(r.get("risk_tier","UNKNOWN"),0) + 1

        mc = st.columns(5)
        for i, t in enumerate(TIER_ORDER):
            mc[i].metric(t, tier_counts.get(t,0))

        st.markdown("<br>", unsafe_allow_html=True)

        # Company cards sorted by risk
        sorted_results = sorted(sc_results,
            key=lambda x: TIER_ORDER.index(x.get("risk_tier","UNKNOWN"))
                          if x.get("risk_tier","UNKNOWN") in TIER_ORDER else 99)

        for res in sorted_results:
            tier  = res.get("risk_tier","UNKNOWN")
            c_col = TIER_COLOR.get(tier,"#6b7280")
            badge = TIER_BADGE.get(tier,"fs-badge-unknown")
            concern = res.get("key_concern","None")
            dq    = res.get("data_quality","N/A")

            st.markdown(f"""
            <div class='sc-row'>
                <div style='flex:1'>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>{res["company"]}</div>
                    <div style='font-size:11px;color:#6b7280;margin-top:2px'>{res.get("summary","")[:120]}</div>
                </div>
                <div style='text-align:center;min-width:80px'>
                    <div class='{badge}'>{tier}</div>
                    <div style='font-size:10px;color:#6b7280;margin-top:3px;font-family:Space Mono,monospace'>
                        {res.get("signal_count",0)} signals
                    </div>
                </div>
                <div style='min-width:180px;font-size:11px;color:#9ca3af;line-height:1.5'>
                    <b style='color:#6b7280'>Key concern:</b><br>
                    {concern if concern and concern != "None" else "—"}
                </div>
                <div style='min-width:80px;text-align:center'>
                    <div style='font-size:10px;color:#4b5563;font-family:Space Mono,monospace'>
                        DATA<br>{dq.upper() if dq else "N/A"}
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)

        # Insight
        high_risk = [r["company"] for r in sc_results if r.get("risk_tier") in ("CRITICAL","HIGH")]
        safe      = [r["company"] for r in sc_results if r.get("risk_tier") == "LOW"]

        if high_risk or safe:
            st.markdown('<p class="fs-label" style="margin-top:16px">Sector Intelligence</p>',
                        unsafe_allow_html=True)
            if high_risk:
                st.markdown(f"""<div class='alert-r'>
                    <b>⚠️ Elevated risk detected:</b> {", ".join(high_risk)}<br>
                    <span style='font-size:12px'>These suppliers show public distress signals.
                    Exercise caution before awarding new contracts.</span>
                </div>""", unsafe_allow_html=True)
            if safe:
                st.markdown(f"""<div class='alert-g'>
                    <b>✅ Lower risk alternatives:</b> {", ".join(safe)}<br>
                    <span style='font-size:12px'>No distress signals found in recent public news.
                    Consider prioritising for new or expanded contracts.</span>
                </div>""", unsafe_allow_html=True)

        # Export
        csv_buf = io.StringIO()
        writer  = csv.DictWriter(csv_buf, fieldnames=["company","risk_tier","signal_count","summary","key_concern","data_quality"])
        writer.writeheader()
        for res in sorted_results:
            writer.writerow({
                "company":      res.get("company",""),
                "risk_tier":    res.get("risk_tier",""),
                "signal_count": res.get("signal_count",0),
                "summary":      res.get("summary",""),
                "key_concern":  res.get("key_concern",""),
                "data_quality": res.get("data_quality",""),
            })
        st.download_button("⬇️  Export scorecard CSV",
                           csv_buf.getvalue().encode(),
                           f"failsignal_scorecard_{sel_sector.replace(' ','_')}.csv",
                           "text/csv")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — BACK-TEST
# ═════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.markdown('<p class="fs-label">Back-test: Bed Bath & Beyond — 18 months before collapse</p>',
                unsafe_allow_html=True)
    st.markdown("""<div class='alert-y'>
        <div class='fs-label'>What this shows</div>
        <div style='font-size:12px;color:#f9fafb;line-height:1.7'>
            BBBY filed Chapter 11 April 23 2023.
            All signals below were publicly available from October 2021 — 18 months before collapse.
            This is exactly what a FailSignal scan in October 2021 would have surfaced.
        </div>
    </div>""", unsafe_allow_html=True)

    bbby = next((c for c in lib["companies"] if c["id"]=="bbby"), None)
    if bbby:
        st.markdown(f"""
        <div style='display:flex;justify-content:space-between;align-items:center;margin:12px 0'>
            <div>
                <div style='font-size:18px;font-weight:700;color:#f9fafb'>{bbby["name"]}</div>
                <div style='font-size:12px;color:#6b7280'>{bbby["failure_type"]} · {bbby["failure_date"]} · {bbby["industry"]}</div>
            </div>
            <div class='fs-badge-critical'>HIGH RISK<br>
                <span style='font-size:10px;font-weight:400'>{len(bbby["pattern_tags"])}/10 signals</span>
            </div>
        </div>""", unsafe_allow_html=True)

        tags_html = "".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' for t in bbby["pattern_tags"]])
        st.markdown(f'<div style="margin-bottom:14px">{tags_html}</div>', unsafe_allow_html=True)

        for sig in sorted(bbby["signals"], key=lambda x: -x["months_before"]):
            sc = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sig["severity"],"#6b7280")
            st.markdown(f"""
            <div class='fs-card' style='border-left:3px solid {sc};margin-bottom:7px'>
                <div style='display:flex;gap:14px'>
                    <div style='font-family:Space Mono,monospace;font-size:11px;color:{sc};min-width:60px;margin-top:1px'>T−{sig["months_before"]}mo</div>
                    <div>
                        <div style='font-size:12px;color:#f9fafb;line-height:1.5'>{sig["signal_description"]}</div>
                        <a href='{sig["source_url"]}' target='_blank' style='font-size:11px;color:#3b82f6'>source →</a>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — WATCHLIST
# ═════════════════════════════════════════════════════════════════════════════
with tab_watchlist:
    wl = st.session_state.watchlist

    if not wl:
        st.markdown('<div class="fs-card" style="text-align:center;padding:36px">'
                    '<div style="font-size:13px;color:#6b7280">Run a scan — it auto-adds here.</div>'
                    '</div>', unsafe_allow_html=True)
    else:
        # ── Metrics ──
        m1,m2,m3,m4 = st.columns(4)
        m1.metric("Monitored", len(wl))
        high_risk = sum(1 for w in wl if w["risk_tier"] in ("CRITICAL","HIGH"))
        m2.metric("High/Critical", high_risk)

        # ── NEW: Single-source concentration score ──
        high_pct = round(high_risk / len(wl) * 100) if wl else 0
        conc_color = "🔴" if high_pct >= 40 else "🟡" if high_pct >= 20 else "🟢"
        m3.metric("Portfolio Risk", f"{high_pct}%", f"{conc_color} concentration")

        tariff_exposed = sum(1 for w in wl if w.get("tariff_exposure") in ("HIGH","MEDIUM"))
        m4.metric("Tariff Exposed", tariff_exposed)

        if high_pct >= 40:
            st.markdown(f"""<div class='alert-r'>
                <b>⚠️ High portfolio concentration risk:</b> {high_pct}% of monitored suppliers 
                show HIGH or CRITICAL risk. Diversification action required.
            </div>""", unsafe_allow_html=True)

        # ── Company list ──
        st.markdown("<br>", unsafe_allow_html=True)
        for w in sorted(wl, key=lambda x: TIER_ORDER.index(x["risk_tier"])
                         if x["risk_tier"] in TIER_ORDER else 99):
            tier  = w["risk_tier"]
            c_col = TIER_COLOR.get(tier,"#6b7280")
            badge = TIER_BADGE.get(tier,"fs-badge-unknown")
            tariff_exp = w.get("tariff_exposure","UNKNOWN")
            esg_flag   = w.get("esg_violation", False)
            tariff_icon = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢","UNKNOWN":"⚪"}.get(tariff_exp,"⚪")

            st.markdown(f"""
            <div class='sc-row'>
                <div style='flex:1'>
                    <div style='font-size:14px;font-weight:600;color:#f9fafb'>{w["company"]}</div>
                    <div style='font-size:10px;color:#6b7280;font-family:Space Mono,monospace;margin-top:2px'>
                        Scanned {w["scan_date"]} · {w["signal_count"]} signals
                    </div>
                </div>
                <div class='{badge}'>{tier}</div>
                <div style='font-size:11px;color:#9ca3af;min-width:120px'>
                    {tariff_icon} Tariff: {tariff_exp}<br>
                    {"⚠️ ESG concern" if esg_flag else "✅ ESG clean"}
                </div>
            </div>""", unsafe_allow_html=True)

        # ── Watchlist check + alerts ──
        st.markdown("---")
        st.markdown('<p class="fs-label">Run watchlist check</p>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:12px;color:#6b7280;margin-bottom:10px">'
                    'Rescans all companies and shows risk tier changes. '
                    'Sends email if configured.</div>', unsafe_allow_html=True)

        run_check = st.button("▶  Run Watchlist Check Now", use_container_width=False)

        if run_check:
            changes = []
            updated = []
            progress = st.progress(0)
            for i, w in enumerate(wl):
                progress.progress((i+1)/len(wl), text=f"Rescanning {w['company']}…")
                res = run_scan_lightweight(w["company"], SERPER_KEY, GROQ_KEY)
                new_tier = res.get("risk_tier", w["risk_tier"])
                if new_tier != w["risk_tier"]:
                    changes.append({"company": w["company"],
                                    "old_tier": w["risk_tier"],
                                    "new_tier": new_tier})
                updated.append({**w, "prev_tier": w["risk_tier"],
                                 "risk_tier": new_tier,
                                 "signal_count": res.get("signal_count", w["signal_count"]),
                                 "scan_date": res.get("scan_date", w["scan_date"])})
            progress.empty()
            st.session_state.watchlist = updated

            if changes:
                st.markdown('<p class="fs-label">Risk tier changes detected</p>', unsafe_allow_html=True)
                for ch in changes:
                    oc = TIER_COLOR.get(ch["old_tier"],"#6b7280")
                    nc = TIER_COLOR.get(ch["new_tier"],"#6b7280")
                    st.markdown(f"""
                    <div class='alert-r'>
                        <b>{ch["company"]}</b>:
                        <span style='color:{oc}'>{ch["old_tier"]}</span> →
                        <span style='color:{nc};font-weight:700'>{ch["new_tier"]}</span>
                    </div>""", unsafe_allow_html=True)

                # Send alert email if configured
                alert_to = st.session_state.alert_email
                if alert_to and EMAIL_FROM:
                    body    = build_alert_email_body(changes, st.session_state.watchlist)
                    ok, msg = send_alert_email(
                        alert_to,
                        f"FailSignal Alert — {len(changes)} risk tier change(s) detected",
                        body)
                    if ok:
                        st.success(f"Alert email sent to {alert_to}")
                    else:
                        st.warning(f"Email failed: {msg}")
                elif alert_to and not EMAIL_FROM:
                    st.info("Enter ALERT_EMAIL_FROM + ALERT_EMAIL_PASS in secrets.toml to enable email.")
            else:
                st.success("✅ No risk tier changes detected across all monitored suppliers.")

        # ── Export / Import ──
        st.markdown("---")
        col_ex, col_im = st.columns(2)
        with col_ex:
            wl_export = json.dumps(st.session_state.watchlist, indent=2)
            st.download_button("⬇️  Export watchlist JSON", wl_export.encode(),
                               "failsignal_watchlist.json", "application/json")
        with col_im:
            uploaded_wl = st.file_uploader("Import watchlist JSON", type=["json"],
                                            key="wl_import", label_visibility="collapsed")
            if uploaded_wl:
                try:
                    imported = json.load(uploaded_wl)
                    if isinstance(imported, list):
                        st.session_state.watchlist = imported
                        st.success(f"Imported {len(imported)} companies.")
                        st.rerun()
                except Exception as e:
                    st.error(f"Import failed: {e}")

        if st.button("🗑  Clear watchlist"):
            st.session_state.watchlist = []
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 6 — LIBRARY
# ═════════════════════════════════════════════════════════════════════════════
with tab_library:
    st.markdown('<p class="fs-label">Failure library — verified sources only</p>', unsafe_allow_html=True)
    st.markdown("""<div class='fs-card'>
        <div style='font-size:12px;color:#9ca3af;line-height:1.7'>
            All signals sourced from real articles, SEC EDGAR filings, and court documents.
            Every URL was fetched and confirmed. No fabricated or inferred data.
        </div>
    </div>""", unsafe_allow_html=True)

    for co in sorted(lib["companies"], key=lambda x: x["failure_date"], reverse=True):
        with st.expander(
            f'{co["name"]}  ·  {co["failure_type"]}  ·  {co["failure_date"]}  ·  {len(co["signals"])} signals',
            expanded=False):
            st.markdown(f"""
            <div style='display:flex;gap:20px;flex-wrap:wrap;font-size:12px;margin-bottom:8px'>
                <span><b style='color:#9ca3af'>Industry</b> {co["industry"]}</span>
                <span><b style='color:#9ca3af'>Cause</b> {co["primary_cause"]}</span>
            </div>
            <div style='font-size:12px;color:#d1d5db;line-height:1.7;margin-bottom:8px'>{co["key_summary"]}</div>
            """, unsafe_allow_html=True)
            tags_html = "".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' for t in co["pattern_tags"]])
            st.markdown(f'<div style="margin-bottom:8px">{tags_html}</div>', unsafe_allow_html=True)
            for sig in sorted(co["signals"], key=lambda x: -x["months_before"]):
                st.markdown(f"""
                <div style='padding:7px 0;border-bottom:1px solid #1f2937;font-size:11px;color:#9ca3af'>
                    <span style='font-family:Space Mono,monospace;color:#4b5563;margin-right:10px'>T−{sig["months_before"]}mo</span>
                    {sig["signal_description"][:120]}…
                    <a href='{sig["source_url"]}' target='_blank' style='color:#3b82f6;margin-left:6px'>source</a>
                </div>""", unsafe_allow_html=True)
