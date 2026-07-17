#!/bin/zsh

set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p briefs logs

DATE="$(TZ=Asia/Tokyo date +%F)"
OUTPUT="briefs/$DATE.md"
TEMP_FILE="$(mktemp)"
LOG_FILE="logs/$DATE.log"

echo "Generating World Brief for $DATE..."

if codex exec - < prompts/daily.md > "$TEMP_FILE" 2>> "$LOG_FILE"; then
  if [[ -s "$TEMP_FILE" ]]; then
    mv "$TEMP_FILE" "$OUTPUT"
    echo "Saved to $OUTPUT"
  else
    echo "Error: generated file is empty."
    rm -f "$TEMP_FILE"
    exit 1
  fi
else
  echo "Error: Codex generation failed. Existing brief was not changed."
  rm -f "$TEMP_FILE"
  exit 1
fi