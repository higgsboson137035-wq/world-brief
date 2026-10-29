#!/usr/bin/env python3
"""Fail closed unless every rendered Top 3 item is bound to selected evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

try:
    from .json_contract import parse_json
except ImportError:  # Executed as a standalone script.
    from json_contract import parse_json


def _top3_body_items(brief: str) -> list[str]:
    lines = brief.splitlines()
    try:
        start = lines.index("## Today's Top 3") + 1
    except ValueError:
        return []
    section = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        match = re.match(r"^[1-3]\. (.+)$", line)
        if match:
            section.append(match.group(1))
    return section


def validate_consistency(envelope: dict, brief: str) -> dict:
    block = envelope.get("candidate_diagnostics")
    candidates = block.get("candidates") if isinstance(block, dict) else None
    items = envelope.get("top3")
    candidates = candidates if isinstance(candidates, list) else []
    items = items if isinstance(items, list) else []
    by_id: dict[str, dict] = {}
    details: list[dict] = []
    reason = "pass"

    def fail(code: str, detail: dict) -> None:
        nonlocal reason
        if reason == "pass":
            reason = code
        details.append(detail)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str):
            if candidate_id in by_id:
                fail("top3_candidate_missing", {
                    "candidate_id": candidate_id,
                    "reason": "candidate_id_not_unique",
                })
            else:
                by_id[candidate_id] = candidate

    selected_ids = [
        candidate.get("candidate_id") for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("selected_for_top3") is True
    ]
    top3_ids = [item.get("candidate_id") if isinstance(item, dict) else None for item in items]
    if len(items) != 3:
        fail("top3_candidate_missing", {"reason": "top3_count_must_equal_three", "actual_count": len(items)})
    if len(selected_ids) != 3 or len(set(selected_ids)) != 3:
        fail("top3_candidate_not_selected", {
            "reason": "selected_candidate_count_must_equal_three",
            "selected_candidate_ids": selected_ids,
        })
    if len(top3_ids) != len(set(top3_ids)):
        fail("top3_candidate_missing", {"reason": "top3_candidate_id_reused", "top3_candidate_ids": top3_ids})

    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            fail("top3_candidate_missing", {"item": index, "reason": "top3_item_invalid"})
            continue
        candidate_id = item.get("candidate_id")
        candidate = by_id.get(candidate_id) if isinstance(candidate_id, str) else None
        if candidate is None:
            fail("top3_candidate_missing", {"item": index, "candidate_id": candidate_id, "reason": "candidate_id_not_found"})
            continue
        if candidate.get("selected_for_top3") is not True:
            fail("top3_candidate_not_selected", {
                "item": index, "candidate_id": candidate_id,
                "selected_for_top3": candidate.get("selected_for_top3"),
            })
        rejection = candidate.get("rejection_reason")
        if (candidate.get("duplicate") is not False or rejection is not None
                or candidate.get("within_24h") is not True
                or candidate.get("source_verified") is not True):
            fail("top3_candidate_rejected", {
                "item": index, "candidate_id": candidate_id,
                "duplicate": candidate.get("duplicate"), "rejection_reason": rejection,
                "within_24h": candidate.get("within_24h"),
                "source_verified": candidate.get("source_verified"),
            })
        candidate_url = candidate.get("url")
        candidate_source = candidate.get("source")
        if not isinstance(candidate_url, str) or not candidate_url.strip() or item.get("source_url") != candidate_url:
            fail("top3_source_url_mismatch", {
                "item": index, "candidate_id": candidate_id,
                "candidate_url": candidate_url, "top3_source_url": item.get("source_url"),
            })
        if not isinstance(candidate_source, str) or not candidate_source.strip() or item.get("source") != candidate_source:
            fail("top3_evidence_inconsistent", {
                "item": index, "candidate_id": candidate_id,
                "candidate_source": candidate_source, "top3_source": item.get("source"),
            })
        candidate_title = candidate.get("title")
        if (not isinstance(candidate_title, str) or not candidate_title.strip()
                or item.get("source_title") != candidate_title
                or item.get("headline") != candidate_title):
            fail("top3_source_title_mismatch", {
                "item": index, "candidate_id": candidate_id,
                "candidate_title": candidate_title, "top3_source_title": item.get("source_title"),
                "top3_headline": item.get("headline"),
            })
        summary = item.get("summary")
        verified_summary = candidate.get("verified_summary")
        if not isinstance(summary, str) or not summary.strip() or summary != verified_summary:
            fail("top3_evidence_inconsistent", {
                "item": index, "candidate_id": candidate_id,
                "candidate_verified_summary": verified_summary, "top3_summary": summary,
            })

    body_items = _top3_body_items(brief)
    if len(body_items) != 3:
        fail("top3_evidence_inconsistent", {
            "reason": "brief_top3_count_must_equal_three", "brief_top3_count": len(body_items),
        })
    for index, item in enumerate(items[:3]):
        if not isinstance(item, dict):
            continue
        body_summary = body_items[index] if index < len(body_items) else None
        if body_summary != item.get("summary"):
            fail("top3_evidence_inconsistent", {
                "item": index + 1, "candidate_id": item.get("candidate_id"),
                "structured_summary": item.get("summary"), "brief_top3_text": body_summary,
            })

    if (len(top3_ids) != 3 or len(selected_ids) != 3
            or set(top3_ids) != set(selected_ids)):
        fail("top3_candidate_not_selected", {
            "reason": "top3_candidate_set_mismatch",
            "top3_candidate_ids": top3_ids, "selected_candidate_ids": selected_ids,
        })
    return {
        "top3_candidate_ids": top3_ids,
        "selected_candidate_ids": selected_ids,
        "evidence_consistency_result": "PASS" if reason == "pass" else "FAIL",
        "evidence_consistency_reason": reason,
        "evidence_consistency_details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("envelope", type=Path)
    parser.add_argument("brief", type=Path)
    parser.add_argument("diagnostics_output", type=Path)
    args = parser.parse_args()
    try:
        envelope = parse_json(args.envelope.read_text(encoding="utf-8"))
        brief = args.brief.read_text(encoding="utf-8")
        report = validate_consistency(envelope, brief)
    except (OSError, UnicodeError, ValueError) as exc:
        report = {
            "top3_candidate_ids": [], "selected_candidate_ids": [],
            "evidence_consistency_result": "FAIL",
            "evidence_consistency_reason": "top3_evidence_inconsistent",
            "evidence_consistency_details": [{"reason": str(exc)}],
        }
    args.diagnostics_output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostics_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['evidence_consistency_result']} {report['evidence_consistency_reason']}")
    return 0 if report["evidence_consistency_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
