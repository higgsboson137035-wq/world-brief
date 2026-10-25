#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail() {
    printf 'AUTO-PUBLISH=FAILED reason=%s\n' "$1" >&2
    exit 1
}

PYTHON_BIN="${WORLD_BRIEF_PYTHON_BIN:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then fail "project_python_unavailable"; fi

# Production always uses the normal dated draft location, never a test override.
unset WORLD_BRIEF_DRAFT_DIR
export WORLD_BRIEF_PYTHON_BIN="$PYTHON_BIN"

if /bin/bash "$ROOT/scripts/generate_review_draft.sh"; then
    :
else
    GENERATION_EXIT=$?
    if [[ "$GENERATION_EXIT" -eq 10 ]]; then
        if ! "$PYTHON_BIN" -B "$ROOT/scripts/record_no_publish.py" insufficient_verified_articles; then
            fail "no_publish_status_update_failed"
        fi
        # Article shortage is a completed NO_PUBLISH outcome, not a failed run.
        printf 'AUTO-PUBLISH=NO_PUBLISH reason=insufficient_verified_articles\n'
        exit 0
    fi
    fail "draft_generation_or_quality_gate_failed"
fi

if ! "$PYTHON_BIN" -B "$ROOT/scripts/publish_auto_draft.py"; then
    exit 1
fi
