#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== World Brief ==="

# 仮想環境
source .venv/bin/activate

# 今日の日付
TODAY=$(date +%F)

PROMPT="prompts/daily.md"
OUTPUT="briefs/${TODAY}.md"
TMP="${OUTPUT}.tmp"

echo "Generating brief..."

# Codex実行
/opt/homebrew/bin/codex exec - < "$PROMPT" > "$TMP"

if [ -s "$TMP" ]; then
    mv "$TMP" "$OUTPUT"
    echo "Created $OUTPUT"
else
    echo "Codex returned no output."
    rm -f "$TMP"
    exit 1
fi

echo "Building HTML..."
python scripts/build_html.py

echo "Git commit..."

git add .

# 変更がない場合はコミットしない
if git diff --cached --quiet; then
    echo "No changes."
else
    git commit -m "World Brief ${TODAY}"
    git push
fi

echo
echo "Done!"