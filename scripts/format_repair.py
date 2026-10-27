#!/usr/bin/env python3
"""Apply one deterministic, structure-only repair to a draft's H2 sections."""

from __future__ import annotations

from collections import Counter
import re
import sys
import unicodedata
from pathlib import Path

try:
    from .review_draft_gate import HEADINGS
except ImportError:  # Executed as a standalone script.
    from review_draft_gate import HEADINGS


FORMAT_REPAIRABLE_REASONS = {
    "required_heading_invalid",
    "required_heading_order_invalid",
}
FORMAT_REPAIRABLE = "FORMAT_REPAIRABLE"
SUBSTANTIVE_FAILURE = "SUBSTANTIVE_FAILURE"
HEADING = re.compile(r"^##(?!#)\s*(.+?)\s*(?:\r?\n)?$")


def _key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


CANONICAL_BY_KEY = {_key(heading[3:]): heading for heading in HEADINGS}


def _destination(extra_heading: str) -> str:
    key = _key(extra_heading)
    if any(word in key for word in ("アジア", "世界", "地政", "外交", "安全保障", "asia", "world")):
        return HEADINGS[5]
    if any(word in key for word in ("経済", "市場", "金融", "産業", "企業", "economy", "market")):
        return HEADINGS[6]
    if any(word in key for word in ("ai", "技術", "technology", "tech")):
        return HEADINGS[7]
    if any(word in key for word in ("科学", "研究", "science")):
        return HEADINGS[8]
    if any(word in key for word in ("日本", "japan")):
        return HEADINGS[9]
    if any(word in key for word in ("watch", "明日", "注目")):
        return HEADINGS[4]
    if any(word in key for word in ("why", "重要")):
        return HEADINGS[2]
    if any(word in key for word in ("bigpicture", "全体像")):
        return HEADINGS[3]
    # Generic detail / supplement headings have no reliable topic label.
    return HEADINGS[1]


def repair_text(text: str) -> str | None:
    """Normalize H2 spelling/order and fold extra H2 bodies into allowed sections.

    Non-heading lines are copied verbatim. Missing required sections are not
    invented, so a content omission remains a substantive failure.
    """
    sections: dict[str, list[str]] = {heading: [] for heading in HEADINGS}
    seen: Counter[str] = Counter()
    prefix: list[str] = []
    current: str | None = None
    saw_heading = False

    for line in text.splitlines(keepends=True):
        match = HEADING.match(line)
        if match:
            saw_heading = True
            label = match.group(1).strip()
            destination = CANONICAL_BY_KEY.get(_key(label))
            if destination is None:
                destination = _destination(label)
            else:
                seen[destination] += 1
            current = destination
            continue
        if current is None:
            prefix.append(line)
        else:
            sections[current].append(line)

    if not saw_heading or any(seen[heading] == 0 for heading in HEADINGS):
        return None

    pieces = ["".join(prefix)]
    for heading in HEADINGS:
        body = "".join(sections[heading])
        pieces.append(heading + "\n")
        pieces.append(body)
        if body and not body.endswith("\n"):
            pieces.append("\n")
    repaired = "".join(pieces)
    return repaired if repaired != text else None


def classify_failure(reason: str, text: str) -> str:
    if reason in FORMAT_REPAIRABLE_REASONS and repair_text(text) is not None:
        return FORMAT_REPAIRABLE
    return SUBSTANTIVE_FAILURE


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "classify":
        try:
            text = Path(argv[3]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            print(SUBSTANTIVE_FAILURE)
            return 0
        print(classify_failure(argv[2], text))
        return 0
    if len(argv) == 4 and argv[1] == "repair":
        try:
            text = Path(argv[2]).read_text(encoding="utf-8")
            repaired = repair_text(text)
            if repaired is None:
                return 2
            Path(argv[3]).write_text(repaired, encoding="utf-8")
        except (OSError, UnicodeError):
            return 2
        return 0
    print("usage: format_repair.py classify REASON INPUT | repair INPUT OUTPUT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
