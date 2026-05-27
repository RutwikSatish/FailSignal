# ============================================================
#  FailSignal — Supplier Failure Early Warning Agent
#  app.py
#
#  Stack : Streamlit + Serper API + Groq (llama-3.3-70b)
#  Data  : failure_library.json (14 verified company failures)
#
#  Secrets required in .streamlit/secrets.toml:
#    SERPER_API_KEY = "..."
#    GROQ_API_KEY   = "..."
# ============================================================
import streamlit as st
import json
from scanner import run_scan
from library_loader import load_library, build_context, match_patterns

st.set_page_config(
    page_title="FailSignal",
    page_icon="🔴",
    layout="wide",
    initial_sidebar_state="expanded",
)

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
button[data-baseweb="tab"]{background:transparent !important;color:#6b7280 !important;font-family:'Space Mono',monospace !important;font-size:11px !important;border-bottom:2px solid transparent !important}
button[data-baseweb="tab"][aria-selected="true"]{color:#f59e0b !important;border-bottom:2px solid #f59e0b !important}
.stButton>button{background:#1d4ed8 !important;color:#fff !important;border:none !important;border-radius:8px !important;font-weight:700 !important;font-family:'Space Mono',monospace !important;font-size:13px !important}
.stButton>button:hover{background:#2563eb !important}
.stTextInput>div>div>input{background:#111827 !important;border:1px solid #374151 !important;color:#f9fafb !important;border-radius:8px !important;font-size:15px !important}
.fs-card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:18px 22px;margin-bottom:14px}
.fs-alert-r{background:#1a0808;border-left:4px solid #ef4444;border-radius:0 8px 8px 0;padding:14px 18px;margin-bottom:10px}
.fs-alert-y{background:#1a1108;border-left:4px solid #f59e0b;border-radius:0 8px 8px 0;padding:14px 18px;margin-bottom:10px}
.fs-alert-g{background:#081a0e;border-left:4px solid #10b981;border-radius:0 8px 8px 0;padding:14px 18px;margin-bottom:10px}
.fs-mono{font-family:'Space Mono',monospace;font-size:11px}
.fs-label{font-family:'Space Mono',monospace;font-size:10px;color:#f59e0b;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px}
.fs-hero{background:linear-gradient(135deg,#08090f,#0d1520);border:1px solid #1f293780;border-radius:14px;padding:28px 32px;margin-bottom:22px;position:relative;overflow:hidden}
.fs-tag{display:inline-block;background:#ef444420;color:#f87171;border:1px solid #ef444440;border-radius:5px;padding:2px 10px;font-size:11px;font-family:'Space Mono',monospace;margin:2px}
.fs-tag-amber{background:#f59e0b20;color:#fbbf24;border-color:#f59e0b40}
.fs-tag-green{background:#10b98120;color:#34d399;border-color:#10b98140}
.fs-badge-critical{background:#7f1d1d;color:#fca5a5;padding:6px 16px;border-radius:6px;font-family:'Space Mono',monospace;font-size:13px;font-weight:700}
.fs-badge-high{background:#78350f;color:#fed7aa;padding:6px 16px;border-radius:6px;font-family:'Space Mono',monospace;font-size:13px;font-weight:700}
.fs-badge-medium{background:#713f12;color:#fef08a;padding:6px 16px;border-radius:6px;font-family:'Space Mono',monospace;font-size:13px;font-weight:700}
.fs-badge-low{background:#064e3b;color:#a7f3d0;padding:6px 16px;border-radius:6px;font-family:'Space Mono',monospace;font-size:13px;font-weight:700}
.wl-row{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:#111827;border:1px solid #1f2937;border-radius:8px;margin-bottom:8px}
</style>
""", unsafe_allow_html=True)

# ── Load library & secrets ────────────────────────────────────────────────────
@st.cache_resource
def get_library():
    return load_library("failure_library.json")

lib = get_library()
lib_context = build_context(lib)

def _key(k: str) -> str:
    try:    return st.secrets[k]
    except: return ""

SERPER_KEY = _key("SERPER_API_KEY")
GROQ_KEY   = _key("GROQ_API_KEY")

# ── Session state ─────────────────────────────────────────────────────────────
if "result"    not in st.session_state: st.session_state.result    = None
if "watchlist" not in st.session_state: st.session_state.watchlist = []
if "scanning"  not in st.session_state: st.session_state.scanning  = False

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='text-align:center;padding:16px 0 12px'>
        <div style='font-size:28px'>🔴</div>
        <div style='font-family:Space Mono,monospace;font-size:18px;font-weight:700;
                    color:#ef4444;letter-spacing:-1px;margin:4px 0'>FailSignal</div>
        <div style='font-size:10px;color:#6b7280;letter-spacing:3px;text-transform:uppercase'>
            Supplier failure early warning
        </div>
    </div>""", unsafe_allow_html=True)
    st.markdown("---")

    # API status
    if SERPER_KEY and GROQ_KEY:
        st.markdown('<div style="font-size:12px;color:#10b981">✅ APIs configured — ready to scan</div>',
                    unsafe_allow_html=True)
    else:
        missing = []
        if not SERPER_KEY: missing.append("SERPER_API_KEY")
        if not GROQ_KEY:   missing.append("GROQ_API_KEY")
        st.markdown(
            f'<div style="font-size:11px;color:#f59e0b;line-height:1.7">'
            f'⚠️ Missing in secrets.toml:<br>{"<br>".join(missing)}</div>',
            unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div style="font-size:11px;color:#6b7280;line-height:1.8">'
                f'<b style="color:#f9fafb">Library</b><br>'
                f'{lib["total_companies"]} companies · {lib["total_signals"]} signals<br>'
                f'{lib["total_pattern_tags"]} pattern tags<br>'
                f'<span style="color:#374151">v{lib["version"]} · verified sources only</span>'
                '</div>', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown('<div style="font-size:10px;color:#374151;line-height:1.7">'
                'Rutwik Satish · MS Eng. Mgmt · Northeastern<br>'
                'Built with Serper + Groq + verified failure library'
                '</div>', unsafe_allow_html=True)


# ── Hero ──────────────────────────────────────────────────────────────────────
st.markdown("""
<div class='fs-hero'>
    <div style='font-size:24px;font-weight:700;letter-spacing:-0.5px;margin-bottom:6px'>
        🔴 Fail<span style='color:#ef4444'>Signal</span>
    </div>
    <div style='font-size:13px;color:#6b7280;margin-bottom:12px'>
        Enter a supplier name · get a 60-second distress signal report ·
        compared against 14 documented company failures
    </div>
    <div style='font-size:11px;color:#4b5563;font-family:Space Mono,monospace'>
        Sources scanned: news sentiment · executive departures · debt language · vendor signals
    </div>
</div>
""", unsafe_allow_html=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_scan, tab_result, tab_backtest, tab_watchlist, tab_library = st.tabs([
    "🔍  Scan",
    "📋  Result",
    "🔬  Back-test",
    "👁  Watchlist",
    "📚  Library",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — SCAN
# ─────────────────────────────────────────────────────────────────────────────
with tab_scan:
    st.markdown('<p class="fs-label">Enter supplier or company name</p>', unsafe_allow_html=True)

    col_in, col_btn = st.columns([3, 1])
    with col_in:
        company_input = st.text_input(
            "Company name", label_visibility="collapsed",
            placeholder="e.g. Acme Parts Co. · Spirit Airlines · Any company name",
            key="company_input")
    with col_btn:
        scan_btn = st.button("Scan now →", use_container_width=True,
                              disabled=(not SERPER_KEY or not GROQ_KEY))

    if not SERPER_KEY or not GROQ_KEY:
        st.markdown("""
        <div class='fs-alert-y'>
            <b>Setup required</b><br>
            Create <code>.streamlit/secrets.toml</code> with:<br><br>
            <code>SERPER_API_KEY = "your-key-from-serper.dev"</code><br>
            <code>GROQ_API_KEY   = "your-groq-key"</code>
        </div>""", unsafe_allow_html=True)

    if scan_btn and company_input.strip():
        with st.spinner(f"Scanning {company_input.strip()} across 4 data sources…"):
            result = run_scan(company_input.strip(), SERPER_KEY, GROQ_KEY, lib_context)
            st.session_state.result = result

            # Add to watchlist if not already there
            existing_names = [w["company"].lower() for w in st.session_state.watchlist]
            if result["company"].lower() not in existing_names:
                st.session_state.watchlist.append({
                    "company":   result["company"],
                    "risk_tier": result["risk_tier"],
                    "scan_date": result["scan_date"],
                    "signal_count": result["signal_count"],
                })

        tier = result["risk_tier"]
        color_map = {"CRITICAL":"#ef4444","HIGH":"#f97316","MEDIUM":"#f59e0b","LOW":"#10b981"}
        c = color_map.get(tier, "#6b7280")
        st.markdown(f"""
        <div style='background:{c}18;border:1px solid {c}44;border-left:4px solid {c};
                    border-radius:0 10px 10px 0;padding:14px 18px;margin-top:12px'>
            <span style='font-family:Space Mono,monospace;font-size:13px;font-weight:700;color:{c}'>
                {tier} RISK — {result["signal_count"]} of 10 signals detected
            </span><br>
            <span style='font-size:12px;color:#9ca3af'>{result.get("summary","")}</span>
        </div>""", unsafe_allow_html=True)
        st.caption("→ See full report in the **Result** tab")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<p class="fs-label">What this scans</p>', unsafe_allow_html=True)
    sources_grid = [
        ("News sentiment", "Restructuring · covenant · default · bankruptcy language across 30/90 days"),
        ("Leadership signals", "CEO / CFO / COO resignations · departures · replacements"),
        ("Vendor/supplier signals", "Cash-on-delivery demands · supplier refusals · default notices"),
        ("Employee & legal signals", "Mass layoffs · fraud investigations · regulatory actions"),
    ]
    c1, c2 = st.columns(2)
    for i, (title, desc) in enumerate(sources_grid):
        with (c1 if i % 2 == 0 else c2):
            st.markdown(f"""
            <div class='fs-card' style='margin-bottom:10px'>
                <div style='font-size:13px;font-weight:600;color:#f9fafb;margin-bottom:4px'>{title}</div>
                <div style='font-size:12px;color:#6b7280'>{desc}</div>
            </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — RESULT
# ─────────────────────────────────────────────────────────────────────────────
with tab_result:
    r = st.session_state.result
    if r is None:
        st.markdown('<div class="fs-card" style="text-align:center;padding:40px">'
                    '<div style="font-size:14px;color:#6b7280">Run a scan first — go to the Scan tab</div>'
                    '</div>', unsafe_allow_html=True)
    else:
        tier = r.get("risk_tier","LOW")
        badge_class = {"CRITICAL":"fs-badge-critical","HIGH":"fs-badge-high",
                       "MEDIUM":"fs-badge-medium","LOW":"fs-badge-low"}.get(tier,"fs-badge-low")
        color_map = {"CRITICAL":"#ef4444","HIGH":"#f97316","MEDIUM":"#f59e0b","LOW":"#10b981"}
        c = color_map.get(tier,"#6b7280")

        # Header row
        hc1, hc2 = st.columns([2,1])
        with hc1:
            st.markdown(f"""
            <div style='font-size:22px;font-weight:700;color:#f9fafb;margin-bottom:4px'>{r["company"]}</div>
            <div style='font-size:12px;color:#6b7280'>Scanned {r.get("scan_date","today")} ·
            Data quality: {r.get("data_quality","N/A")}</div>""", unsafe_allow_html=True)
        with hc2:
            st.markdown(f'<div class="{badge_class}">{tier} RISK<br>'
                        f'<span style="font-size:11px;font-weight:400">{r["signal_count"]} of 10 signals</span>'
                        f'</div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Summary
        st.markdown(f"""
        <div class='fs-alert-{"r" if tier in ("CRITICAL","HIGH") else "y" if tier=="MEDIUM" else "g"}'>
            <div class='fs-label'>Assessment</div>
            <div style='font-size:13px;color:#f9fafb;line-height:1.7'>{r.get("summary","")}</div>
        </div>""", unsafe_allow_html=True)

        # Detected tags
        detected = r.get("detected_tags",[])
        all_tags = ["c_suite_exodus","going_concern_language","debt_language_spike",
                    "vendor_payment_failure","insider_stock_sales","fraud_transparency",
                    "employee_sentiment_collapse","merger_rescue_blocked",
                    "asset_strip_related_party","operational_shortfall"]

        st.markdown('<p class="fs-label" style="margin-top:18px">Pattern tags</p>', unsafe_allow_html=True)
        tag_html = ""
        for t in all_tags:
            if t in detected:
                tag_html += f'<span class="fs-tag">{t.replace("_"," ")}</span>'
            else:
                tag_html += f'<span style="display:inline-block;background:#1f293760;color:#4b5563;border:1px solid #1f2937;border-radius:5px;padding:2px 10px;font-size:11px;font-family:Space Mono,monospace;margin:2px">{t.replace("_"," ")}</span>'
        st.markdown(f'<div>{tag_html}</div>', unsafe_allow_html=True)

        # Evidence
        evidence = r.get("evidence",{})
        if evidence:
            st.markdown('<p class="fs-label" style="margin-top:18px">Evidence from search results</p>',
                        unsafe_allow_html=True)
            for tag_id, text in evidence.items():
                st.markdown(f"""
                <div class='fs-card' style='border-left:3px solid {c}'>
                    <div style='font-size:11px;font-family:Space Mono,monospace;
                                color:{c};margin-bottom:6px'>{tag_id.replace("_"," ").upper()}</div>
                    <div style='font-size:13px;color:#d1d5db;line-height:1.6'>{text}</div>
                </div>""", unsafe_allow_html=True)

        # Raw signals
        raw_sigs = r.get("raw_signals",[])
        if raw_sigs:
            st.markdown('<p class="fs-label" style="margin-top:4px">Raw signals found</p>',
                        unsafe_allow_html=True)
            for sig in raw_sigs:
                sev = sig.get("severity","Medium")
                sev_color = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sev,"#6b7280")
                url = sig.get("source_url","")
                url_part = f'<a href="{url}" target="_blank" style="color:#3b82f6;font-size:11px">{url[:60]}…</a>' if url else ""
                st.markdown(f"""
                <div style='display:flex;gap:12px;padding:10px 14px;background:#0d1117;
                            border-radius:8px;margin-bottom:6px;align-items:flex-start'>
                    <span style='color:{sev_color};font-size:11px;font-family:Space Mono,monospace;
                                 min-width:50px;margin-top:2px'>{sev}</span>
                    <div>
                        <div style='font-size:13px;color:#f9fafb;line-height:1.5'>{sig.get("description","")}</div>
                        {url_part}
                    </div>
                </div>""", unsafe_allow_html=True)

        # Comparable failures
        if detected:
            comparables = match_patterns(lib, detected, top_n=2)
            if comparables:
                st.markdown('<p class="fs-label" style="margin-top:18px">Most comparable historical failures</p>',
                            unsafe_allow_html=True)
                for m in comparables:
                    co = m["company"]
                    overlap = m["overlap_tags"]
                    score   = m["score"]
                    st.markdown(f"""
                    <div class='fs-card' style='border-left:3px solid #ef4444'>
                        <div style='display:flex;justify-content:space-between'>
                            <div>
                                <div style='font-size:15px;font-weight:600;color:#f87171'>{co["name"]}</div>
                                <div style='font-size:12px;color:#6b7280'>{co["failure_type"]} · {co["failure_date"]} · {co["industry"]}</div>
                            </div>
                            <div style='font-family:Space Mono,monospace;font-size:13px;
                                        color:#f87171;font-weight:700'>{score:.0%} match</div>
                        </div>
                        <div style='font-size:12px;color:#9ca3af;margin-top:8px;line-height:1.6'>{co["key_summary"][:250]}…</div>
                        <div style='margin-top:8px'>
                            {"".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' for t in overlap])}
                        </div>
                    </div>""", unsafe_allow_html=True)

        # Recommended actions
        actions = r.get("recommended_actions",[])
        if actions:
            st.markdown('<p class="fs-label" style="margin-top:18px">Recommended actions</p>',
                        unsafe_allow_html=True)
            for i, action in enumerate(actions, 1):
                st.markdown(f"""
                <div style='display:flex;gap:12px;padding:10px 14px;background:#111827;
                            border-radius:8px;margin-bottom:6px'>
                    <span style='font-family:Space Mono,monospace;font-size:12px;
                                 color:#6b7280;min-width:20px'>{i}</span>
                    <span style='font-size:13px;color:#f9fafb'>{action}</span>
                </div>""", unsafe_allow_html=True)

        # Sources
        sources = r.get("sources_searched",[])
        if sources:
            with st.expander(f"Sources searched ({len(sources)})", expanded=False):
                for s in sources:
                    st.markdown(f'<a href="{s}" target="_blank" style="font-size:12px;color:#3b82f6">{s}</a><br>',
                                unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — BACK-TEST (demonstrates the library works on a known failure)
# ─────────────────────────────────────────────────────────────────────────────
with tab_backtest:
    st.markdown('<p class="fs-label">Back-test: what the library shows for Bed Bath & Beyond</p>',
                unsafe_allow_html=True)
    st.markdown("""
    <div class='fs-alert-y'>
        <div class='fs-label'>What this demonstrates</div>
        <div style='font-size:13px;color:#f9fafb;line-height:1.7'>
            Bed Bath & Beyond filed Chapter 11 on April 23 2023.
            The signals below were all publicly available and documented from news sources
            as early as October 2021 — 18 months before collapse.
            This is what a FailSignal scan in October 2021 would have shown.
        </div>
    </div>""", unsafe_allow_html=True)

    # Load BBBY from library
    bbby = next((c for c in lib["companies"] if c["id"]=="bbby"), None)
    if bbby:
        st.markdown(f"""
        <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:16px'>
            <div>
                <div style='font-size:20px;font-weight:700;color:#f9fafb'>{bbby["name"]}</div>
                <div style='font-size:12px;color:#6b7280'>Chapter 11 filed {bbby["failure_date"]} · {bbby["industry"]}</div>
            </div>
            <div class='fs-badge-critical'>HIGH RISK<br>
                <span style='font-size:11px;font-weight:400'>{len(bbby["pattern_tags"])} of 10 signals</span>
            </div>
        </div>""", unsafe_allow_html=True)

        st.markdown('<p class="fs-label">Pattern tags identified</p>', unsafe_allow_html=True)
        tag_html = "".join([f'<span class="fs-tag">{t.replace("_"," ")}</span>' for t in bbby["pattern_tags"]])
        st.markdown(f'<div style="margin-bottom:16px">{tag_html}</div>', unsafe_allow_html=True)

        st.markdown('<p class="fs-label">Signal timeline — sorted earliest to latest</p>',
                    unsafe_allow_html=True)
        for sig in sorted(bbby["signals"], key=lambda x: -x["months_before"]):
            sev_color = {"High":"#ef4444","Medium":"#f59e0b","Low":"#10b981"}.get(sig["severity"],"#6b7280")
            st.markdown(f"""
            <div class='fs-card' style='border-left:3px solid {sev_color};margin-bottom:8px'>
                <div style='display:flex;gap:16px;align-items:flex-start'>
                    <div style='font-family:Space Mono,monospace;font-size:11px;
                                color:{sev_color};min-width:72px;margin-top:2px'>
                        T−{sig["months_before"]}mo</div>
                    <div>
                        <div style='font-size:13px;color:#f9fafb;line-height:1.6'>{sig["signal_description"]}</div>
                        <div style='font-size:11px;color:#4b5563;margin-top:4px'>
                            {sig["source_type"]} ·
                            <a href="{sig["source_url"]}" target="_blank"
                               style="color:#3b82f6">{sig["source_url"][:60]}…</a>
                        </div>
                    </div>
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""
        <div class='fs-alert-r' style='margin-top:8px'>
            <div class='fs-label'>Key summary</div>
            <div style='font-size:13px;color:#f9fafb;line-height:1.7'>{bbby["key_summary"]}</div>
        </div>""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — WATCHLIST
# ─────────────────────────────────────────────────────────────────────────────
with tab_watchlist:
    wl = st.session_state.watchlist
    if not wl:
        st.markdown('<div class="fs-card" style="text-align:center;padding:40px">'
                    '<div style="font-size:14px;color:#6b7280">No companies scanned yet.<br>'
                    'Run a scan — it automatically adds here.</div></div>',
                    unsafe_allow_html=True)
    else:
        color_map = {"CRITICAL":"#ef4444","HIGH":"#f97316","MEDIUM":"#f59e0b","LOW":"#10b981"}
        badge_class = {"CRITICAL":"fs-badge-critical","HIGH":"fs-badge-high",
                       "MEDIUM":"fs-badge-medium","LOW":"fs-badge-low"}

        m1,m2,m3 = st.columns(3)
        m1.metric("Companies monitored", len(wl))
        high_risk = sum(1 for w in wl if w["risk_tier"] in ("CRITICAL","HIGH"))
        m2.metric("High/Critical risk", high_risk, delta_color="off")
        avg_sig = round(sum(w["signal_count"] for w in wl)/len(wl),1) if wl else 0
        m3.metric("Avg signals detected", avg_sig)

        st.markdown("<br>", unsafe_allow_html=True)
        for w in sorted(wl, key=lambda x: ["CRITICAL","HIGH","MEDIUM","LOW"].index(x["risk_tier"])):
            c = color_map.get(w["risk_tier"],"#6b7280")
            bc = badge_class.get(w["risk_tier"],"fs-badge-low")
            st.markdown(f"""
            <div class='wl-row'>
                <div>
                    <div style='font-size:15px;font-weight:600;color:#f9fafb'>{w["company"]}</div>
                    <div style='font-size:11px;color:#6b7280;font-family:Space Mono,monospace'>
                        Scanned {w["scan_date"]} · {w["signal_count"]} signals
                    </div>
                </div>
                <div class='{bc}'>{w["risk_tier"]}</div>
            </div>""", unsafe_allow_html=True)

        if st.button("Clear watchlist"):
            st.session_state.watchlist = []
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# TAB 5 — LIBRARY
# ─────────────────────────────────────────────────────────────────────────────
with tab_library:
    st.markdown('<p class="fs-label">Failure library — 14 verified companies</p>', unsafe_allow_html=True)
    st.markdown("""
    <div class='fs-card'>
        <div style='font-size:13px;color:#9ca3af;line-height:1.7'>
            All signals sourced from real articles, SEC EDGAR filings, and court documents.
            Every source URL was fetched and read. No fabricated data.
        </div>
    </div>""", unsafe_allow_html=True)

    for co in sorted(lib["companies"], key=lambda x: x["failure_date"], reverse=True):
        with st.expander(
            f'{co["name"]}  —  {co["failure_type"]}  ·  {co["failure_date"]}  ·  {len(co["signals"])} signals',
            expanded=False
        ):
            st.markdown(f"""
            <div style='display:flex;gap:24px;flex-wrap:wrap;font-size:12px;margin-bottom:10px'>
                <span><b style='color:#9ca3af'>Industry</b> {co["industry"]}</span>
                <span><b style='color:#9ca3af'>Cause</b> {co["primary_cause"]}</span>
                <span><b style='color:#9ca3af'>Country</b> {co["country"]}</span>
            </div>
            <div style='font-size:13px;color:#d1d5db;line-height:1.7;margin-bottom:10px'>{co["key_summary"]}</div>
            """, unsafe_allow_html=True)

            tag_html = "".join([
                f'<span class="fs-tag">{t.replace("_"," ")}</span>'
                for t in co["pattern_tags"]
            ])
            st.markdown(f'<div style="margin-bottom:10px">{tag_html}</div>', unsafe_allow_html=True)

            for sig in sorted(co["signals"], key=lambda x: -x["months_before"]):
                st.markdown(f"""
                <div style='padding:8px 0;border-bottom:1px solid #1f2937;font-size:12px;color:#9ca3af'>
                    <span style='font-family:Space Mono,monospace;color:#6b7280;margin-right:12px'>T−{sig["months_before"]}mo</span>
                    {sig["signal_description"][:120]}…
                    <a href='{sig["source_url"]}' target='_blank' style='color:#3b82f6;margin-left:8px'>source</a>
                </div>""", unsafe_allow_html=True)
