#!/usr/bin/env python3
"""Validate and safely repair Top 3 candidate identity bindings."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys


PASS = "PASS"
FAIL = "FAIL"


def _metadata(item: dict) -> tuple[str, str, str] | None:
    title = item.get("title")
    source = item.get("source")
    url = item.get("url")
    if all(isinstance(value, str) and value.strip() for value in (title, source, url)):
        return url, title, source
    return None


def _top_metadata(item: dict) -> tuple[str, str, str] | None:
    headline, source_title = item.get("headline"), item.get("source_title")
    source, url = item.get("source"), item.get("source_url")
    if (not all(isinstance(value, str) and value.strip()
                for value in (headline, source_title, source, url))
            or headline != source_title):
        return None
    return url, headline, source


def _valid_selected(candidate: dict) -> bool:
    return (
        candidate.get("selected_for_top3") is True
        and candidate.get("rejection_reason") is None
        and candidate.get("duplicate") is False
        and candidate.get("source_verified") is True
        and candidate.get("within_24h") is True
        and isinstance(candidate.get("verified_summary"), str)
        and bool(candidate["verified_summary"].strip())
        and _metadata(candidate) is not None
    )


def evaluate_identity_binding(envelope: dict) -> dict:
    """Return deterministic identity-binding status and diagnostic IDs."""
    items = envelope.get("top3")
    block = envelope.get("candidate_diagnostics")
    candidates = block.get("candidates") if isinstance(block, dict) else None
    top_ids = [item.get("candidate_id") if isinstance(item, dict) else None
               for item in items] if isinstance(items, list) else []
    candidates = candidates if isinstance(candidates, list) else None
    selected_ids = [candidate.get("candidate_id") for candidate in candidates or []
                    if isinstance(candidate, dict) and candidate.get("selected_for_top3") is True]

    def result(reason: str, mapping: list[str] | None = None) -> dict:
        return {
            "identity_binding_result": PASS if reason == "pass" else FAIL,
            "identity_binding_reason": reason,
            "top3_candidate_ids": top_ids,
            "selected_candidate_ids": selected_ids,
            "metadata_match_candidate_ids": mapping or [],
        }

    if not isinstance(items, list) or len(items) != 3 or candidates is None:
        return result("top3_or_diagnostics_invalid")
    by_id: dict[str, dict] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return result("candidate_diagnostics_invalid")
        cid = candidate.get("candidate_id")
        if not isinstance(cid, str) or not cid or cid in by_id:
            return result("candidate_id_missing_or_duplicate")
        by_id[cid] = candidate
    if len(selected_ids) != 3 or len(set(selected_ids)) != 3:
        return result("selected_set_not_three")
    selected = [by_id[cid] for cid in selected_ids]
    if any(not _valid_selected(candidate) for candidate in selected):
        return result("selected_candidate_invalid")

    selected_metadata = [_metadata(candidate) for candidate in selected]
    top_metadata = [_top_metadata(item) if isinstance(item, dict) else None for item in items]
    if any(value is None for value in top_metadata):
        return result("top3_metadata_invalid")
    if Counter(selected_metadata) != Counter(top_metadata):
        return result("top3_selected_set_mismatch")

    mapped: list[str] = []
    for metadata in top_metadata:
        matches = [candidate for candidate in selected if _metadata(candidate) == metadata]
        if len(matches) != 1:
            return result("selected_metadata_match_not_unique")
        mapped.append(matches[0]["candidate_id"])
    if len(set(mapped)) != 3:
        return result("identity_mapping_not_one_to_one", mapped)
    if top_ids == mapped:
        return result("pass", mapped)
    return result("candidate_id_mismatch", mapped)


def repair_identity_binding(envelope: dict) -> tuple[dict, dict] | None:
    """Return a copy with only Top 3 candidate_id fields rebound, or None."""
    status = evaluate_identity_binding(envelope)
    if status["identity_binding_result"] != FAIL or status["identity_binding_reason"] != "candidate_id_mismatch":
        return None
    repaired = deepcopy(envelope)
    for item, candidate_id in zip(repaired["top3"], status["metadata_match_candidate_ids"]):
        item["candidate_id"] = candidate_id
    # Enforce the narrow mutation contract, including brief and diagnostics.
    for old, new in zip(envelope["top3"], repaired["top3"]):
        if {key: value for key, value in old.items() if key != "candidate_id"} != {
                key: value for key, value in new.items() if key != "candidate_id"}:
            return None
    if (repaired["candidate_diagnostics"] != envelope["candidate_diagnostics"]
            or repaired.get("brief_markdown") != envelope.get("brief_markdown")):
        return None
    final = evaluate_identity_binding(repaired)
    if final["identity_binding_result"] != PASS:
        return None
    return repaired, {
        **final,
        "original_top3_candidate_ids": status["top3_candidate_ids"],
        "repaired_top3_candidate_ids": final["top3_candidate_ids"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("envelope", type=Path)
    repair = sub.add_parser("repair")
    repair.add_argument("envelope", type=Path)
    repair.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        envelope = json.loads(args.envelope.read_text(encoding="utf-8"))
        if args.command == "check":
            report = evaluate_identity_binding(envelope)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["identity_binding_result"] == PASS else 1
        result = repair_identity_binding(envelope)
        if result is None:
            report = evaluate_identity_binding(envelope)
            print(json.dumps({"identity_binding_result": FAIL,
                              "identity_binding_reason": report["identity_binding_reason"]}, ensure_ascii=False))
            return 1
        repaired, report = result
        args.output.write_text(json.dumps(repaired, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"identity_binding_input_invalid:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
