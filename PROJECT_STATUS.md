# Project Status

```json project-dashboard-v1
{
  "schema_version": 1,
  "project_id": "world-brief",
  "name": "World Brief",
  "status": "観察期間",
  "progress_percent": 85,
  "priority": "高",
  "current_work": "v2.2プロンプトの実運用評価",
  "remaining_tasks": [
    "1週間の品質観察",
    "Big Pictureの有効性評価",
    "v2.3改善項目の整理"
  ],
  "estimated_sessions": 1,
  "estimate_confidence": "中",
  "risks": [
    "ニュース品質の日次変動",
    "検索結果への依存",
    "公式ソース未取得時の代替記事選定"
  ],
  "updated_at": "2026-07-23T21:30:00+09:00",
  "data_note": "プロンプトv2.2をGitタグ prompt-v2.2として固定し、実運用による品質評価フェーズへ移行。"
}
```

## 概要

英語の主要報道機関の記事をもとに、日本語で簡潔かつ分析的な世界情勢ブリーフを自動生成・公開するプロジェクトです。

現在は、プロンプト v2.2 を確定し、日々の生成品質を評価する観察期間へ移行しています。

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

### 現在のフェーズ

Prompt v2.2 の観察期間。

同一の課題が複数回確認された場合のみ、v2.3でプロンプトを改訂する。

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