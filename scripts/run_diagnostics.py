#!/usr/bin/env python3
"""Persist one machine-readable SAFE-AUTO run record and attempt artifacts."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "logs" / "runs"
REASONS = {
    "outside_24h", "missing_url", "unverifiable_source", "duplicate_url",
    "duplicate_story", "insufficient_source_detail", "title_invalid",
    "missing_required_section", "japan_impact_gate", "other_quality_gate",
    "top3_not_selected", "model_returned_sentinel", "unknown",
}
FIELDS = (
    "candidate_id", "title", "source", "url", "published_at", "within_24h",
    "source_verified", "duplicate", "selected_for_top3", "rejection_reason",
)
SEARCH_STAGES = {
    "GENERAL": {
        "strategy": "GENERAL",
        "query_family": "general_world_news",
        "next": "TRUSTED_SOURCES",
        "instructions": (
            "このstageでは世界の主要ニュースを広く探す一般検索を行います。"
            "少なくとも地域・テーマの異なる複数の一般検索を行い、記事候補と公式個別記事ページを確認してください。"
            "3件の異なるニュースを公開基準に沿って構成できれば、このstageで終了します。"
        ),
    },
    "TRUSTED_SOURCES": {
        "strategy": "TRUSTED_SOURCES",
        "query_family": "source_targeted_AP_REUTERS_BBC",
        "next": "CATEGORY_EXPANSION",
        "instructions": (
            "このstageでは通常の一般検索を繰り返さず、AP、Reuters、BBCをそれぞれ個別のsource-oriented Web search queryで検索してください。"
            "例：site:apnews.com/world [今日の日付] world news、site:reuters.com/world/ [今日の日付] world news、"
            "site:bbc.com/news/world [今日の日付] latest news。各sourceへの検索を別々に行い、"
            "いずれかのsourceが見つからない・応答しない場合も他sourceの検索を継続します。"
            "3件の異なるニュースを公開基準に沿って構成できれば、このstageで終了します。"
        ),
    },
    "CATEGORY_EXPANSION": {
        "strategy": "CATEGORY_EXPANSION",
        "query_family": "geopolitics_conflict|world_politics_diplomacy|economy_energy|disasters|security|science_technology",
        "next": "NONE",
        "instructions": (
            "このstageでは同じ一般検索や同じsource-only検索を繰り返さず、次のカテゴリを個別のqueryに分けて探索空間を広げてください："
            "geopolitics/conflict、world politics/diplomacy、economy/energy、major disasters、security、science/technology。"
            "各queryには直近24時間の範囲と信頼できる媒体の個別記事を含め、少なくともAP、Reuters、BBCの複数媒体を横断してください。"
            "各候補の記事日時・URL・sourceを確認し、既存の品質基準を満たす3件が揃えば構成してください。"
            "このstageでsentinelを返す場合は、利用可能な検索カテゴリを探索し終えた後に限ります。"
        ),
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_dir(run_date: str, run_id: str | None = None) -> Path:
    run_id = run_id or "run-01"
    if run_id not in {"run-01", "run-02"}:
        raise ValueError("diagnostic_run_id_invalid")
    directory = RUNS / run_date
    return directory if run_id == "run-01" else directory / run_id


def record_path(run_date: str, run_id: str | None = None) -> Path:
    return run_dir(run_date, run_id) / "diagnostic.json"


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temp = Path(name)
    os.close(fd)
    try:
        shutil.copyfile(source, temp)
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def load_record(run_date: str, run_id: str | None = None) -> dict:
    normalized_run_id = run_id or "run-01"
    path = record_path(run_date, normalized_run_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("diagnostic_record_unavailable") from exc
    if (not isinstance(value, dict) or value.get("date") != run_date
            or value.get("run_id", "run-01") != normalized_run_id):
        raise ValueError("diagnostic_record_invalid")
    return value


def select_run(
    run_date: str, allow_rerun: str,
    published_markdown: Path, published_html: Path,
) -> tuple[str, str, str | None]:
    if allow_rerun not in {"0", "1"}:
        raise ValueError("rerun_flag_invalid")
    first_path = record_path(run_date, "run-01")
    second_directory = run_dir(run_date, "run-02")
    if not first_path.exists():
        if allow_rerun == "1":
            raise ValueError("rerun_previous_run_missing")
        if second_directory.exists():
            raise ValueError("diagnostic_run_state_unknown")
        return "run-01", "scheduled", None
    if allow_rerun != "1":
        raise ValueError("diagnostic_run_already_exists")

    previous = load_record(run_date, "run-01")
    if not previous.get("finished_at"):
        raise ValueError("diagnostic_run_in_progress")
    previous_status = previous.get("final_status")
    if previous_status not in {"FAILED", "RETRIEVAL_EXHAUSTED"}:
        raise ValueError("rerun_previous_status_not_eligible")
    if published_markdown.exists() or published_html.exists():
        raise ValueError("published_artifact_exists")
    if second_directory.exists():
        raise ValueError("diagnostic_run_already_exists")
    return "run-02", "human_rerun", previous_status


def start_run(
    run_date: str, run_id: str = "run-01", trigger: str = "scheduled",
    previous_run_status: str | None = None,
) -> None:
    if trigger not in {"scheduled", "human_rerun"}:
        raise ValueError("diagnostic_trigger_invalid")
    if run_id == "run-01":
        if trigger != "scheduled" or previous_run_status is not None:
            raise ValueError("diagnostic_run_metadata_invalid")
    elif run_id == "run-02":
        selected_id, selected_trigger, selected_previous = select_run(
            run_date, "1", ROOT / "briefs" / f"{run_date}.md",
            ROOT / f"{run_date}.html",
        )
        if (selected_id, selected_trigger, selected_previous) != (
            run_id, trigger, previous_run_status
        ):
            raise ValueError("diagnostic_run_metadata_invalid")
    else:
        raise ValueError("diagnostic_run_id_invalid")
    directory = run_dir(run_date, run_id)
    if run_id == "run-02" and directory.exists():
        raise ValueError("diagnostic_run_already_exists")
    directory.mkdir(parents=True, exist_ok=True)
    if record_path(run_date, run_id).exists():
        raise ValueError("diagnostic_run_already_exists")
    value = {
        "schema_version": 2,
        "date": run_date,
        "run_id": run_id,
        "trigger": trigger,
        "previous_run_status": previous_run_status,
        "started_at": now(),
        "finished_at": None,
        "final_status": "FAILED",
        "final_run_reason": "run_incomplete",
        "codex_exit_code": None,
        "attempts": [],
        "attempt_count": 0,
        "sentinel_seen": False,
        "final_gate_result": "UNKNOWN",
        "final_gate_reason": "run_incomplete",
        "original_gate_result": "UNKNOWN",
        "original_gate_reason": "run_incomplete",
        "format_repair_attempted": False,
        "format_repair_result": "NOT_ATTEMPTED",
        "repaired_gate_result": "NOT_RUN",
        "repaired_gate_reason": "NOT_RUN",
        "candidate_diagnostics_status": "UNKNOWN",
        "candidate_diagnostics_coverage": "UNKNOWN",
        "candidate_diagnostics": [],
        "json_contract_result": "NOT_RUN",
        "json_contract_reason": "NOT_RUN",
        "contract_repair_attempted": False,
        "contract_repair_result": "NOT_ATTEMPTED",
        "contract_repair_reason": "NOT_ATTEMPTED",
        "top3_candidate_ids": [],
        "selected_candidate_ids": [],
        "evidence_consistency_result": "NOT_RUN",
        "evidence_consistency_reason": "NOT_RUN",
        "evidence_consistency_details": [],
        "search_stage": None,
        "search_strategy": None,
        "query_family": None,
        "candidate_count": None,
        "human_review_required": False,
        "draft_saved": False,
        "publish_started": False,
        "commit_created": False,
        "commit_type": None,
        "push_attempted": False,
        "push_result": "NOT_ATTEMPTED",
    }
    atomic_json(record_path(run_date, run_id), value)


def record_attempt(
    run_date: str, number: int, codex_exit_code: int, gate_result: str,
    gate_reason: str, sentinel_seen: bool, candidate_status: str,
    raw_output: Path, candidate_output: Path,
    gate_output: Path, search_stage: str, strategy: str, query_family: str,
    original_output: Path | None = None, repaired_output: Path | None = None,
    repair_metadata: dict | None = None, run_id: str = "run-01",
    contract_metadata: dict | None = None,
    original_json_output: Path | None = None,
    contract_repaired_json: Path | None = None,
    contract_original_brief: Path | None = None,
    contract_repaired_brief: Path | None = None,
    evidence_consistency_output: Path | None = None,
) -> None:
    value = load_record(run_date, run_id)
    directory = run_dir(run_date, run_id)
    raw_target = directory / f"attempt-{number}-codex-output.md"
    final_target = directory / f"attempt-{number}-final-output.md"
    candidate_target = directory / f"attempt-{number}-candidates.json"
    gate_target = directory / f"attempt-{number}-gate.json"
    original_target = directory / f"attempt-{number}-original-output.md"
    repaired_target = directory / f"attempt-{number}-repaired-output.md"
    original_json_target = directory / f"attempt-{number}-original-json-output.json"
    contract_json_target = directory / f"attempt-{number}-contract-repaired-json.json"
    contract_original_brief_target = directory / f"attempt-{number}-original-brief-markdown.md"
    contract_repaired_brief_target = directory / f"attempt-{number}-contract-repaired-brief-markdown.md"
    format_repaired_brief_target = directory / f"attempt-{number}-format-repaired-brief-markdown.md"
    evidence_consistency_target = directory / f"attempt-{number}-evidence-consistency.json"
    atomic_copy(raw_output, raw_target)
    atomic_copy(gate_output, final_target)
    if original_json_output is not None and original_json_output.exists():
        atomic_copy(original_json_output, original_json_target)
    if contract_repaired_json is not None and contract_repaired_json.exists():
        atomic_copy(contract_repaired_json, contract_json_target)
    if contract_original_brief is not None and contract_original_brief.exists():
        atomic_copy(contract_original_brief, contract_original_brief_target)
    if contract_repaired_brief is not None and contract_repaired_brief.exists():
        atomic_copy(contract_repaired_brief, contract_repaired_brief_target)
    evidence_consistency = {
        "top3_candidate_ids": [],
        "selected_candidate_ids": [],
        "evidence_consistency_result": "NOT_RUN",
        "evidence_consistency_reason": "NOT_RUN",
        "evidence_consistency_details": [],
    }
    if evidence_consistency_output is not None and evidence_consistency_output.exists():
        atomic_copy(evidence_consistency_output, evidence_consistency_target)
        try:
            evidence_consistency = json.loads(evidence_consistency_target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            evidence_consistency = {
                **evidence_consistency,
                "evidence_consistency_result": "FAIL",
                "evidence_consistency_reason": "evidence_consistency_diagnostics_invalid",
            }
    if original_output is not None and original_output.exists():
        atomic_copy(original_output, original_target)
    try:
        candidates = json.loads(candidate_output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        candidates = {"status": "UNKNOWN", "coverage": "UNKNOWN", "candidates": []}
        candidate_status = "UNKNOWN"
    atomic_json(candidate_target, candidates)
    atomic_json(directory / "candidate-diagnostics.json", candidates)
    atomic_copy(final_target, directory / "final-output.md")
    repair = {
        "original_gate_result": gate_result,
        "original_gate_reason": gate_reason,
        "format_repair_attempted": False,
        "format_repair_result": "NOT_ATTEMPTED",
        "repaired_gate_result": "NOT_RUN",
        "repaired_gate_reason": "NOT_RUN",
    }
    if repair_metadata:
        repair.update(repair_metadata)
    if repair["format_repair_attempted"] and repaired_output is not None and repaired_output.exists():
        atomic_copy(repaired_output, repaired_target)
        atomic_copy(repaired_output, format_repaired_brief_target)
    repair["original_output"] = original_target.name if original_output is not None and original_output.exists() else None
    repair["repaired_output"] = repaired_target.name if repaired_target.exists() else None
    contract = {
        "json_contract_result": "NOT_RUN",
        "json_contract_reason": "NOT_RUN",
        "contract_repair_attempted": False,
        "contract_repair_result": "NOT_ATTEMPTED",
        "contract_repair_reason": "NOT_ATTEMPTED",
    }
    if contract_metadata:
        contract.update(contract_metadata)
    contract.update({
        "original_json_output": original_json_target.name if original_json_target.exists() else None,
        "contract_repaired_json": contract_json_target.name if contract_json_target.exists() else None,
        "contract_original_brief_markdown": (
            contract_original_brief_target.name if contract_original_brief_target.exists() else None
        ),
        "contract_repaired_brief_markdown": (
            contract_repaired_brief_target.name if contract_repaired_brief_target.exists() else None
        ),
        "format_repaired_brief_markdown": (
            format_repaired_brief_target.name if format_repaired_brief_target.exists() else None
        ),
    })
    gate = {
        "attempt": number,
        "search_stage": search_stage,
        "strategy": strategy,
        "query_family": query_family,
        "candidate_count": (len(candidates.get("candidates", []))
                            if candidate_status == "RECORDED" else None),
        "codex_exit_code": codex_exit_code,
        "gate_result": gate_result,
        "gate_reason": gate_reason,
        "sentinel_seen": sentinel_seen,
        "candidate_diagnostics_status": candidate_status,
        **evidence_consistency,
        "evidence_consistency_output": (
            evidence_consistency_target.name if evidence_consistency_target.exists() else None
        ),
        **contract,
        **repair,
    }
    atomic_json(gate_target, gate)
    attempts = [item for item in value.get("attempts", []) if item.get("attempt") != number]
    attempts.append({
        **gate,
        "raw_output": raw_target.name,
        "final_output": final_target.name,
        "gate_record": gate_target.name,
        "candidate_diagnostics": candidate_target.name,
        **repair,
    })
    attempts.sort(key=lambda item: item["attempt"])
    value["attempts"] = attempts
    value["attempt_count"] = len(attempts)
    value["codex_exit_code"] = codex_exit_code
    value["sentinel_seen"] = bool(value.get("sentinel_seen")) or sentinel_seen
    value["final_gate_result"] = gate_result
    value["final_gate_reason"] = gate_reason
    for key in (
        "original_gate_result", "original_gate_reason", "format_repair_attempted",
        "format_repair_result", "repaired_gate_result", "repaired_gate_reason",
    ):
        value[key] = repair[key]
    value["candidate_diagnostics_status"] = candidate_status
    value["candidate_diagnostics"] = candidates.get("candidates", [])
    value["candidate_diagnostics_coverage"] = candidates.get("coverage", "UNKNOWN")
    value["search_stage"] = search_stage
    value["search_strategy"] = strategy
    value["query_family"] = query_family
    value["candidate_count"] = gate["candidate_count"]
    for key in (
        "top3_candidate_ids", "selected_candidate_ids", "evidence_consistency_result",
        "evidence_consistency_reason", "evidence_consistency_details",
    ):
        value[key] = evidence_consistency.get(key)
    for key in (
        "json_contract_result", "json_contract_reason", "contract_repair_attempted",
        "contract_repair_result", "contract_repair_reason", "original_json_output",
        "contract_repaired_json", "contract_original_brief_markdown",
        "contract_repaired_brief_markdown", "format_repaired_brief_markdown",
    ):
        value[key] = contract[key]
    atomic_json(record_path(run_date, run_id), value)


def finalize(run_date: str, args: argparse.Namespace) -> None:
    run_id = getattr(args, "run_id", "run-01")
    value = load_record(run_date, run_id)
    prior_gate_reason = value.get("final_gate_reason", "UNKNOWN")
    final_gate_reason = args.gate_reason
    if (args.final_status == "FAILED"
            and args.gate_reason in {"draft_generation_or_quality_gate_failed", "draft_publish_failed"}
            and prior_gate_reason not in {None, "UNKNOWN", "run_incomplete"}):
        final_gate_reason = prior_gate_reason
    if args.final_status == "FAILED" and not value.get("attempts"):
        final_gate_reason = "UNKNOWN"
    value.update({
        "finished_at": now(),
        "final_status": args.final_status,
        "final_gate_result": args.gate_result,
        "final_gate_reason": final_gate_reason,
        "final_run_reason": args.gate_reason,
        "draft_saved": args.draft_saved,
        "publish_started": args.publish_started,
        "commit_created": args.commit_created,
        "commit_type": args.commit_type,
        "push_attempted": args.push_attempted,
        "push_result": args.push_result,
        "human_review_required": args.human_review_required,
    })
    if args.codex_exit_code is not None:
        value["codex_exit_code"] = args.codex_exit_code
    if args.sentinel_seen:
        value["sentinel_seen"] = True
    atomic_json(record_path(run_date, run_id), value)


def mark(run_date: str, run_id: str = "run-01", **values: object) -> None:
    value = load_record(run_date, run_id)
    value.update(values)
    atomic_json(record_path(run_date, run_id), value)


def validate_candidate(candidate: object) -> dict:
    if not isinstance(candidate, dict) or any(field not in candidate for field in FIELDS):
        raise ValueError("candidate_diagnostic_schema_invalid")
    result = dict(candidate)
    for field in ("candidate_id", "title", "source", "url", "published_at"):
        if result[field] is not None and not isinstance(result[field], str):
            raise ValueError("candidate_diagnostic_schema_invalid")
    for field in ("within_24h", "source_verified", "duplicate", "selected_for_top3"):
        item = result[field]
        if not (type(item) is bool or item == "UNKNOWN"):
            raise ValueError("candidate_diagnostic_schema_invalid")
    reason = result["rejection_reason"]
    if reason is not None and (not isinstance(reason, str) or reason not in REASONS):
        raise ValueError("candidate_diagnostic_reason_invalid")
    return result


def extract_response(raw_path: Path, brief_path: Path, candidates_path: Path) -> tuple[str, str]:
    """Split the JSON response envelope; preserve legacy text as UNKNOWN diagnostics."""
    raw = raw_path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if stripped == "INSUFFICIENT_VERIFIED_ARTICLES":
        brief_path.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        atomic_json(candidates_path, {"status": "UNKNOWN", "coverage": "UNKNOWN", "candidates": []})
        return "NO_PUBLISH", "UNKNOWN"
    if stripped.startswith("{"):
        try:
            envelope = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("diagnostic_output_invalid_json") from exc
        if not isinstance(envelope, dict) or envelope.get("final_status") not in {"PUBLISH", "NO_PUBLISH"}:
            raise ValueError("diagnostic_output_schema_invalid")
        diagnostic_block = envelope.get("candidate_diagnostics")
        if diagnostic_block is None:
            diagnostics = []
            diag_status = "UNKNOWN"
            coverage = "UNKNOWN"
        elif not isinstance(diagnostic_block, dict):
            raise ValueError("diagnostic_output_schema_invalid")
        else:
            diagnostics = diagnostic_block.get("candidates")
            coverage = diagnostic_block.get("coverage", "UNKNOWN")
            if coverage not in {"COMPLETE", "PARTIAL", "UNKNOWN"} or not isinstance(diagnostics, list):
                raise ValueError("diagnostic_output_schema_invalid")
            diagnostics = [validate_candidate(candidate) for candidate in diagnostics]
            diag_status = "RECORDED"
        body = envelope.get("brief_markdown")
        if envelope["final_status"] == "NO_PUBLISH":
            if body not in (None, "INSUFFICIENT_VERIFIED_ARTICLES"):
                raise ValueError("diagnostic_output_status_conflict")
            body = "INSUFFICIENT_VERIFIED_ARTICLES"
        elif not isinstance(body, str):
            raise ValueError("diagnostic_output_brief_missing")
        elif body.strip() == "INSUFFICIENT_VERIFIED_ARTICLES":
            raise ValueError("diagnostic_output_status_conflict")
        brief_path.write_text(body, encoding="utf-8")
        atomic_json(candidates_path, {
            "status": diag_status,
            "coverage": coverage,
            "candidates": diagnostics,
        })
        return envelope["final_status"], diag_status
    # Compatibility for existing model output; diagnostics are explicitly UNKNOWN.
    brief_path.write_text(raw, encoding="utf-8")
    atomic_json(candidates_path, {"status": "UNKNOWN", "coverage": "UNKNOWN", "candidates": []})
    return ("NO_PUBLISH" if stripped == "INSUFFICIENT_VERIFIED_ARTICLES" else "PUBLISH"), "UNKNOWN"


def render_prompt(template_path: Path, output_path: Path, stage: str) -> None:
    try:
        template = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("prompt_unreadable") from exc
    if stage not in SEARCH_STAGES:
        raise ValueError("search_stage_invalid")
    values = SEARCH_STAGES[stage]
    replacements = {
        "__SEARCH_STAGE__": stage,
        "__SEARCH_STRATEGY__": values["strategy"],
        "__SEARCH_QUERY_FAMILY__": values["query_family"],
        "__NEXT_SEARCH_STAGE__": values["next"],
        "__SEARCH_STAGE_INSTRUCTIONS__": values["instructions"],
        "__MAX_SEARCH_STAGE__": str(len(SEARCH_STAGES)),
    }
    for marker, replacement in replacements.items():
        if template.count(marker) != 1:
            raise ValueError("prompt_stage_placeholder_invalid")
        template = template.replace(marker, replacement)
    output_path.write_text(template, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("date")
    start.add_argument("--run-id", choices=("run-01", "run-02"), default="run-01")
    start.add_argument("--trigger", choices=("scheduled", "human_rerun"), default="scheduled")
    start.add_argument("--previous-run-status", choices=("NONE", "FAILED", "RETRIEVAL_EXHAUSTED"), default="NONE")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("date")
    prepare.add_argument("allow_rerun", choices=("0", "1"))
    prepare.add_argument("published_markdown", type=Path)
    prepare.add_argument("published_html", type=Path)
    attempt = sub.add_parser("attempt")
    attempt.add_argument("date")
    attempt.add_argument("number", type=int)
    attempt.add_argument("codex_exit_code", type=int)
    attempt.add_argument("gate_result")
    attempt.add_argument("gate_reason")
    attempt.add_argument("sentinel_seen", choices=("true", "false"))
    attempt.add_argument("candidate_status")
    attempt.add_argument("raw_output", type=Path)
    attempt.add_argument("candidate_output", type=Path)
    attempt.add_argument("gate_output", type=Path)
    attempt.add_argument("search_stage")
    attempt.add_argument("strategy")
    attempt.add_argument("query_family")
    attempt.add_argument("--run-id", choices=("run-01", "run-02"), default="run-01")
    attempt.add_argument("--original-output", type=Path)
    attempt.add_argument("--repaired-output", type=Path)
    attempt.add_argument("--original-gate-result", default="UNKNOWN")
    attempt.add_argument("--original-gate-reason", default="UNKNOWN")
    attempt.add_argument("--format-repair-attempted", choices=("true", "false"), default="false")
    attempt.add_argument("--format-repair-result", default="NOT_ATTEMPTED")
    attempt.add_argument("--repaired-gate-result", default="NOT_RUN")
    attempt.add_argument("--repaired-gate-reason", default="NOT_RUN")
    attempt.add_argument("--json-contract-result", default="NOT_RUN")
    attempt.add_argument("--json-contract-reason", default="NOT_RUN")
    attempt.add_argument("--contract-repair-attempted", choices=("true", "false"), default="false")
    attempt.add_argument("--contract-repair-result", default="NOT_ATTEMPTED")
    attempt.add_argument("--contract-repair-reason", default="NOT_ATTEMPTED")
    attempt.add_argument("--original-json-output", type=Path)
    attempt.add_argument("--contract-repaired-json", type=Path)
    attempt.add_argument("--contract-original-brief", type=Path)
    attempt.add_argument("--contract-repaired-brief", type=Path)
    attempt.add_argument("--evidence-consistency-output", type=Path)
    extract = sub.add_parser("extract")
    extract.add_argument("raw_output", type=Path)
    extract.add_argument("brief_output", type=Path)
    extract.add_argument("candidate_output", type=Path)
    render = sub.add_parser("render-prompt")
    render.add_argument("template", type=Path)
    render.add_argument("output", type=Path)
    render.add_argument("stage", choices=tuple(SEARCH_STAGES))
    marker = sub.add_parser("mark")
    marker.add_argument("date")
    marker.add_argument("key", choices=("publish_started",))
    marker.add_argument("value", choices=("true",))
    marker.add_argument("--run-id", choices=("run-01", "run-02"), default="run-01")
    finish = sub.add_parser("finish")
    finish.add_argument("date")
    finish.add_argument("--run-id", choices=("run-01", "run-02"), default="run-01")
    finish.add_argument("final_status", choices=("PUBLISH", "NO_PUBLISH", "FAILED", "RETRIEVAL_EXHAUSTED"))
    finish.add_argument("gate_result")
    finish.add_argument("gate_reason")
    finish.add_argument("--codex-exit-code", type=int)
    for flag in ("draft-saved", "publish-started", "commit-created", "push-attempted", "sentinel-seen"):
        finish.add_argument(f"--{flag}", action="store_true")
    finish.add_argument("--commit-type", default=None)
    finish.add_argument("--push-result", default="NOT_ATTEMPTED")
    finish.add_argument("--human-review-required", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "start":
            previous = None if args.previous_run_status == "NONE" else args.previous_run_status
            start_run(args.date, args.run_id, args.trigger, previous)
        elif args.command == "prepare":
            run_id, trigger, previous = select_run(
                args.date, args.allow_rerun, args.published_markdown, args.published_html,
            )
            print(f"{run_id} {trigger} {previous or 'NONE'}")
        elif args.command == "attempt":
            repair_metadata = {
                "original_gate_result": args.original_gate_result,
                "original_gate_reason": args.original_gate_reason,
                "format_repair_attempted": args.format_repair_attempted == "true",
                "format_repair_result": args.format_repair_result,
                "repaired_gate_result": args.repaired_gate_result,
                "repaired_gate_reason": args.repaired_gate_reason,
            }
            contract_metadata = {
                "json_contract_result": args.json_contract_result,
                "json_contract_reason": args.json_contract_reason,
                "contract_repair_attempted": args.contract_repair_attempted == "true",
                "contract_repair_result": args.contract_repair_result,
                "contract_repair_reason": args.contract_repair_reason,
            }
            record_attempt(args.date, args.number, args.codex_exit_code, args.gate_result,
                           args.gate_reason, args.sentinel_seen == "true", args.candidate_status,
                           args.raw_output, args.candidate_output, args.gate_output,
                           args.search_stage, args.strategy, args.query_family,
                           args.original_output, args.repaired_output, repair_metadata,
                           args.run_id, contract_metadata, args.original_json_output,
                           args.contract_repaired_json, args.contract_original_brief,
                           args.contract_repaired_brief, args.evidence_consistency_output)
        elif args.command == "extract":
            status, diag = extract_response(args.raw_output, args.brief_output, args.candidate_output)
            print(f"{status} {diag}")
        elif args.command == "mark":
            mark(args.date, args.run_id, **{args.key: args.value == "true"})
        elif args.command == "render-prompt":
            render_prompt(args.template, args.output, args.stage)
        else:
            finalize(args.date, argparse.Namespace(
                run_id=args.run_id,
                final_status=args.final_status, gate_result=args.gate_result,
                gate_reason=args.gate_reason, codex_exit_code=args.codex_exit_code,
                draft_saved=args.draft_saved, publish_started=args.publish_started,
                commit_created=args.commit_created, push_attempted=args.push_attempted,
                push_result=args.push_result, commit_type=args.commit_type,
                sentinel_seen=args.sentinel_seen,
                human_review_required=args.human_review_required,
            ))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
