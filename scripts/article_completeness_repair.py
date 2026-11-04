#!/usr/bin/env python3
"""Validate or reconstruct missing selected-article blocks from saved evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

try:
    from .json_contract import parse_json, validate_envelope
    from .review_draft_gate import SOURCE_LINK, SOURCE, TITLE
except ImportError:  # Executed as a standalone script.
    from json_contract import parse_json, validate_envelope
    from review_draft_gate import SOURCE_LINK, SOURCE, TITLE


INVALID = "ARTICLE_COMPLETENESS_INVALID"
PASS = "PASS"
def _metadata(text: str) -> list[tuple[str, str, str]]:
    lines = text.splitlines()
    blocks = []
    for index, line in enumerate(lines):
        match = SOURCE_LINK.fullmatch(line)
        if not match:
            continue
        previous = [value for value in lines[:index] if value.strip()]
        if len(previous) < 2:
            blocks.append(("", "", match.group(1)))
            continue
        title, source = previous[-2:]
        title_value = title.removeprefix("英語記事タイトル：") if TITLE.fullmatch(title) else ""
        source_value = source.removeprefix("情報源：") if SOURCE.fullmatch(source) else ""
        blocks.append((title_value, source_value, match.group(1)))
    return blocks


def _article_body_count(text: str) -> int:
    lines = text.splitlines()
    count = 0
    for index, line in enumerate(lines):
        if not SOURCE_LINK.fullmatch(line):
            continue
        previous = [value for value in lines[:index] if value.strip()]
        if len(previous) < 3:
            continue
        body_line = previous[-3]
        if (not body_line.startswith("## ") and not TITLE.fullmatch(body_line)
                and not SOURCE.fullmatch(body_line) and not body_line.startswith("🔗 ")
                and not re.match(r"^[1-3]\. ", body_line)):
            count += 1
    return count


def validate_completeness(envelope: dict, brief: str) -> str:
    top3 = envelope.get("top3")
    if not isinstance(top3, list) or len(top3) != 3:
        return INVALID
    blocks = _metadata(brief)
    title_count = sum(line.startswith("英語記事タイトル：") for line in brief.splitlines())
    source_count = sum(line.startswith("情報源：") for line in brief.splitlines())
    url_count = sum("🔗 [原文を読む]" in line for line in brief.splitlines())
    if (len(blocks) != 3 or _article_body_count(brief) != 3
            or (title_count, source_count, url_count) != (3, 3, 3)):
        return INVALID
    for item in top3:
        if not isinstance(item, dict):
            return INVALID
        expected = (item.get("source_title"), item.get("source"), item.get("source_url"))
        if any(not isinstance(value, str) or not value for value in expected) or blocks.count(expected) != 1:
            return INVALID
    return PASS


def _selected_candidates(envelope: dict) -> dict[str, dict]:
    block = envelope.get("candidate_diagnostics")
    candidates = block.get("candidates") if isinstance(block, dict) else []
    return {
        item.get("candidate_id"): item
        for item in candidates if isinstance(item, dict) and item.get("selected_for_top3") is True
    }


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return None
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def repair_envelope(envelope: dict, brief: str) -> tuple[dict, str] | None:
    """Add only absent selected articles; all metadata is copied from top3."""
    if validate_completeness(envelope, brief) == PASS:
        return None
    top3 = envelope.get("top3")
    if not isinstance(top3, list) or len(top3) != 3:
        return None
    candidates = _selected_candidates(envelope)
    lines = brief.splitlines()
    existing = _metadata(brief)
    for item in top3:
        if not isinstance(item, dict):
            return None
        metadata = (item.get("source_title"), item.get("source"), item.get("source_url"))
        occurrences = existing.count(metadata)
        if occurrences == 1:
            continue
        if occurrences > 1:
            return None
        candidate = candidates.get(item.get("candidate_id"))
        if (not isinstance(candidate, dict)
                or candidate.get("title") != item.get("source_title")
                or candidate.get("source") != item.get("source")
                or candidate.get("url") != item.get("source_url")):
            return None
        summary = candidate.get("verified_summary")
        if not isinstance(summary, str) or not summary.strip() or summary != item.get("summary"):
            return None
        bounds = _section_bounds(lines, "## 🌍 世界情勢")
        if bounds is None:
            return None
        start, end = bounds
        article = [
            "", summary.strip(), "",
            "この動きが示す点は、記録された出来事が外交や政策判断に関わることだ。今後、関係する当事者が具体的な対応を示すかを確認したい。",
            "", f"英語記事タイトル：{item['source_title']}", f"情報源：{item['source']}",
            "", f"🔗 [原文を読む]({item['source_url']})",
        ]
        lines[end:end] = article
        existing.append(metadata)
    repaired = "\n".join(lines).rstrip() + "\n"
    if validate_completeness(envelope, repaired) != PASS:
        return None
    return envelope, repaired


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("envelope", type=Path)
    check.add_argument("brief", type=Path)
    repair = sub.add_parser("repair")
    repair.add_argument("envelope", type=Path)
    repair.add_argument("brief", type=Path)
    repair.add_argument("output_json", type=Path)
    repair.add_argument("output_brief", type=Path)
    args = parser.parse_args(argv)
    try:
        envelope = parse_json(args.envelope.read_text(encoding="utf-8"))
        body = args.brief.read_text(encoding="utf-8")
        status, canonical_body, _ = validate_envelope(envelope)
        if status != "PUBLISH" or canonical_body != body:
            print("ARTICLE_COMPLETENESS_INVALID")
            return 1
        if args.command == "check":
            result = validate_completeness(envelope, body)
            print(result)
            return 0 if result == PASS else 1
        result = repair_envelope(envelope, body)
        if result is None:
            print("ARTICLE_COMPLETENESS_INVALID")
            return 1
        repaired_envelope, repaired_body = result
        repaired_envelope = dict(repaired_envelope)
        repaired_envelope["brief_markdown"] = repaired_body
        args.output_json.write_text(json.dumps(repaired_envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.output_brief.write_text(repaired_body, encoding="utf-8")
        print(PASS)
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print("ARTICLE_COMPLETENESS_INVALID")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
