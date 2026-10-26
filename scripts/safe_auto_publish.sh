#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUN_DATE="$(date +%F)"
export WORLD_BRIEF_RUN_DATE="$RUN_DATE"
DIAGNOSTICS="$ROOT/scripts/run_diagnostics.py"
PYTHON_BIN="${WORLD_BRIEF_PYTHON_BIN:-$ROOT/.venv/bin/python}"
DIAGNOSTIC_STARTED=0
PUBLISH_STARTED=0
DRAFT_SAVED=0
COMMIT_CREATED=0
COMMIT_TYPE=""
PUSH_ATTEMPTED=0
PUSH_RESULT="NOT_ATTEMPTED"
GATE_RESULT="UNKNOWN"

finish_run() {
    local status="$1" reason="$2" human_review="${3:-false}"
    local args=("$RUN_DATE" "$status" "$GATE_RESULT" "$reason")
    [[ "$DRAFT_SAVED" -eq 1 ]] && args+=(--draft-saved)
    [[ "$PUBLISH_STARTED" -eq 1 ]] && args+=(--publish-started)
    [[ "$COMMIT_CREATED" -eq 1 ]] && args+=(--commit-created)
    [[ "$PUSH_ATTEMPTED" -eq 1 ]] && args+=(--push-attempted)
    [[ "$PUSH_RESULT" != "NOT_ATTEMPTED" ]] && args+=(--push-result "$PUSH_RESULT")
    [[ -n "$COMMIT_TYPE" ]] && args+=(--commit-type "$COMMIT_TYPE")
    if [[ "$status" == "NO_PUBLISH" ]]; then args+=(--sentinel-seen); fi
    if [[ "$human_review" == "true" ]]; then args+=(--human-review-required); fi
    "$PYTHON_BIN" -B "$DIAGNOSTICS" finish "${args[@]}" || return 1
}

fail() {
    if [[ "$DIAGNOSTIC_STARTED" -eq 1 ]]; then
        finish_run "FAILED" "$1" || printf 'AUTO-PUBLISH=FAILED reason=diagnostic_finalize_failed\n' >&2
    fi
    printf 'AUTO-PUBLISH=FAILED reason=%s\n' "$1" >&2
    exit 1
}

if [[ ! -x "$PYTHON_BIN" ]]; then fail "project_python_unavailable"; fi

if ! "$PYTHON_BIN" -B "$DIAGNOSTICS" start "$RUN_DATE"; then
    fail "diagnostic_run_start_failed"
fi
DIAGNOSTIC_STARTED=1

# Production always uses the normal dated draft location, never a test override.
unset WORLD_BRIEF_DRAFT_DIR
export WORLD_BRIEF_PYTHON_BIN="$PYTHON_BIN"

if /bin/bash "$ROOT/scripts/generate_review_draft.sh"; then
    :
else
    GENERATION_EXIT=$?
    if [[ "$GENERATION_EXIT" -eq 11 ]]; then
        GATE_RESULT="RETRY"
        finish_run "RETRIEVAL_EXHAUSTED" "search_strategies_exhausted" true || fail "diagnostic_finalize_failed"
        printf 'AUTO-PUBLISH=RETRIEVAL_EXHAUSTED reason=search_strategies_exhausted human_review_required=true\n' >&2
        exit 11
    fi
    if [[ "$GENERATION_EXIT" -eq 12 ]]; then
        finish_run "FAILED" "network_unavailable" || fail "diagnostic_finalize_failed"
        printf 'AUTO-PUBLISH=FAILED reason=network_unavailable\n' >&2
        exit 12
    fi
    if [[ "$GENERATION_EXIT" -eq 10 ]]; then
        fail "legacy_no_publish_signal_unexpected"
    fi
    fail "draft_generation_or_quality_gate_failed"
fi
DRAFT_SAVED=1

"$PYTHON_BIN" -B "$DIAGNOSTICS" mark "$RUN_DATE" publish_started true || fail "diagnostic_publish_mark_failed"
PUBLISH_STARTED=1
if PUBLISH_OUTPUT="$("$PYTHON_BIN" -B "$ROOT/scripts/publish_auto_draft.py" 2>&1)"; then
    printf '%s\n' "$PUBLISH_OUTPUT"
else
    printf '%s\n' "$PUBLISH_OUTPUT" >&2
    if [[ "$PUBLISH_OUTPUT" == *"commit_parent_invalid"* || "$PUBLISH_OUTPUT" == *"commit_path_set_invalid"* || "$PUBLISH_OUTPUT" == *"commit_diff_check_failed"* || "$PUBLISH_OUTPUT" == *"worktree_sync_temp_exists"* || "$PUBLISH_OUTPUT" == *"remote_diff_preflight_failed"* || "$PUBLISH_OUTPUT" == *"push_failed_no_retry"* || "$PUBLISH_OUTPUT" == *"push_tracking_ref_not_advanced"* || "$PUBLISH_OUTPUT" == *"push_outgoing_not_empty"* || "$PUBLISH_OUTPUT" == *"push_incoming_not_empty"* ]]; then
        COMMIT_CREATED=1
        COMMIT_TYPE="ARTICLE_PUBLISH"
    fi
    if [[ "$PUBLISH_OUTPUT" == *"push_failed_no_retry"* || "$PUBLISH_OUTPUT" == *"push_tracking_ref_not_advanced"* || "$PUBLISH_OUTPUT" == *"push_outgoing_not_empty"* || "$PUBLISH_OUTPUT" == *"push_incoming_not_empty"* ]]; then
        PUSH_ATTEMPTED=1
        PUSH_RESULT="FAILED"
    fi
    PUBLISH_FAILURE_REASON="draft_publish_failed"
    if [[ "$PUBLISH_OUTPUT" =~ AUTO-PUBLISH=FAILED\ reason=([a-z_]+) ]]; then
        PUBLISH_FAILURE_REASON="${BASH_REMATCH[1]}"
    fi
    fail "$PUBLISH_FAILURE_REASON"
fi
COMMIT_CREATED=1
COMMIT_TYPE="ARTICLE_PUBLISH"
PUSH_ATTEMPTED=1
PUSH_RESULT="PASS"
GATE_RESULT="PASS"
finish_run "PUBLISH" "pass" || fail "diagnostic_finalize_failed"
