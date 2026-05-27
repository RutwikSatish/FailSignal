"""
library_loader.py
Drop this file into your FailSignal project.
Usage:
    from library_loader import load_library, get_company, match_patterns, build_context

    lib = load_library()                    # load once at startup
    co  = get_company(lib, "bbby")          # get one company
    matches = match_patterns(lib, signals)  # find companies with similar signals
    ctx = build_context(lib)               # build LLM context string
"""
import json, os
from pathlib import Path

_LIBRARY = None

def load_library(path: str | None = None) -> dict:
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY
    if path is None:
        path = Path(__file__).parent / "failure_library.json"
    with open(path, encoding="utf-8") as f:
        _LIBRARY = json.load(f)
    return _LIBRARY

def get_company(lib: dict, company_id: str) -> dict | None:
    return next((c for c in lib["companies"] if c["id"] == company_id), None)

def match_patterns(lib: dict, detected_tags: list[str], top_n: int = 3) -> list[dict]:
    """
    Given a list of detected pattern tag IDs from a live scan,
    return the top_n most similar companies from the library.
    """
    scores = []
    for co in lib["companies"]:
        overlap = set(co["pattern_tags"]) & set(detected_tags)
        if overlap:
            score = len(overlap) / max(len(co["pattern_tags"]), 1)
            scores.append({"company": co, "overlap_tags": list(overlap), "score": score})
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_n]

def build_context(lib: dict, max_companies: int = 15) -> str:
    """
    Build a compact LLM context string summarising the failure library.
    Pass this as part of your system prompt or user message.
    """
    lines = ["FAILURE PATTERN LIBRARY — use this to identify comparable failures.\n"]
    for co in lib["companies"][:max_companies]:
        lines.append(f"COMPANY: {co['name']} ({co['failure_date']}, {co['failure_type']})")
        lines.append(f"  Industry: {co['industry']} | Primary cause: {co['primary_cause']}")
        lines.append(f"  Pattern tags: {', '.join(co['pattern_tags'])}")
        lines.append(f"  Key signals: {co['key_summary'][:200]}")
        lines.append("")
    return "\n".join(lines)

if __name__ == "__main__":
    lib = load_library()
    print(f"Library loaded: {lib['total_companies']} companies, "
          f"{lib['total_signals']} signals, {lib['total_pattern_tags']} tags")
    print()
    print("Sample context (first 500 chars):")
    print(build_context(lib)[:500])
    print()
    print("Pattern match test — searching for c_suite_exodus + debt_language_spike:")
    matches = match_patterns(lib, ["c_suite_exodus", "debt_language_spike"])
    for m in matches:
        print(f"  {m['company']['name']:30s} overlap={m['overlap_tags']}  score={m['score']:.2f}")
