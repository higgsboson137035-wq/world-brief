#!/usr/bin/env python3
"""Repair only Top 3 summaries from their selected candidate verified summaries."""

from __future__ import annotations

from copy import deepcopy
import re


INVALID = "TOP3_SUMMARY_BINDING_INVALID"
PASS = "PASS"
TOP3_HEADING = "## Today's Top 3"
NUMBERED_SUMMARY = re.compile(r"^([1-3]\. )(.*)$")


def repair_summary_binding(envelope: dict, brief: str) -> tuple[dict, str] | None:
    """Return a copy with only structured/body Top 3 summaries rebound.

    All candidate, identity, headline, publisher and URL fields are immutable.
    Missing or invalid verified summaries fail closed rather than being inferred.
    """
    items = envelope.get("top3")
    block = envelope.get("candidate_diagnostics")
    candidates = block.get("candidates") if isinstance(block, dict) else None
    if not isinstance(items, list) or len(items) != 3 or not isinstance(candidates, list):
        return None
    by_id: dict[str, dict] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        cid = candidate.get("candidate_id")
        if not isinstance(cid, str) or cid in by_id:
            return None
        by_id[cid] = candidate

    try:
        start = brief.splitlines().index(TOP3_HEADING) + 1
    except ValueError:
        return None
    lines = brief.splitlines()
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    numbered = [(i, NUMBERED_SUMMARY.fullmatch(lines[i])) for i in range(start, end)
                if NUMBERED_SUMMARY.fullmatch(lines[i])]
    if len(numbered) != 3 or [match.group(1) for _, match in numbered] != ["1. ", "2. ", "3. "]:
        return None

    target_summaries: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        candidate = by_id.get(item.get("candidate_id"))
        if (candidate is None or candidate.get("selected_for_top3") is not True
                or candidate.get("duplicate") is not False or candidate.get("rejection_reason") is not None
                or candidate.get("within_24h") is not True or candidate.get("source_verified") is not True):
            return None
        verified = candidate.get("verified_summary")
        if not isinstance(verified, str) or not verified.strip():
            return None
        if (item.get("headline") != candidate.get("title")
                or item.get("source_title") != candidate.get("title")
                or item.get("source") != candidate.get("source")
                or item.get("source_url") != candidate.get("url")):
            return None
        target_summaries.append(verified)

    repaired_envelope = deepcopy(envelope)
    repaired_brief_lines = list(lines)
    changed = False
    for index, (item, summary, (line_index, match)) in enumerate(zip(repaired_envelope["top3"], target_summaries, numbered)):
        if item.get("summary") != summary or match.group(2) != summary:
            changed = True
        item["summary"] = summary
        repaired_brief_lines[line_index] = match.group(1) + summary
    if not changed:
        return None
    repaired_brief = "\n".join(repaired_brief_lines)
    if brief.endswith("\n") and not repaired_brief.endswith("\n"):
        repaired_brief += "\n"

    # Fail closed if any field outside the approved summary fields changed.
    for old_item, new_item in zip(items, repaired_envelope["top3"]):
        old = {key: value for key, value in old_item.items() if key != "summary"}
        new = {key: value for key, value in new_item.items() if key != "summary"}
        if old != new:
            return None
    if repaired_envelope["candidate_diagnostics"] != envelope["candidate_diagnostics"]:
        return None
    repaired_envelope["brief_markdown"] = repaired_brief
    return repaired_envelope, repaired_brief
