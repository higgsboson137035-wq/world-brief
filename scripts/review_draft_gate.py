#!/usr/bin/env python3
"""Shared structural quality gate for Human Review drafts."""

from __future__ import annotations

import re
import sys
from pathlib import Path


MAX_OUTPUT_BYTES = 1_048_576
INSUFFICIENT_VERIFIED_ARTICLES = "INSUFFICIENT_VERIFIED_ARTICLES"
NO_PUBLISH_REASON = "no_publish_insufficient_verified_articles"
HEADINGS = (
    "## Today's Top 3",
    "## Executive Summary",
    "## Why It Matters",
    "## Big Picture",
    "## Tomorrow's Watchlist",
    "## 🌍 世界情勢",
    "## 💹 経済",
    "## 🤖 AI・テクノロジー",
    "## 🔬 科学",
    "## 🇯🇵 日本への影響",
)
SOURCE_LINK = re.compile(r"^🔗 \[原文を読む\]\((https://[^\s)]+)\)[ \t]*$")
TITLE = re.compile(r"^英語記事タイトル：\S.*$")
SOURCE = re.compile(r"^情報源：\S.*$")


def japan_impact_reason(lines: list[str]) -> str | None:
    try:
        start = lines.index("## 🇯🇵 日本への影響") + 1
    except ValueError:
        return "japan_impact_empty"

    section = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)

    blocks = 0
    sentences = 0
    has_block = False
    for raw in section:
        line = raw.strip()
        if not line or line.startswith(("英語記事タイトル：", "情報源：", "🔗 ")):
            if not line and has_block:
                blocks += 1
                has_block = False
            continue
        if line.startswith(("- ", "* ")):
            if has_block:
                blocks += 1
                has_block = False
            blocks += 1
            sentences += len(re.findall(r"[。！？]", line))
            continue
        has_block = True
        sentences += len(re.findall(r"[。！？]", line))
    if has_block:
        blocks += 1
    if blocks == 0:
        return "japan_impact_empty"
    if blocks < 2 and sentences <= 2:
        return "japan_impact_too_shallow"
    return None


def validate_text(text: str, expected_date: str) -> str:
    if not text:
        return "empty_output"
    if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
        return "output_too_large"
    if text in (
        INSUFFICIENT_VERIFIED_ARTICLES,
        INSUFFICIENT_VERIFIED_ARTICLES + "\n",
        INSUFFICIENT_VERIFIED_ARTICLES + "\r\n",
    ):
        return NO_PUBLISH_REASON
    if INSUFFICIENT_VERIFIED_ARTICLES in text or text.splitlines()[0].startswith(
        "INSUFFICIENT_VERIFIED_"
    ):
        return "invalid_output"
    lines = text.splitlines()
    if not lines or lines[0] != f"_Date: {expected_date}_":
        return "date_header_invalid"

    found_headings = [line for line in lines if line.startswith("## ")]
    if any(lines.count(heading) != 1 for heading in HEADINGS):
        return "required_heading_invalid"
    if tuple(found_headings) != HEADINGS:
        return "required_heading_order_invalid"

    japan_reason = japan_impact_reason(lines)
    if japan_reason:
        return japan_reason

    try:
        top_start = lines.index("## Today's Top 3") + 1
    except ValueError:
        return "top3_invalid"
    top_lines = []
    for line in lines[top_start:]:
        if line.startswith("## "):
            break
        if re.match(r"^[0-9]+\. ", line):
            top_lines.append(line)
        if re.search(r"該当する重要ニュースなし|掲載候補なし", line):
            return "top3_placeholder"
    if len(top_lines) != 3 or any(
        not line.startswith(f"{index}. ") for index, line in enumerate(top_lines, 1)
    ):
        return "top3_invalid"

    raw_links = [line for line in lines if "🔗 [原文を読む]" in line]
    if not raw_links:
        return "missing_source_link"
    parsed_links = [SOURCE_LINK.fullmatch(line) for line in raw_links]
    if any(match is None for match in parsed_links):
        return "invalid_source_link"
    urls = [match.group(1) for match in parsed_links if match]

    titles = [line for line in lines if line.startswith("英語記事タイトル：")]
    sources = [line for line in lines if line.startswith("情報源：")]
    if len(urls) < 3 or len(titles) != len(urls) or len(sources) != len(urls):
        return "article_source_metadata_invalid"

    for index, line in enumerate(lines):
        if SOURCE_LINK.fullmatch(line):
            previous = [candidate for candidate in lines[:index] if candidate.strip()]
            if len(previous) < 2 or not SOURCE.fullmatch(previous[-1]) or not TITLE.fullmatch(previous[-2]):
                return "article_source_metadata_invalid"

    if len(set(urls)) < 3:
        return "fewer_than_three_distinct_urls"
    if len(set(urls)) != len(urls):
        return "duplicate_source_url"
    if any(re.match(r"^https://(www\.)?reuters\.com/world/?$", url) for url in urls):
        return "non_article_source_url"
    if re.search(r"Top 3.*選定.*見送|Top 3.*選定できません|掲載を見送|ニュース項目を掲載しません", text):
        return "incomplete_retrieval"
    return "pass"


def validate_file(path: Path, expected_date: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "invalid_utf8"
    except OSError:
        return "input_unreadable"
    return validate_text(text, expected_date)


def classify_text(text: str, expected_date: str) -> tuple[str, str]:
    """Return the workflow state and reason for one Codex response."""
    reason = validate_text(text, expected_date)
    if reason == "pass":
        return "VALID_DRAFT", reason
    if reason == NO_PUBLISH_REASON:
        return "INSUFFICIENT_VERIFIED_ARTICLES", reason
    return "INVALID_OUTPUT", reason


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage_error")
        return 2
    reason = validate_file(Path(argv[1]), argv[2])
    print(reason)
    return 0 if reason == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
