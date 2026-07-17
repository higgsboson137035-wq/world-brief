# World Brief

## 概要

World Briefは、英語の主要報道機関の記事をもとに、日本語で作成する世界情勢ブリーフです。

目的は、世界情勢の重要な動きを毎朝5分程度で把握できるようにすることです。

---

## 特徴

- 英語ニュースを中心に情報を収集
- 日本語で簡潔に要約
- 各ニュースの「なぜ重要か」を重視
- 情報源へのリンクを掲載
- 日付ごとのMarkdownとHTMLを保存
- 過去のブリーフをアーカイブ
- GitHub Pagesで公開
- 1つのコマンドで生成・公開

---

## 使い方

プロジェクトのディレクトリへ移動します。

```bash
cd ~/Workspace/world-brief
```

次のコマンドを実行すると、ブリーフの生成からGitHubへの公開までを一括で行います。

```bash
./scripts/generate.sh
```

このスクリプトは、次の処理を順番に実行します。

1. Codexによるブリーフ生成
2. Markdownファイルの保存
3. HTMLページの生成
4. 最新ページとアーカイブの更新
5. Gitへのコミット
6. GitHubへのプッシュ

---

## ディレクトリ構成

```text
world-brief/
├── AGENTS.md
├── README.md
├── prompts/
│   └── daily.md
├── briefs/
│   └── YYYY-MM-DD.md
├── logs/
├── scripts/
│   ├── generate.sh
│   └── build_html.py
├── style.css
├── index.html
├── latest.html
├── archive.html
└── YYYY-MM-DD.html
```

### 主なファイル

- `prompts/daily.md`：Codexに渡す生成指示
- `briefs/`：日付ごとのMarkdown原稿
- `scripts/generate.sh`：生成・HTML化・GitHub公開の一括実行
- `scripts/build_html.py`：MarkdownからWebサイトを生成
- `index.html`：最新ブリーフを表示するトップページ
- `latest.html`：最新ブリーフの単独ページ
- `archive.html`：過去のブリーフ一覧
- `style.css`：サイトのデザイン

---

## 公開

生成したサイトはGitHub Pagesで公開しています。

```text
https://higgsboson137035-wq.github.io/world-brief/
```

`main`ブランチへ変更がプッシュされると、GitHub Pages側にも反映されます。

---

## 必要な環境

- macOS
- Python 3
- Python仮想環境
- Python Markdown
- Git
- GitHub CLI
- Codex CLI

Python仮想環境を初めて作成する場合は、次を実行します。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install markdown
```

---

## 現在実装されている機能

- Codexによる世界情勢ブリーフ生成
- Markdown形式での日次保存
- HTMLへの自動変換
- 最新記事ページの自動更新
- 日付別記事ページの生成
- アーカイブ一覧の自動生成
- トップページへの最新記事表示
- 最終更新日時の表示
- GitコミットとGitHubプッシュの自動化
- GitHub Pagesによる公開

---

## 今後の予定

- 毎朝7時の完全自動実行
- 自動目次
- ダークモード
- スマートフォン表示の改善
- RSSフィード
- タグ・検索機能
- エラー記録と通知