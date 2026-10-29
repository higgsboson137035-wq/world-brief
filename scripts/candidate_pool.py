#!/usr/bin/env python3
"""Accumulate and classify candidate evidence across World Brief search stages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UNKNOWN = {None, "UNKNOWN", "unknown"}
REJECT_REASONS = {
    "outside_24h", "missing_url", "unverifiable_source", "duplicate_url",
    "duplicate_story", "insufficient_source_detail", "title_invalid",
    "missing_required_section", "japan_impact_gate", "other_quality_gate",
    "model_uncertain", "model_returned_sentinel",
}


def normalized_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/") or "/"
        query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ref", "source"}]
        return urlunsplit((parsed.scheme.lower(), host, path, urlencode(sorted(query)), ""))
    except (TypeError, ValueError):
        return None


def eligibility(candidate: dict) -> tuple[str, str]:
    """Return ELIGIBLE, REJECTED, or UNKNOWN using only explicit recorded evidence."""
    reason = candidate.get("rejection_reason")
    if reason in REJECT_REASONS or candidate.get("duplicate") is True:
        return "REJECTED", reason if reason in REJECT_REASONS else "duplicate_story"
    if reason == "unknown":
        return "UNKNOWN", "rejection_reason_unknown"
    for field, code in (("title", "title_invalid"), ("source", "insufficient_source_detail"),
                        ("url", "missing_url")):
        value = candidate.get(field)
        if isinstance(value, str) and value.strip():
            if field == "url" and normalized_url(value) is None:
                return "REJECTED", "missing_url"
            continue
        if value is None or value == "":
            return "REJECTED", code
        return "UNKNOWN", f"{field}_unknown"
    if candidate.get("within_24h") is False:
        return "REJECTED", "outside_24h"
    if candidate.get("source_verified") is False:
        return "REJECTED", "unverifiable_source"
    if candidate.get("duplicate") is False:
        pass
    else:
        return "UNKNOWN", "duplicate_unknown"
    if candidate.get("within_24h") is not True:
        return "UNKNOWN", "freshness_unknown"
    if candidate.get("source_verified") is not True:
        return "UNKNOWN", "source_verification_unknown"
    if reason not in (None, "top3_not_selected"):
        return "REJECTED", str(reason or "unknown")
    return "ELIGIBLE", "eligible"


def _useful(old: object, new: object) -> object:
    return new if new not in UNKNOWN and new != "" else old


def update_pool(pool_path: Path, stage_path: Path, stage: str, stats_path: Path) -> dict:
    try:
        pool_doc = json.loads(pool_path.read_text(encoding="utf-8")) if pool_path.exists() else {"candidates": []}
        stage_doc = json.loads(stage_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate_pool_input_invalid") from exc
    pool = pool_doc.get("candidates", [])
    incoming = stage_doc.get("candidates", [])
    if not isinstance(pool, list) or not isinstance(incoming, list):
        raise ValueError("candidate_pool_schema_invalid")
    stage_counts = {"eligible": 0, "rejected": 0, "unknown": 0}
    stage_seen: set[str] = set()
    index = {normalized_url(c.get("url")): c for c in pool if isinstance(c, dict) and normalized_url(c.get("url"))}
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        url_key = normalized_url(raw.get("url"))
        stage_identity = url_key or f"missing-url:{raw.get('candidate_id')}"
        if stage_identity not in stage_seen:
            status, _ = eligibility(raw)
            stage_counts[status.lower()] += 1
            stage_seen.add(stage_identity)
        if url_key and url_key in index:
            target = index[url_key]
            target["originating_stages"] = list(dict.fromkeys(target.get("originating_stages", []) + [stage]))
            target["originating_candidate_ids"] = list(dict.fromkeys(
                target.get("originating_candidate_ids", []) + [raw.get("candidate_id")]
            ))
            for key in ("title", "source", "url", "published_at", "within_24h", "source_verified", "duplicate", "rejection_reason", "verified_summary"):
                target[key] = _useful(target.get(key), raw.get(key))
            # Explicit rejection in any copy must remain disqualifying.
            if raw.get("rejection_reason") in REJECT_REASONS or raw.get("duplicate") is True:
                target["rejection_reason"] = raw.get("rejection_reason") or "duplicate_story"
                target["duplicate"] = raw.get("duplicate", target.get("duplicate"))
            continue
        item = {key: raw.get(key) for key in (
            "title", "source", "url", "published_at", "within_24h", "source_verified",
            "duplicate", "rejection_reason", "verified_summary",
        )}
        item["candidate_id"] = f"P{len(pool) + 1}"
        item["originating_stages"] = [stage]
        item["originating_candidate_ids"] = [raw.get("candidate_id")]
        item["originating_stage"] = stage
        item["canonical_url"] = url_key
        pool.append(item)
        if url_key:
            index[url_key] = item
    # Only explicit duplicate story/url annotations deduplicate different URLs.
    eligible_ids = []
    counts = {"eligible": 0, "rejected": 0, "unknown": 0}
    for item in pool:
        state, _ = eligibility(item)
        counts[state.lower()] += 1
        if state == "ELIGIBLE":
            eligible_ids.append(item["candidate_id"])
    out = {"status": "RECORDED", "candidates": pool}
    pool_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {
        "search_stage": stage,
        "stage_candidate_count": len(incoming),
        "stage_eligible_count": stage_counts["eligible"],
        "stage_rejected_count": stage_counts["rejected"],
        "stage_unknown_count": stage_counts["unknown"],
        "cumulative_candidate_count": len(pool),
        "cumulative_eligible_count": counts["eligible"],
        "cumulative_rejected_count": counts["rejected"],
        "cumulative_unknown_count": counts["unknown"],
        "cumulative_candidate_ids": [c["candidate_id"] for c in pool],
        "cumulative_eligible_ids": eligible_ids,
        "candidates": pool,
    }
    stats_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['stage_candidate_count']} {result['stage_eligible_count']} {result['cumulative_candidate_count']} {result['cumulative_eligible_count']}")
    return result


def build_selection_prompt(pool_path: Path, output_path: Path, run_date: str = "UNKNOWN") -> None:
    doc = json.loads(pool_path.read_text(encoding="utf-8"))
    eligible = []
    for candidate in doc.get("candidates", []):
        state, _ = eligibility(candidate)
        if state == "ELIGIBLE":
            eligible.append(candidate)
    if len(eligible) < 3:
        raise ValueError("selection_pool_below_minimum")
    allowed = ("candidate_id", "title", "source", "url", "published_at", "verified_summary")
    evidence = [{key: item.get(key) for key in allowed} for item in eligible]
    payload = json.dumps(evidence, ensure_ascii=False, indent=2)
    prompt = f"""# World Brief selection-only recovery\n\nRun date in Japan: {run_date}. Set the first brief_markdown line exactly to _Date: {run_date}_. Do not use web search, network, shell commands, or new sources. Use only the eligible candidate records below. Do not add or invent candidates or facts.\n\nThere are {len(eligible)} eligible candidates, at least three. You MUST NOT return INSUFFICIENT_VERIFIED_ARTICLES. Select exactly three distinct eligible candidates. Preserve each candidate's title, source, and URL exactly. For selected candidates provide verified_summary and use exactly the same text in top3.summary. If the available evidence cannot support a substantive detail, omit that detail rather than inventing it; downstream gates remain mandatory.\n\nSelection priority: global importance, avoid duplicate stories, balance regions and themes, then clearest recorded source evidence. Set exactly the selected three candidate records selected_for_top3=true, duplicate=false, source_verified=true, within_24h=true, rejection_reason=null. For other eligible records use selected_for_top3=false and rejection_reason=top3_not_selected. Order candidate_diagnostics.candidates with the selected three first in exactly the same order as top3, then list the remaining eligible candidates. Do not add candidates.\n\nReturn only the normal JSON contract with final_status=PUBLISH, brief_markdown containing exactly these headings in this order: ## Today's Top 3; ## Executive Summary; ## Why It Matters; ## Big Picture; ## Tomorrow's Watchlist; ## 🌍 世界情勢; ## 💹 経済; ## 🤖 AI・テクノロジー; ## 🔬 科学; ## 🇯🇵 日本への影響. Top 3 must have exactly three numbered summary lines. Return top3 exactly three items with candidate_id/headline/source_title/source/source_url/summary and candidate_diagnostics containing the eligible pool without adding candidates. Each diagnostic candidate must include candidate_id, title, source, url, published_at, verified_summary, within_24h, source_verified, duplicate, selected_for_top3, rejection_reason. Copy the eligible candidate title/source/url/date exactly. Keep all existing evidence and quality criteria unchanged.\n\nELIGIBLE CANDIDATES (verified information only):\n{payload}\n"""
    output_path.write_text(prompt, encoding="utf-8")


def validate_selection(pool_path: Path, selection_path: Path) -> str:
    try:
        pool_doc = json.loads(pool_path.read_text(encoding="utf-8"))
        envelope = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("selection_output_invalid") from exc
    if not isinstance(envelope, dict) or envelope.get("final_status") != "PUBLISH":
        raise ValueError("selection_output_not_publish")
    eligible = [c for c in pool_doc.get("candidates", []) if eligibility(c)[0] == "ELIGIBLE"]
    expected = {c["candidate_id"]: c for c in eligible}
    block = envelope.get("candidate_diagnostics")
    outputs = block.get("candidates") if isinstance(block, dict) else None
    if not isinstance(outputs, list):
        raise ValueError("selection_candidate_diagnostics_missing")
    ids = [c.get("candidate_id") for c in outputs if isinstance(c, dict)]
    if len(ids) != len(outputs) or len(set(ids)) != len(ids) or set(ids) != set(expected):
        raise ValueError("selection_candidate_pool_changed")
    by_id = {c["candidate_id"]: c for c in outputs}
    for candidate_id, original in expected.items():
        repaired = by_id[candidate_id]
        for field in ("title", "source", "url", "published_at"):
            if repaired.get(field) != original.get(field):
                raise ValueError(f"selection_candidate_{field}_changed:{candidate_id}")
        if (repaired.get("within_24h") is not True or repaired.get("source_verified") is not True
                or repaired.get("duplicate") is not False):
            raise ValueError(f"selection_candidate_eligibility_changed:{candidate_id}")
    top3 = envelope.get("top3")
    if not isinstance(top3, list) or len(top3) != 3:
        raise ValueError("selection_top3_count_invalid")
    top_ids = [item.get("candidate_id") if isinstance(item, dict) else None for item in top3]
    selected_ids = [candidate_id for candidate_id in ids if by_id[candidate_id].get("selected_for_top3") is True]
    if len(set(top_ids)) != 3 or top_ids != selected_ids or any(candidate_id not in expected for candidate_id in top_ids):
        raise ValueError("selection_top3_binding_invalid")
    for item in top3:
        original = expected[item["candidate_id"]]
        if (item.get("headline") != original.get("title")
                or item.get("source_title") != original.get("title")
                or item.get("source") != original.get("source")
                or item.get("source_url") != original.get("url")):
            raise ValueError(f"selection_top3_evidence_changed:{item['candidate_id']}")
    return "PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    update = sub.add_parser("update")
    update.add_argument("pool", type=Path)
    update.add_argument("stage_candidates", type=Path)
    update.add_argument("stage")
    update.add_argument("stats", type=Path)
    prompt = sub.add_parser("selection-prompt")
    prompt.add_argument("pool", type=Path)
    prompt.add_argument("output", type=Path)
    prompt.add_argument("--date", default="UNKNOWN")
    validate = sub.add_parser("validate-selection")
    validate.add_argument("pool", type=Path)
    validate.add_argument("selection", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "update":
            update_pool(args.pool, args.stage_candidates, args.stage, args.stats)
        elif args.command == "selection-prompt":
            build_selection_prompt(args.pool, args.output, args.date)
        else:
            print(validate_selection(args.pool, args.selection))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc) or "candidate_pool_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
