#!/usr/bin/env python3
"""Rebuild a gated Human Review draft from an explicitly recorded saved run.

This command never searches, invokes Codex, or publishes. Publication remains
the responsibility of publish_auto_draft.py after a separate authorization.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import markdown

try:
    from .json_contract import ContractError, parse_json, validate_envelope
    from .review_draft_gate import validate_text
    from .top3_evidence_gate import validate_consistency
except ImportError:  # Executed as a standalone script.
    from json_contract import ContractError, parse_json, validate_envelope
    from review_draft_gate import validate_text
    from top3_evidence_gate import validate_consistency


RUN_ID_RE = re.compile(r"^run-\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOTICE_MD = "# World Brief — Human確認前・未公開\n\n> 記事の事実・出典・日時・リンクはHuman未確認です。\n\n"
NOTICE_HTML = (
    '<aside style="border:2px solid #9a3412;background:#fff7ed;padding:1rem;margin-bottom:1rem">'
    "<strong>Human確認前・未公開</strong><p>記事の事実・出典・日時・リンクはHuman未確認です。</p></aside>\n"
)
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "ol", "ul", "li",
    "strong", "em", "a", "hr", "br", "blockquote", "code", "pre",
}
VOID_TAGS = {"hr", "br"}
SOURCE_LINK = re.compile(r"^🔗 \[原文を読む\]\((https://[^\s)]+)\)[ \t]*$")


class ResumeFailure(ValueError):
    """A stable refusal reason for an unsafe or invalid resume."""


class _Sanitizer(HTMLParser):
    def __init__(self, urls: set[str]):
        super().__init__(convert_charrefs=True)
        self.urls = urls
        self.output: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            raise ResumeFailure("render_rejected_tag")
        if tag == "a":
            values = dict(attrs)
            href = values.get("href", "")
            if set(values) != {"href"} or href not in self.urls or not href.startswith("https://"):
                raise ResumeFailure("render_rejected_link")
            self.output.append('<a href="' + html.escape(href, quote=True) + '" rel="noopener noreferrer">')
        else:
            if attrs:
                raise ResumeFailure("render_rejected_attribute")
            self.output.append(f"<{tag}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ResumeFailure("render_invalid_nesting")
        self.stack.pop()
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(html.escape(data))


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResumeFailure("saved_run_json_unreadable") from exc
    if not isinstance(value, dict):
        raise ResumeFailure("saved_run_json_invalid")
    return value


def _recorded_payload_path(run_dir: Path, diagnostic: dict) -> Path:
    attempts = diagnostic.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict):
        raise ResumeFailure("saved_run_attempt_record_invalid")
    attempt = attempts[0]
    # Prefer an explicitly recorded successful contract repair, otherwise use
    # the recorded original JSON. Never select artifacts by mtime or glob order.
    if attempt.get("selection_repair_result") == "PASS":
        # The selection repair envelope is the adopted payload only when its
        # diagnostics explicitly point to that output. If that output needed a
        # contract repair, current run records do not retain a definitive
        # adopted-payload pointer, so fail closed instead of guessing.
        if attempt.get("contract_repair_attempted") is True:
            raise ResumeFailure("saved_run_adopted_payload_ambiguous")
        name = attempt.get("selection_repair_output")
    elif attempt.get("contract_repair_result") == "PASS":
        name = attempt.get("contract_repaired_json")
    else:
        name = attempt.get("original_json_output")
    if not isinstance(name, str) or not name:
        raise ResumeFailure("saved_run_payload_pointer_missing")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise ResumeFailure("saved_run_payload_pointer_invalid")
    path = run_dir / relative
    if path.is_symlink() or not path.is_file():
        raise ResumeFailure("saved_run_payload_missing")
    return path


def _render_html(run_date: str, body: str) -> str:
    if re.search(r"<\s*/?\s*[A-Za-z!][^>]*>", body):
        raise ResumeFailure("render_rejected_raw_html")
    urls = {m.group(1) for line in body.splitlines() if (m := SOURCE_LINK.fullmatch(line))}
    if not urls:
        raise ResumeFailure("source_links_missing")
    try:
        rendered = markdown.markdown(body)
        cleaner = _Sanitizer(urls)
        cleaner.feed(rendered)
        cleaner.close()
    except ResumeFailure:
        raise
    except Exception as exc:
        raise ResumeFailure("render_invalid_html") from exc
    if cleaner.stack:
        raise ResumeFailure("render_invalid_html")
    return (
        "<!doctype html>\n<html lang=\"ja\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>World Brief — {run_date} — Human Review</title>\n"
        "<link rel=\"stylesheet\" href=\"../style.css\">\n</head>\n<body>\n<article>\n"
        + NOTICE_HTML + "".join(cleaner.output)
        + "\n</article>\n</body>\n</html>\n"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() or path.is_symlink():
            raise ResumeFailure("resume_output_already_exists")
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def prepare_resume(root: Path, run_date: str, run_id: str, *, output_dir: Path | None = None,
                   resumed_at: str | None = None) -> dict:
    """Revalidate a saved failed run and reconstruct ordinary review drafts."""
    root = root.resolve()
    if not DATE_RE.fullmatch(run_date):
        raise ResumeFailure("date_invalid")
    try:
        date.fromisoformat(run_date)
    except ValueError as exc:
        raise ResumeFailure("date_invalid") from exc
    if not RUN_ID_RE.fullmatch(run_id):
        raise ResumeFailure("run_id_invalid")

    if (root / "briefs" / f"{run_date}.md").exists() or (root / f"{run_date}.html").exists():
        raise ResumeFailure("public_date_artifact_exists")
    if not (root / "drafts").is_dir() or (root / "drafts").is_symlink():
        raise ResumeFailure("draft_directory_missing")
    draft_root = (output_dir or (root / "drafts")).resolve()
    try:
        draft_root.relative_to((root / "drafts").resolve())
    except ValueError as exc:
        raise ResumeFailure("draft_directory_outside_drafts") from exc
    draft_md = draft_root / f"{run_date}-human-review.md"
    draft_html = draft_root / f"{run_date}-human-review.html"
    if any(p.exists() or p.is_symlink() for p in (draft_md, draft_html)):
        raise ResumeFailure("draft_already_exists")

    run_dir = root / "logs" / "runs" / run_date / run_id
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ResumeFailure("saved_run_not_found")
    diagnostic_path = run_dir / "diagnostic.json"
    if diagnostic_path.is_symlink():
        raise ResumeFailure("saved_run_diagnostic_symlink_rejected")
    resume_record_path = run_dir / "resume-publication.json"
    if resume_record_path.exists() or resume_record_path.is_symlink():
        raise ResumeFailure("resume_already_recorded")
    diagnostic = _read_json(diagnostic_path)
    if diagnostic.get("date") != run_date or diagnostic.get("run_id") != run_id:
        raise ResumeFailure("saved_run_identity_mismatch")
    source_status = diagnostic.get("final_status")
    if source_status not in {"FAILED", "RETRIEVAL_EXHAUSTED"}:
        raise ResumeFailure("saved_run_status_not_resumable")
    attempts = diagnostic.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict):
        raise ResumeFailure("saved_run_attempt_record_invalid")
    attempt = attempts[0]
    if attempt.get("codex_exit_code") != 0 or attempt.get("json_contract_result") != "PASS":
        raise ResumeFailure("saved_run_generation_or_contract_not_successful")
    failed_evidence = (
        diagnostic.get("evidence_consistency_result") == "FAIL"
        or attempt.get("evidence_consistency_result") == "FAIL"
        or diagnostic.get("final_gate_reason") == "top3_candidate_not_selected"
        or attempt.get("gate_reason") == "top3_candidate_not_selected"
    )
    if not failed_evidence:
        raise ResumeFailure("saved_run_failure_not_resume_eligible")

    payload_path = _recorded_payload_path(run_dir, diagnostic)
    try:
        envelope = parse_json(payload_path.read_text(encoding="utf-8"))
        final_status, body, _normalized_candidates = validate_envelope(envelope)
    except (OSError, UnicodeError, ContractError, ValueError) as exc:
        raise ResumeFailure(f"json_contract_failed:{exc}") from exc
    if final_status != "PUBLISH":
        raise ResumeFailure("saved_payload_not_publish")

    evidence = validate_consistency(envelope, body)
    if evidence.get("evidence_consistency_result") != "PASS":
        raise ResumeFailure(f"evidence_consistency_failed:{evidence.get('evidence_consistency_reason')}")
    quality = validate_text(body, run_date)
    if quality != "pass":
        raise ResumeFailure(f"quality_gate_failed:{quality}")

    page = _render_html(run_date, body)
    md_content = (NOTICE_MD + body).encode("utf-8")
    html_content = page.encode("utf-8")
    draft_root.mkdir(parents=True, exist_ok=True)
    _atomic_write(draft_md, md_content)
    try:
        _atomic_write(draft_html, html_content)
    except Exception:
        draft_md.unlink(missing_ok=True)
        raise

    timestamp = resumed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record = {
        "schema_version": 1,
        "date": run_date,
        "run_id": run_id,
        "resume_from_run": run_id,
        "resume_source_status": source_status,
        "resumed_at": timestamp,
        "source_json": payload_path.name,
        "source_json_sha256": hashlib.sha256(payload_path.read_bytes()).hexdigest(),
        "resume_json_contract": "PASS",
        "resume_evidence_consistency": evidence.get("evidence_consistency_result"),
        "resume_evidence_consistency_reason": evidence.get("evidence_consistency_reason"),
        "resume_quality_gate": quality,
        "draft_saved": True,
        "publish_started": False,
        "commit_created": False,
        "push_attempted": False,
        "final_status": "RESUME_DRAFT_READY",
    }
    try:
        _atomic_write(resume_record_path, (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except Exception:
        draft_md.unlink(missing_ok=True)
        draft_html.unlink(missing_ok=True)
        raise
    return {**record, "draft_markdown": str(draft_md), "draft_html": str(draft_html)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="saved run date (YYYY-MM-DD)")
    parser.add_argument("run_id", help="saved run id, for example run-02")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        result = prepare_resume(root, args.date, args.run_id)
    except ResumeFailure as exc:
        print(f"RESUME=FAILED reason={exc}", file=sys.stderr)
        return 1
    print(f"RESUME=READY date={result['date']} run={result['run_id']}")
    print(f"Markdown: {result['draft_markdown']}")
    print(f"HTML: {result['draft_html']}")
    print(f"Provenance: {root / 'logs' / 'runs' / result['date'] / result['run_id'] / 'resume-publication.json'}")
    print("Publication: NOT_STARTED (invoke the existing publisher only after separate authorization)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
