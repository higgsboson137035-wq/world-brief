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

### Primary Sources（原則として使用）

- Reuters
- Associated Press (AP)
- BBC
- Financial Times
- Bloomberg
- The Economist
- The Wall Street Journal
- The New York Times

### Secondary Sources（補足としてのみ使用）

以下は、Primary Sourcesで十分な報道がない場合のみ使用する。

- Foreign Affairs
- Foreign Policy
- 各国政府・国際機関の公式発表

地域紙やローカルメディア（例：Korea Times、Jakarta Postなど）は、
Primary Sourcesで報道されていない固有のニュースに限って引用する。

## Source Selection Rules

- Reutersを第一候補とする。
- Reutersの記事がある場合は、Reutersを引用する。
- BBC、FT、Bloomberg、APはReutersの補強として使用する。
- The Economistは分析記事として使用し、速報の唯一の根拠にはしない。
- 同じ出来事について複数の英語ソースがある場合は、最も権威の高い媒体を採用する。
- 地域紙は例外的な場合のみ使用する。

## News Selection Policy

選定基準は次の順とする。

1. 国際政治への影響
2. 世界経済への影響
3. 日本への影響
4. 軍事・安全保障への影響
5. 長期的な歴史的重要性

話題性だけでは選ばない。

## Freshness

対象は原則として直近24時間に公開または更新された記事とする。

同じ出来事でも、新しい続報がある場合は続報を優先する。

背景説明のために古い記事を引用する場合は、その旨を明記する。

## Diversity

可能な限り、

・米州
・欧州
・中東
・アジア
・国際経済

をバランスよく扱う。

一地域だけで大半を占めないようにする。

## Big Picture

最後に200〜300字程度で、
今日のニュース全体を俯瞰し、

・世界がどちらへ向かっているか
・ニュース同士のつながり
・日本にとっての意味

を整理する。

単なる要約ではなく編集者として分析する。


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

## Editorial Principle

単なるニュースの翻訳ではなく、

「忙しい日本の読者が、
今日の世界を理解するために
本当に知るべきこと」

を編集して届ける。

情報量よりも、
重要性と分かりやすさを優先する。

## Output

日次生成は次の役割分担とする。

1. Codexは完成したMarkdown原稿だけを標準出力へ出力する。
2. Codexはリポジトリ内のファイルを直接作成・編集しない。
3. Codexはシェルコマンドを実行しない。
4. CodexはHTMLを生成・更新しない。
5. `scripts/generate.sh` が標準出力を `briefs/YYYY-MM-DD.md` として保存する。
6. `scripts/build_html.py` がMarkdownから以下のHTML成果物を生成する。

- `latest.html`
- `archive.html`
- `YYYY-MM-DD.html`

日次生成時のCodexの最終回答には、説明、進捗報告、作業宣言、コードブロックを含めず、完成原稿のみを含める。

---

## Safety

URLや事実を推測して作成しない。

情報が確認できない場合は、その旨を明記する。

---

## Git

変更は小さくまとめる。

コミットメッセージは内容が分かるものにする。

不要なファイルは追加しない。