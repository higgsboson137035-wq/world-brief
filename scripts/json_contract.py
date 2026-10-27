#!/usr/bin/env python3
"""Validate and safely repair the World Brief JSON response contract."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

try:
    from .run_diagnostics import atomic_json, validate_candidate
except ImportError:  # Executed as a standalone script.
    from run_diagnostics import atomic_json, validate_candidate


CONTRACT_REPAIRABLE = "CONTRACT_REPAIRABLE"
DIAGNOSTICS_EMBEDDED = "candidate_diagnostics_embedded_in_brief_markdown"
ALLOWED_COVERAGE = {"COMPLETE", "PARTIAL", "UNKNOWN"}
DIAGNOSTIC_HEADING = re.compile(
    r"^##\s*(?:candidate\s+diagnostics?|候補診断|候補一覧|coverage\s+note)\s*$",
    re.IGNORECASE,
)
ANY_H2 = re.compile(r"^##(?!#)\s+")
FIELD_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?`?(?:candidate_id|selected_for_top3|rejection_reason|"
    r"source_verified|within_24h)`?\s*[:=|]",
    re.IGNORECASE,
)
STRUCTURED_FIELD_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:candidate_id|selected_for_top3|rejection_reason|source_verified|within_24h)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
COVERAGE_LINE = re.compile(
    r"(?<![A-Za-z0-9_])`?coverage`?\s*[:=]\s*(?:COMPLETE|PARTIAL|FULL|UNKNOWN)\b",
    re.IGNORECASE,
)
ID_ROW = re.compile(r"^\s*\|?\s*C\d+\s*(?:\||[:–—-])")
PROCESS_NOTE = re.compile(
    r"(?:このstageでは.{0,100}候補|公式記事ページを確認できた候補|"
    r"候補一覧.{0,40}網羅性|検索結果の網羅性)",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """A stable, user-safe JSON contract failure reason."""


def _reject_constant(value: str):
    raise ContractError("json_nonstandard_constant")


def parse_json(raw: str) -> dict:
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except ContractError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContractError("json_invalid") from exc
    if not isinstance(value, dict):
        raise ContractError("json_root_must_be_object")
    return value


def _validate_envelope(envelope: dict) -> tuple[str, str, dict]:
    final_status = envelope.get("final_status")
    if final_status not in {"PUBLISH", "NO_PUBLISH"}:
        raise ContractError("final_status_invalid")
    body = envelope.get("brief_markdown")
    if not isinstance(body, str) or not body.strip():
        raise ContractError("brief_markdown_missing_or_invalid")
    if final_status == "NO_PUBLISH" and body.strip() != "INSUFFICIENT_VERIFIED_ARTICLES":
        raise ContractError("no_publish_brief_invalid")
    if final_status == "PUBLISH" and body.strip() == "INSUFFICIENT_VERIFIED_ARTICLES":
        raise ContractError("publish_brief_is_sentinel")

    block = envelope.get("candidate_diagnostics")
    if not isinstance(block, dict):
        raise ContractError("candidate_diagnostics_missing_or_invalid")
    coverage = block.get("coverage")
    candidates = block.get("candidates")
    if coverage not in ALLOWED_COVERAGE or not isinstance(candidates, list):
        raise ContractError("candidate_diagnostics_schema_invalid")
    try:
        validated = [validate_candidate(item) for item in candidates]
    except ValueError as exc:
        raise ContractError(str(exc)) from exc
    normalized = {"status": "RECORDED", "coverage": coverage, "candidates": validated}
    return final_status, body, normalized


def _table_cells(line: str) -> list[str]:
    if not line.lstrip().startswith("|"):
        return []
    return [cell.strip().strip("`*_").casefold() for cell in line.strip().strip("|").split("|")]


def _is_diagnostic_table_header(lines: list[str], index: int) -> bool:
    cells = _table_cells(lines[index])
    if not cells or index + 1 >= len(lines):
        return False
    separator = _table_cells(lines[index + 1])
    if not separator or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator):
        return False
    joined = " ".join(cells)
    field_header = any(field in joined for field in (
        "candidate_id", "selected_for_top3", "rejection_reason", "source_verified", "within_24h",
    ))
    candidate_id_rows = False
    for row in range(index + 2, len(lines)):
        if not lines[row].lstrip().startswith("|"):
            break
        if ID_ROW.match(lines[row]):
            candidate_id_rows = True
            break
    diagnostic_column = any(any(marker in cell for marker in (
        "candidate", "selected", "rejection", "reason", "24h", "24時間", "採否", "採用", "却下",
        "source_verified", "within_24h",
    )) for cell in cells)
    labeled_candidate_table = (
        any(cell in {"id", "候補id", "候補ＩＤ"} for cell in cells)
        and any("記事" in cell or "article" in cell or "title" in cell for cell in cells)
        and (diagnostic_column or candidate_id_rows)
    )
    return field_header or labeled_candidate_table


def contains_candidate_diagnostics(body: str) -> bool:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if DIAGNOSTIC_HEADING.match(line):
            return True
        if FIELD_LINE.match(line) or COVERAGE_LINE.search(line) or ID_ROW.match(line):
            return True
        if STRUCTURED_FIELD_TOKEN.search(line):
            return True
        if _is_diagnostic_table_header(lines, index):
            return True
        if PROCESS_NOTE.search(line):
            return True
        if re.fullmatch(r"\s*(?:candidate\s+diagnostics?|候補診断|候補一覧)\s*:?\s*", line, re.IGNORECASE):
            return True
    return False


def validate_envelope(envelope: dict) -> tuple[str, str, dict]:
    final_status, body, diagnostics = _validate_envelope(envelope)
    if final_status == "PUBLISH" and contains_candidate_diagnostics(body):
        raise ContractError(DIAGNOSTICS_EMBEDDED)
    return final_status, body, diagnostics


def _is_diagnostic_content_line(lines: list[str], index: int) -> bool:
    line = lines[index]
    return (
        FIELD_LINE.match(line) is not None
        or STRUCTURED_FIELD_TOKEN.search(line) is not None
        or COVERAGE_LINE.search(line) is not None
        or ID_ROW.match(line) is not None
        or PROCESS_NOTE.search(line) is not None
        or _is_diagnostic_table_header(lines, index)
    )


def repair_brief_markdown(body: str) -> str:
    """Remove only structurally identified candidate-diagnostic material."""
    if not contains_candidate_diagnostics(body):
        raise ContractError("contract_repair_not_applicable")
    lines = body.splitlines(keepends=True)
    kept: list[str] = []
    skip_diagnostic_section = False
    skip_table = False
    for index, line in enumerate(lines):
        if ANY_H2.match(line):
            if skip_diagnostic_section:
                skip_diagnostic_section = False
            if DIAGNOSTIC_HEADING.match(line):
                skip_diagnostic_section = True
                continue
        if skip_diagnostic_section:
            continue
        if skip_table:
            if line.lstrip().startswith("|"):
                continue
            skip_table = False
        if _is_diagnostic_table_header(lines, index):
            skip_table = True
            continue
        if _is_diagnostic_content_line(lines, index):
            continue
        kept.append(line)
    repaired = "".join(kept)
    if contains_candidate_diagnostics(repaired):
        raise ContractError(DIAGNOSTICS_EMBEDDED)
    if not repaired.strip():
        raise ContractError("contract_repair_removed_entire_brief")
    return repaired.rstrip() + "\n"


def repair_envelope(envelope: dict) -> dict:
    final_status, body, diagnostics = _validate_envelope(envelope)
    if final_status != "PUBLISH" or not contains_candidate_diagnostics(body):
        raise ContractError("contract_repair_not_applicable")
    repaired = deepcopy(envelope)
    repaired["brief_markdown"] = repair_brief_markdown(body)
    _, _, repaired_diagnostics = validate_envelope(repaired)
    if repaired_diagnostics != diagnostics:
        raise ContractError("contract_repair_changed_candidate_diagnostics")
    return repaired


def validate_and_extract(raw_path: Path, brief_path: Path, candidates_path: Path) -> tuple[str, str]:
    raw = raw_path.read_text(encoding="utf-8")
    envelope = parse_json(raw)
    try:
        status, body, diagnostics = validate_envelope(envelope)
    except ContractError as exc:
        if str(exc) != DIAGNOSTICS_EMBEDDED:
            raise
        _, body, diagnostics = _validate_envelope(envelope)
        brief_path.write_text(body, encoding="utf-8")
        atomic_json(candidates_path, diagnostics)
        return CONTRACT_REPAIRABLE, str(exc)
    brief_path.write_text(body, encoding="utf-8")
    atomic_json(candidates_path, diagnostics)
    return status, "none"


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("json_input", type=Path)
    validate.add_argument("brief_output", type=Path)
    validate.add_argument("candidates_output", type=Path)
    repair = sub.add_parser("repair")
    repair.add_argument("json_input", type=Path)
    repair.add_argument("json_output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "validate":
            status, reason = validate_and_extract(args.json_input, args.brief_output, args.candidates_output)
            print(f"{status} {reason}")
        else:
            envelope = parse_json(args.json_input.read_text(encoding="utf-8"))
            repaired = repair_envelope(envelope)
            args.json_output.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("PASS none")
    except (OSError, UnicodeError, ContractError, ValueError) as exc:
        print(f"FAIL {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
