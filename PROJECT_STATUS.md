# Project Status

```json project-dashboard-v1
{
  "schema_version": 1,
  "project_id": "world-brief",
  "name": "World Brief",
  "status": "観察期間",
  "progress_percent": 85,
  "priority": "高",
  "current_work": "v2.3の表示改善後の日次生成・配信運用と品質評価",
  "remaining_tasks": [
    "日次生成品質の継続評価",
    "Big Pictureの有効性評価",
    "毎朝7時の完全自動実行の導入判断"
  ],
  "estimated_sessions": null,
  "estimate_confidence": "未評価",
  "risks": [
    "ニュース品質の日次変動",
    "検索結果への依存",
    "公式ソース未取得時の代替記事選定"
  ],
  "updated_at": "2026-08-04T20:42:04+09:00",
  "data_note": "進捗率85%は既存ステータス値を維持しており、今回再算定していません。2026年8月4日版まで日次生成済みで、v2.3として出典リンクとレスポンシブ表示を改善済みです。generate.shによる一括生成・公開は実装済みですが、毎朝7時の完全自動実行設定はリポジトリ内で確認できません。必要セッション数は未評価です。"
}
```

## 概要

英語の主要報道機関の記事をもとに、日本語で簡潔かつ分析的な世界情勢ブリーフを自動生成・公開するプロジェクトです。

現在は、プロンプト v2.2を基盤に日々の生成品質を評価しながら、v2.3の出典リンクとレスポンシブ表示改善を反映して運用しています。

---

## 現在の状況

### 完了

- 日次自動生成・公開フローを構築
- Markdownベースの生成パイプラインを確立
- 出力構成を固定
- Executive Summaryを追加
- Why It Mattersを追加
- Big Pictureを追加
- Tomorrow's Watchlistを追加
- Reuters・APなど公式ソース優先ルールを整備
- Reuters転載サイト（MarketScreener、Investing.com、StreetInsider等）の排除
- 重複ニュース抑制ルールを整備
- 「日本への影響」を分析中心へ改善
- Prompt v2.2をGitタグ `prompt-v2.2` として保存
- 2026年8月4日版まで日次ブリーフを生成・公開
- v2.3で出典リンク表記とレスポンシブ表示を改善

### 現在のフェーズ

Prompt v2.2を基盤とした日次運用・品質観察期間。

`scripts/generate.sh`による生成からGitHubへの公開までの一括処理は実装済みです。毎朝7時の完全自動実行設定はリポジトリ内で確認できません。

---

## 次の候補

- Big Pictureの品質評価
- HTMLデザイン改善
- 週次・月次ブリーフ生成
- 品質評価指標の追加

---

## 制約

- 情報源は一次情報・公式URLを優先する
- 公式URLを確認できない転載記事は採用しない
- 日本への影響は単なる再掲ではなく分析を重視する
- プロンプトは観察期間中は原則変更しない
