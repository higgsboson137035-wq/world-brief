#!/bin/zsh

set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p briefs

DATE=$(date +%F)

echo "Generating World Brief for $DATE..."

codex exec - < prompts/daily.md > "briefs/$DATE.md"

echo "Saved to briefs/$DATE.md"