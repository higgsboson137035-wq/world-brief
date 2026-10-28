#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() {
    printf 'SAFE_DRAFT_FAILED: %s\n' "$1" >&2
    exit "${2:-1}"
}

TODAY="${WORLD_BRIEF_RUN_DATE:-$(date +%F)}"
RUN_ID="${WORLD_BRIEF_RUN_ID:-run-01}"
RUN_TRIGGER="${WORLD_BRIEF_TRIGGER:-scheduled}"
PREVIOUS_RUN_STATUS="${WORLD_BRIEF_PREVIOUS_RUN_STATUS:-NONE}"
DRAFT_DIR_OVERRIDE="${WORLD_BRIEF_DRAFT_DIR:-}"
if [[ -z "$DRAFT_DIR_OVERRIDE" ]]; then
    DRAFT_DIR="$ROOT/drafts"
else
    case "$DRAFT_DIR_OVERRIDE" in
        drafts/*) ;;
        *) fail "draft_directory_must_be_under_drafts" ;;
    esac
    if [[ -L "$ROOT/drafts" ]]; then fail "draft_directory_symlink_rejected"; fi
    DRAFT_DIR="$ROOT/drafts"
    relative_dir="${DRAFT_DIR_OVERRIDE#drafts/}"
    IFS='/' read -r -a draft_parts <<< "$relative_dir"
    for part in "${draft_parts[@]}"; do
        if [[ -z "$part" || "$part" == "." || "$part" == ".." ]]; then
            fail "draft_directory_path_invalid"
        fi
        DRAFT_DIR="$DRAFT_DIR/$part"
        if [[ -L "$DRAFT_DIR" ]]; then fail "draft_directory_symlink_rejected"; fi
        if [[ -e "$DRAFT_DIR" && ! -d "$DRAFT_DIR" ]]; then fail "draft_directory_not_a_directory"; fi
        if [[ ! -d "$DRAFT_DIR" ]]; then mkdir "$DRAFT_DIR" || fail "draft_directory_create_failed"; fi
    done
fi
DRAFT_MD="$DRAFT_DIR/${TODAY}-human-review.md"
DRAFT_HTML="$DRAFT_DIR/${TODAY}-human-review.html"
PROMPT="$ROOT/prompts/daily_review_draft.md"
CODEX_BIN="${WORLD_BRIEF_CODEX_BIN:-/opt/homebrew/bin/codex}"
PYTHON_BIN="${WORLD_BRIEF_PYTHON_BIN:-$ROOT/.venv/bin/python}"
MAX_OUTPUT_BYTES=1048576
CODEX_TIMEOUT_SECONDS=1200

if [[ ! -d "$DRAFT_DIR" ]]; then fail "draft_directory_missing"; fi
if [[ ! -f "$PROMPT" ]]; then fail "prompt_missing"; fi
if [[ ! -x "$CODEX_BIN" ]]; then fail "codex_cli_unavailable"; fi
if [[ ! -x "$PYTHON_BIN" ]]; then fail "project_python_unavailable"; fi
if [[ -e "$DRAFT_MD" || -L "$DRAFT_MD" || -e "$DRAFT_HTML" || -L "$DRAFT_HTML" ]]; then
    fail "draft_already_exists"
fi

WORK_DIR="$(mktemp -d "$DRAFT_DIR/.${TODAY}-review.XXXXXX")" || fail "temporary_directory_unavailable"
trap 'rm -rf "$WORK_DIR"' EXIT
RAW_OUTPUT="$WORK_DIR/codex-output.md"
GATE_OUTPUT="$WORK_DIR/brief-output.md"
CANDIDATE_OUTPUT="$WORK_DIR/candidates.json"
ORIGINAL_OUTPUT="$WORK_DIR/original-brief.md"
REPAIRED_OUTPUT="$WORK_DIR/repaired-brief.md"
ORIGINAL_JSON_OUTPUT="$WORK_DIR/original-json-output.json"
CONTRACT_REPAIRED_JSON="$WORK_DIR/contract-repaired-output.json"
CONTRACT_ORIGINAL_BRIEF="$WORK_DIR/original-brief-markdown.md"
CONTRACT_REPAIRED_BRIEF="$WORK_DIR/contract-repaired-brief-markdown.md"
EVIDENCE_OUTPUT="$WORK_DIR/evidence-consistency.json"
STAGED_MD="$WORK_DIR/review-draft.md"
STAGED_HTML="$WORK_DIR/review-draft.html"
DIAGNOSTICS="$ROOT/scripts/run_diagnostics.py"
FORMAT_REPAIR="$ROOT/scripts/format_repair.py"
JSON_CONTRACT="$ROOT/scripts/json_contract.py"
EVIDENCE_GATE="$ROOT/scripts/top3_evidence_gate.py"
RUN_DIAGNOSTIC_DIR="$ROOT/logs/runs/$TODAY"
if [[ "$RUN_ID" == "run-02" ]]; then RUN_DIAGNOSTIC_DIR="$RUN_DIAGNOSTIC_DIR/run-02"; fi

if [[ ! -f "$RUN_DIAGNOSTIC_DIR/diagnostic.json" ]]; then
    "$PYTHON_BIN" -B "$DIAGNOSTICS" start "$TODAY" --run-id "$RUN_ID" \
        --trigger "$RUN_TRIGGER" --previous-run-status "$PREVIOUS_RUN_STATUS" || fail "diagnostic_run_start_failed"
fi

printf 'Checking network availability...\n'
NETWORK_READY=0
for attempt in {1..12}; do
    if curl -fsS --max-time 5 https://www.apple.com/ >/dev/null 2>&1; then
        NETWORK_READY=1
        break
    fi
    if [[ "$attempt" -lt 12 ]]; then sleep 10; fi
done
if [[ "$NETWORK_READY" -ne 1 ]]; then fail "network_unavailable" 12; fi

validate_brief() {
    local candidate="$1"
    local reason
    if reason="$("$PYTHON_BIN" -B "$ROOT/scripts/review_draft_gate.py" "$candidate" "$TODAY")"; then
        printf '%s' "$reason"
    elif [[ -n "$reason" ]]; then
        printf '%s' "$reason"
    else
        printf 'quality_gate_failed'
    fi
}

run_codex() {
    local rendered_prompt="$1"
    "$PYTHON_BIN" -B - "$CODEX_BIN" "$rendered_prompt" "$RAW_OUTPUT" "$CODEX_TIMEOUT_SECONDS" <<'PY'
import subprocess
import sys

binary, prompt_path, output_path, timeout = sys.argv[1:]
try:
    with open(prompt_path, "rb") as prompt, open(output_path, "wb") as output:
        result = subprocess.run(
            [binary, "--search", "exec", "-"],
            stdin=prompt,
            stdout=output,
            check=False,
            timeout=int(timeout),
        )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except OSError:
    raise SystemExit(125)
raise SystemExit(result.returncode)
PY
}

echo "Generating Human Review draft..."
BRIEF_VALID=0
ATTEMPT=0
for STAGE in GENERAL TRUSTED_SOURCES CATEGORY_EXPANSION; do
    ATTEMPT=$((ATTEMPT + 1))
    RENDERED_PROMPT="$WORK_DIR/prompt-${STAGE}.md"
    "$PYTHON_BIN" -B "$DIAGNOSTICS" render-prompt "$PROMPT" "$RENDERED_PROMPT" "$STAGE" || fail "prompt_stage_render_failed"
    case "$STAGE" in
        GENERAL) STRATEGY=GENERAL; QUERY_FAMILY=general_world_news ;;
        TRUSTED_SOURCES) STRATEGY=TRUSTED_SOURCES; QUERY_FAMILY=source_targeted_AP_REUTERS_BBC ;;
        CATEGORY_EXPANSION) STRATEGY=CATEGORY_EXPANSION; QUERY_FAMILY='geopolitics_conflict|world_politics_diplomacy|economy_energy|disasters|security|science_technology' ;;
    esac
    : > "$RAW_OUTPUT"
    RESPONSE_STATUS="UNKNOWN"
    CANDIDATE_STATUS="UNKNOWN"
    SENTINEL_SEEN="false"
    JSON_CONTRACT_RESULT="NOT_RUN"
    JSON_CONTRACT_REASON="NOT_RUN"
    CONTRACT_REPAIR_ATTEMPTED=false
    CONTRACT_REPAIR_RESULT=NOT_ATTEMPTED
    CONTRACT_REPAIR_REASON=NOT_ATTEMPTED
    EVIDENCE_CONSISTENCY_RESULT=NOT_RUN
    EVIDENCE_CONSISTENCY_REASON=NOT_RUN
    QUALITY_GATE_RAN=false
    VALIDATED_JSON="$RAW_OUTPUT"
    if run_codex "$RENDERED_PROMPT"; then
        CODEX_EXIT=0
    else
        CODEX_EXIT=$?
    fi

    if [[ "$CODEX_EXIT" -ne 0 ]]; then
        FAILURE_REASON="codex_exit_nonzero"
        : > "$GATE_OUTPUT"
        printf '{"status":"UNKNOWN","coverage":"UNKNOWN","candidates":[]}\n' > "$CANDIDATE_OUTPUT"
        GATE_RESULT="FAIL"
    else
        cp "$RAW_OUTPUT" "$ORIGINAL_JSON_OUTPUT" || fail "original_json_save_failed"
        CONTRACT_INFO="$("$PYTHON_BIN" -B "$JSON_CONTRACT" validate "$RAW_OUTPUT" \
            "$CONTRACT_ORIGINAL_BRIEF" "$CANDIDATE_OUTPUT" 2>&1)"
        read -r JSON_CONTRACT_RESULT JSON_CONTRACT_REASON <<< "$CONTRACT_INFO"
        if [[ "$JSON_CONTRACT_RESULT" == "CONTRACT_REPAIRABLE" ]]; then
            CONTRACT_REPAIR_ATTEMPTED=true
            CONTRACT_REPAIR_RESULT=FAIL
            CONTRACT_REPAIR_REASON="$JSON_CONTRACT_REASON"
            if REPAIR_INFO="$("$PYTHON_BIN" -B "$JSON_CONTRACT" repair "$RAW_OUTPUT" \
                "$CONTRACT_REPAIRED_JSON" 2>&1)"; then
                read -r CONTRACT_REPAIR_RESULT REPAIR_REASON <<< "$REPAIR_INFO"
            else
                REPAIR_REASON="$REPAIR_INFO"
            fi
            if [[ "$CONTRACT_REPAIR_RESULT" == "PASS" ]]; then
                REVALIDATE_INFO="$("$PYTHON_BIN" -B "$JSON_CONTRACT" validate \
                    "$CONTRACT_REPAIRED_JSON" "$CONTRACT_REPAIRED_BRIEF" "$CANDIDATE_OUTPUT" 2>&1)"
                read -r REVALIDATE_RESULT REVALIDATE_REASON <<< "$REVALIDATE_INFO"
                if [[ "$REVALIDATE_RESULT" == "PUBLISH" ]]; then
                    JSON_CONTRACT_RESULT="PASS"
                    JSON_CONTRACT_REASON="${CONTRACT_REPAIR_REASON}_repaired"
                    CONTRACT_REPAIR_REASON="$CONTRACT_REPAIR_REASON"
                    VALIDATED_JSON="$CONTRACT_REPAIRED_JSON"
                    cp "$CONTRACT_REPAIRED_BRIEF" "$GATE_OUTPUT" || fail "contract_repaired_brief_copy_failed"
                    RESPONSE_STATUS="PUBLISH"
                    CANDIDATE_STATUS="RECORDED"
                else
                    JSON_CONTRACT_RESULT="FAIL"
                    JSON_CONTRACT_REASON="${REVALIDATE_REASON:-contract_repair_validation_failed}"
                    CONTRACT_REPAIR_RESULT="FAIL"
                    CONTRACT_REPAIR_REASON="$JSON_CONTRACT_REASON"
                    FAILURE_REASON="FAILED_JSON_CONTRACT"
                    GATE_RESULT="FAIL"
                    : > "$GATE_OUTPUT"
                fi
            else
                JSON_CONTRACT_RESULT="FAIL"
                JSON_CONTRACT_REASON="$CONTRACT_REPAIR_REASON"
                CONTRACT_REPAIR_REASON="${REPAIR_REASON:-contract_repair_failed}"
                FAILURE_REASON="FAILED_JSON_CONTRACT"
                GATE_RESULT="FAIL"
                : > "$GATE_OUTPUT"
            fi
        elif [[ "$JSON_CONTRACT_RESULT" == "PUBLISH" || "$JSON_CONTRACT_RESULT" == "NO_PUBLISH" ]]; then
            RESPONSE_STATUS="$JSON_CONTRACT_RESULT"
            JSON_CONTRACT_RESULT="PASS"
            if [[ "$JSON_CONTRACT_REASON" == "none" ]]; then JSON_CONTRACT_REASON="none"; fi
            cp "$CONTRACT_ORIGINAL_BRIEF" "$GATE_OUTPUT" || fail "brief_extract_failed"
            CANDIDATE_STATUS="RECORDED"
        else
            JSON_CONTRACT_RESULT="FAIL"
            JSON_CONTRACT_REASON="${JSON_CONTRACT_REASON:-json_contract_invalid}"
            FAILURE_REASON="FAILED_JSON_CONTRACT"
            GATE_RESULT="FAIL"
            : > "$GATE_OUTPUT"
            printf '{"status":"UNKNOWN","coverage":"UNKNOWN","candidates":[]}\n' > "$CANDIDATE_OUTPUT"
        fi
        if [[ "$JSON_CONTRACT_RESULT" == "PASS" ]]; then
            if [[ "$RESPONSE_STATUS" == "NO_PUBLISH" ]]; then
                FAILURE_REASON="no_publish_insufficient_verified_articles"
                GATE_RESULT="RETRY"
            else
                if EVIDENCE_INFO="$("$PYTHON_BIN" -B "$EVIDENCE_GATE" "$VALIDATED_JSON" \
                    "$GATE_OUTPUT" "$EVIDENCE_OUTPUT" 2>&1)"; then
                    read -r EVIDENCE_CONSISTENCY_RESULT EVIDENCE_CONSISTENCY_REASON <<< "$EVIDENCE_INFO"
                else
                    read -r EVIDENCE_CONSISTENCY_RESULT EVIDENCE_CONSISTENCY_REASON <<< "$EVIDENCE_INFO"
                    FAILURE_REASON="${EVIDENCE_CONSISTENCY_REASON:-top3_evidence_inconsistent}"
                    GATE_RESULT="FAIL"
                fi
                if [[ "$EVIDENCE_CONSISTENCY_RESULT" == "PASS" ]]; then
                    FAILURE_REASON="$(validate_brief "$GATE_OUTPUT")"
                    QUALITY_GATE_RAN=true
                    if [[ "$FAILURE_REASON" == "pass" ]]; then
                        GATE_RESULT="PASS"
                    elif [[ "$FAILURE_REASON" == "no_publish_insufficient_verified_articles" ]]; then
                        GATE_RESULT="RETRY"
                    else
                        GATE_RESULT="FAIL"
                    fi
                fi
            fi
        fi
    fi

    if [[ "$QUALITY_GATE_RAN" == true ]]; then ORIGINAL_GATE_RESULT="$GATE_RESULT"; else ORIGINAL_GATE_RESULT="NOT_RUN"; fi
    ORIGINAL_GATE_REASON="$FAILURE_REASON"
    FORMAT_REPAIR_ATTEMPTED=false
    FORMAT_REPAIR_RESULT=NOT_ATTEMPTED
    REPAIRED_GATE_RESULT=NOT_RUN
    REPAIRED_GATE_REASON=NOT_RUN
    if [[ -f "$GATE_OUTPUT" ]]; then cp "$GATE_OUTPUT" "$ORIGINAL_OUTPUT"; else : > "$ORIGINAL_OUTPUT"; fi

    if [[ "$CODEX_EXIT" -eq 0 ]] && [[ "$QUALITY_GATE_RAN" == true ]] && [[ "$GATE_RESULT" == "FAIL" ]] && \
        [[ "$("$PYTHON_BIN" -B "$FORMAT_REPAIR" classify "$FAILURE_REASON" "$GATE_OUTPUT")" == "FORMAT_REPAIRABLE" ]]; then
        FORMAT_REPAIR_ATTEMPTED=true
        if "$PYTHON_BIN" -B "$FORMAT_REPAIR" repair "$ORIGINAL_OUTPUT" "$REPAIRED_OUTPUT"; then
            FORMAT_REPAIR_RESULT=PASS
            cp "$REPAIRED_OUTPUT" "$GATE_OUTPUT"
            FAILURE_REASON="$(validate_brief "$GATE_OUTPUT")"
            REPAIRED_GATE_REASON="$FAILURE_REASON"
            if [[ "$FAILURE_REASON" == "pass" ]]; then
                GATE_RESULT=PASS
                REPAIRED_GATE_RESULT=PASS
                if ! EVIDENCE_INFO="$("$PYTHON_BIN" -B "$EVIDENCE_GATE" "$VALIDATED_JSON" \
                    "$REPAIRED_OUTPUT" "$EVIDENCE_OUTPUT" 2>&1)"; then
                    read -r EVIDENCE_CONSISTENCY_RESULT EVIDENCE_CONSISTENCY_REASON <<< "$EVIDENCE_INFO"
                    FAILURE_REASON="${EVIDENCE_CONSISTENCY_REASON:-top3_evidence_inconsistent}"
                    GATE_RESULT=FAIL
                else
                    read -r EVIDENCE_CONSISTENCY_RESULT EVIDENCE_CONSISTENCY_REASON <<< "$EVIDENCE_INFO"
                fi
            else
                GATE_RESULT=FAIL
                REPAIRED_GATE_RESULT=FAIL
            fi
        else
            FORMAT_REPAIR_RESULT=FAIL
            FAILURE_REASON="format_repair_failed"
            GATE_RESULT=FAIL
            REPAIRED_GATE_RESULT=FAIL
            REPAIRED_GATE_REASON="$FAILURE_REASON"
        fi
    fi

    if [[ "$RESPONSE_STATUS" == "NO_PUBLISH" || "$FAILURE_REASON" == "no_publish_insufficient_verified_articles" ]]; then
        SENTINEL_SEEN="true"
    fi
    "$PYTHON_BIN" -B "$DIAGNOSTICS" attempt "$TODAY" "$ATTEMPT" "$CODEX_EXIT" \
        "$GATE_RESULT" "$FAILURE_REASON" "$SENTINEL_SEEN" "$CANDIDATE_STATUS" \
        "$RAW_OUTPUT" "$CANDIDATE_OUTPUT" "$GATE_OUTPUT" "$STAGE" "$STRATEGY" "$QUERY_FAMILY" \
        --original-output "$ORIGINAL_OUTPUT" \
        --original-gate-result "$ORIGINAL_GATE_RESULT" \
        --original-gate-reason "$ORIGINAL_GATE_REASON" \
        --format-repair-attempted "$FORMAT_REPAIR_ATTEMPTED" \
        --format-repair-result "$FORMAT_REPAIR_RESULT" \
        --repaired-gate-result "$REPAIRED_GATE_RESULT" \
        --repaired-gate-reason "$REPAIRED_GATE_REASON" \
        --repaired-output "$REPAIRED_OUTPUT" \
        --json-contract-result "$JSON_CONTRACT_RESULT" \
        --json-contract-reason "$JSON_CONTRACT_REASON" \
        --contract-repair-attempted "$CONTRACT_REPAIR_ATTEMPTED" \
        --contract-repair-result "$CONTRACT_REPAIR_RESULT" \
        --contract-repair-reason "$CONTRACT_REPAIR_REASON" \
        --original-json-output "$ORIGINAL_JSON_OUTPUT" \
        --contract-repaired-json "$CONTRACT_REPAIRED_JSON" \
        --contract-original-brief "$CONTRACT_ORIGINAL_BRIEF" \
        --contract-repaired-brief "$CONTRACT_REPAIRED_BRIEF" \
        --evidence-consistency-output "$EVIDENCE_OUTPUT" \
        --run-id "$RUN_ID" || fail "diagnostic_attempt_save_failed"

    if [[ "$FAILURE_REASON" == "pass" ]]; then
        BRIEF_VALID=1
        break
    fi
    if [[ "$FAILURE_REASON" == "no_publish_insufficient_verified_articles" ]]; then
        if [[ "$STAGE" == "CATEGORY_EXPANSION" ]]; then
            printf 'RETRIEVAL_EXHAUSTED reason=search_strategies_exhausted\n'
            exit 11
        fi
        continue
    fi
    if [[ "$CODEX_EXIT" -ne 0 ]]; then
        printf 'Generation stopped at %s: %s (exit=%s)\n' "$STAGE" "$FAILURE_REASON" "$CODEX_EXIT" >&2
        fail "$FAILURE_REASON" 13
    fi
    printf 'Generation stopped at %s: %s\n' "$STAGE" "$FAILURE_REASON" >&2
    fail "$FAILURE_REASON" 13
done

if [[ "$BRIEF_VALID" -ne 1 ]]; then fail "generation_or_quality_gate_failed"; fi

if ! "$PYTHON_BIN" -B - "$GATE_OUTPUT" "$TODAY" "$STAGED_MD" "$STAGED_HTML" "$ROOT/style.css" <<'PY'
from html.parser import HTMLParser
from pathlib import Path
import html
import re
import sys
import markdown

raw_path, today, md_path, html_path, _style_path = sys.argv[1:]
source = Path(raw_path).read_text(encoding="utf-8")
urls = set(re.findall(r"^🔗 \[原文を読む\]\((https://[^)]+)\)[ \t]*$", source, re.MULTILINE))
if re.search(r"<\s*/?\s*[A-Za-z!][^>]*>", source):
    print("render_rejected_raw_html")
    raise SystemExit(1)

allowed_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ol", "ul", "li", "strong", "em", "a", "hr", "br", "blockquote", "code", "pre"}
void_tags = {"hr", "br"}

class Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in allowed_tags:
            raise ValueError("tag")
        if tag == "a":
            values = dict(attrs)
            href = values.get("href", "")
            if set(values) != {"href"} or href not in urls or not href.startswith("https://"):
                raise ValueError("link")
            self.output.append('<a href="' + html.escape(href, quote=True) + '" rel="noopener noreferrer">')
        else:
            if attrs:
                raise ValueError("attribute")
            self.output.append("<" + tag + ">")
        if tag not in void_tags:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in void_tags:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError("nesting")
        self.stack.pop()
        self.output.append("</" + tag + ">")

    def handle_data(self, data):
        self.output.append(html.escape(data))

try:
    body = markdown.markdown(source)
    cleaner = Sanitizer()
    cleaner.feed(body)
    cleaner.close()
    if cleaner.stack:
        raise ValueError("unclosed")
    if not urls or any(not url.startswith("https://") for url in urls):
        raise ValueError("source")
    final_md = (
        "# World Brief — Human確認前・未公開\n\n"
        "> 記事の事実・出典・日時・リンクはHuman未確認です。\n\n"
        + source
    )
    page = (
        "<!doctype html>\n<html lang=\"ja\">\n<head>\n"
        "<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>World Brief — {today} — Human Review</title>\n"
        "<link rel=\"stylesheet\" href=\"../style.css\">\n</head>\n<body>\n<article>\n"
        "<aside style=\"border:2px solid #9a3412;background:#fff7ed;padding:1rem;margin-bottom:1rem\">"
        "<strong>Human確認前・未公開</strong><p>記事の事実・出典・日時・リンクはHuman未確認です。</p></aside>\n"
        + "".join(cleaner.output)
        + "\n</article>\n</body>\n</html>\n"
    )
    Path(md_path).write_text(final_md, encoding="utf-8")
    Path(html_path).write_text(page, encoding="utf-8")
except Exception:
    print("html_render_failed")
    raise SystemExit(1)
PY
then
    fail "html_render_failed"
fi

if [[ ! -s "$STAGED_MD" || ! -s "$STAGED_HTML" ]]; then fail "draft_render_output_missing"; fi
if [[ -e "$DRAFT_MD" || -L "$DRAFT_MD" || -e "$DRAFT_HTML" || -L "$DRAFT_HTML" ]]; then
    fail "draft_already_exists"
fi
if ! mv "$STAGED_MD" "$DRAFT_MD"; then fail "draft_markdown_save_failed"; fi
if ! mv "$STAGED_HTML" "$DRAFT_HTML"; then fail "draft_html_save_failed"; fi

printf 'SAFE-AUTO-DRAFT=READY\nMarkdown: %s\nHTML: %s\n' "$DRAFT_MD" "$DRAFT_HTML"
