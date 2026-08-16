#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== World Brief ==="
echo "Started: $(date)"
echo "Uptime: $(uptime)"

# 仮想環境
source .venv/bin/activate

# Mac再起動直後のネットワーク待機
echo "Checking network..."

NETWORK_READY=0

for i in {1..12}; do
    if curl -fsS --max-time 5 https://www.apple.com/ >/dev/null 2>&1; then
        echo "Network is ready."
        NETWORK_READY=1
        break
    fi

    echo "Network not ready (${i}/12). Retrying in 10 seconds..."
    sleep 10
done

if [ "$NETWORK_READY" -ne 1 ]; then
    echo "Network did not become available."
    exit 1
fi

# 今日の日付
TODAY=$(date +%F)

PROMPT="prompts/daily.md"
OUTPUT="briefs/${TODAY}.md"
TMP="${OUTPUT}.tmp"

echo "Generating brief..."

MAX_ATTEMPTS=3
ATTEMPT=1

while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
    echo "Attempt ${ATTEMPT}/${MAX_ATTEMPTS}..."

    # 前回の一時ファイルを削除
    rm -f "$TMP"

    /opt/homebrew/bin/codex --search exec - < "$PROMPT" > "$TMP"

    if [ ! -s "$TMP" ]; then
        echo "Codex returned no output."

    elif grep -Eq \
        "Top 3.*選定.*見送|Top 3.*選定できません|掲載を見送|ニュース項目を掲載しません" \
        "$TMP"; then
        echo "News retrieval appears incomplete."

    elif ! grep -Eq '^1\.' "$TMP" \
      || ! grep -Eq '^2\.' "$TMP" \
      || ! grep -Eq '^3\.' "$TMP"; then
        echo "Today's Top 3 is incomplete."

    elif awk '
        /^## Today'\''s Top 3$/ { inside=1; next }
        /^## / && inside { exit }
        inside { print }
    ' "$TMP" | grep -q "該当する重要ニュースなし"; then
        echo "Today's Top 3 contains no-news placeholders."

    else
        echo "Brief looks valid."
        break
    fi

    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; then
        if [ "$ATTEMPT" -eq 1 ]; then
            WAIT_SECONDS=30
        else
            WAIT_SECONDS=120
        fi

        echo "Retrying in ${WAIT_SECONDS} seconds..."
        rm -f "$TMP"
        sleep "$WAIT_SECONDS"
    fi

    ATTEMPT=$((ATTEMPT + 1))
done

# 最終チェック
if [ ! -s "$TMP" ]; then
    echo "Codex returned no usable output after ${MAX_ATTEMPTS} attempts."
    rm -f "$TMP"
    exit 1
fi

if ! grep -Eq '^1\.' "$TMP" \
  || ! grep -Eq '^2\.' "$TMP" \
  || ! grep -Eq '^3\.' "$TMP"; then
    echo "News retrieval still failed after ${MAX_ATTEMPTS} attempts."
    rm -f "$TMP"
    exit 1
fi

if grep -Eq \
    "Top 3.*選定.*見送|Top 3.*選定できません|掲載を見送|ニュース項目を掲載しません" \
    "$TMP"; then
    echo "News retrieval still appears incomplete after ${MAX_ATTEMPTS} attempts."
    rm -f "$TMP"
    exit 1
fi

if awk '
    /^## Today'\''s Top 3$/ { inside=1; next }
    /^## / && inside { exit }
    inside { print }
' "$TMP" | grep -q "該当する重要ニュースなし"; then
    echo "News retrieval still contains no-news placeholders after ${MAX_ATTEMPTS} attempts."
    rm -f "$TMP"
    exit 1
fi

mv "$TMP" "$OUTPUT"
echo "Created $OUTPUT"

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