# AGENTS.md

## Project

World Brief

このプロジェクトは、毎朝配信する世界情勢ブリーフを生成・公開するためのものです。

---

## Goal

英語の信頼できるニュースソースをもとに、日本語で簡潔かつ分析的な世界情勢ブリーフを作成する。

対象読者は、日本のビジネスパーソン。

読了時間は約5分。

The Economist Espresso のような読みやすさを目指す。

---

## Scope

このプロジェクト内のファイルだけを変更すること。

親ディレクトリや他のプロジェクトは変更しない。

---

## News Sources

優先順位

1. Reuters
2. Associated Press (AP)
3. BBC
4. Financial Times
5. Bloomberg
6. The Economist
7. The Wall Street Journal
8. The New York Times
9. Foreign Affairs
10. 各国政府・国際機関の公式発表

日本語記事のみを根拠にしない。

---

## Writing Style

本文は日本語。

簡潔で落ち着いた文章。

事実と分析を区別する。

煽情的な表現は避ける。

各記事について以下を記載する。

- 何が起きた
- なぜ重要か
- 今後の注目点
- 英語記事タイトル
- 情報源
- URL

---

## Output

生成する成果物

- latest.html
- archive.html
- briefs/YYYY-MM-DD.html

---

## Safety

URLや事実を推測して作成しない。

情報が確認できない場合は、その旨を明記する。

---

## Git

変更は小さくまとめる。

コミットメッセージは内容が分かるものにする。

不要なファイルは追加しない。