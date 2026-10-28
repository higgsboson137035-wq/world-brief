import unittest

from scripts.format_repair import repair_text
from scripts.json_contract import repair_envelope
from scripts.top3_evidence_gate import validate_consistency


def candidate(candidate_id, title, source, url, summary, *, selected=True, duplicate=False, reason=None):
    return {
        "candidate_id": candidate_id,
        "title": title,
        "source": source,
        "url": url,
        "published_at": "2026-10-27 06:00 UTC",
        "verified_summary": summary,
        "within_24h": True,
        "source_verified": True,
        "duplicate": duplicate,
        "selected_for_top3": selected,
        "rejection_reason": reason,
    }


def base_data():
    candidates = [
        candidate("C1", "Localized ceasefire takes effect", "AP News", "https://apnews.com/a", "停戦の実施状況を確認する。"),
        candidate("C2", "Ebola cases pass a milestone", "Reuters", "https://www.reuters.com/b", "感染拡大への対応が焦点となる。"),
        candidate("C3", "Wall Street stocks rise on AI demand", "AP News", "https://apnews.com/c", "米国株式市場ではAI関連株が相場を支えた。"),
    ]
    top3 = [{
        "candidate_id": item["candidate_id"],
        "headline": item["title"],
        "source_title": item["title"],
        "source": item["source"],
        "source_url": item["url"],
        "summary": item["verified_summary"],
    } for item in candidates]
    brief = (
        "_Date: 2026-10-27_\n\n## Today's Top 3\n\n"
        + "\n".join(f"{i}. {item['summary']}" for i, item in enumerate(top3, 1))
        + "\n\n## Executive Summary\n\nSummary.\n\n"
        + "## Why It Matters\n\nReason.\n\n"
        + "## Big Picture\n\nPicture.\n\n"
        + "## Tomorrow's Watchlist\n\n- One.\n- Two.\n- Three.\n\n"
        + "## 🌍 世界情勢\n\nWorld.\n\n## 💹 経済\n\nEconomy.\n\n"
        + "## 🤖 AI・テクノロジー\n\n今回は掲載候補なし\n\n"
        + "## 🔬 科学\n\n今回は掲載候補なし\n\n"
        + "## 🇯🇵 日本への影響\n\nImpact one.\n\nImpact two.\n"
    )
    envelope = {
        "final_status": "PUBLISH",
        "brief_markdown": brief,
        "top3": top3,
        "candidate_diagnostics": {"coverage": "COMPLETE", "candidates": candidates},
    }
    return envelope, brief


class Top3EvidenceGateTests(unittest.TestCase):
    def test_three_top3_items_exactly_match_selected_candidates(self):
        envelope, brief = base_data()
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_result"], "PASS")
        self.assertEqual(report["top3_candidate_ids"], ["C1", "C2", "C3"])
        self.assertEqual(report["selected_candidate_ids"], ["C1", "C2", "C3"])

    def test_missing_candidate_id_fails(self):
        envelope, brief = base_data()
        envelope["top3"][0]["candidate_id"] = "C99"
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_candidate_missing")

    def test_unselected_candidate_fails(self):
        envelope, brief = base_data()
        envelope["candidate_diagnostics"]["candidates"][0]["selected_for_top3"] = False
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_candidate_not_selected")

    def test_duplicate_selected_candidate_fails(self):
        envelope, brief = base_data()
        envelope["candidate_diagnostics"]["candidates"][0]["duplicate"] = True
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_candidate_rejected")

    def test_rejection_reason_on_selected_candidate_fails(self):
        envelope, brief = base_data()
        envelope["candidate_diagnostics"]["candidates"][0]["rejection_reason"] = "duplicate_story"
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_candidate_rejected")

    def test_source_url_mismatch_fails(self):
        envelope, brief = base_data()
        envelope["top3"][1]["source_url"] = "https://example.com/wrong"
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_source_url_mismatch")

    def test_source_mismatch_fails(self):
        envelope, brief = base_data()
        envelope["top3"][1]["source"] = "BBC"
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_evidence_inconsistent")

    def test_source_title_mismatch_fails(self):
        envelope, brief = base_data()
        envelope["top3"][2]["headline"] = "South Korea economy grows faster than expected"
        envelope["top3"][2]["source_title"] = "South Korea economy grows faster than expected"
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_reason"], "top3_source_title_mismatch")
        self.assertEqual(report["evidence_consistency_details"][-1]["candidate_title"], "Wall Street stocks rise on AI demand")

    def test_korea_gdp_summary_bound_to_us_stock_candidate_fails(self):
        envelope, _ = base_data()
        wrong = "韓国の7〜9月期GDP成長率が市場予想を上回った。"
        envelope["top3"][2]["summary"] = wrong
        brief = envelope["brief_markdown"].replace("米国株式市場ではAI関連株が相場を支えた。", wrong)
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_result"], "FAIL")
        self.assertEqual(report["evidence_consistency_reason"], "top3_evidence_inconsistent")
        self.assertTrue(any(d.get("candidate_verified_summary") != d.get("top3_summary")
                            for d in report["evidence_consistency_details"]))

    def test_correctly_selected_korea_candidate_passes(self):
        envelope, _ = base_data()
        candidates = envelope["candidate_diagnostics"]["candidates"]
        candidates[2]["selected_for_top3"] = False
        candidates[2]["rejection_reason"] = "top3_not_selected"
        korea = candidate("C5", "South Korea economy grows faster than expected", "Reuters",
                          "https://www.reuters.com/world/asia/korea-gdp", "韓国のGDP成長率が市場予想を上回った。")
        candidates.append(korea)
        top_item = {
            "candidate_id": "C5", "headline": korea["title"], "source_title": korea["title"],
            "source": korea["source"], "source_url": korea["url"], "summary": korea["verified_summary"],
        }
        envelope["top3"][2] = top_item
        brief = envelope["brief_markdown"].replace(
            "米国株式市場ではAI関連株が相場を支えた。", korea["verified_summary"],
        )
        report = validate_consistency(envelope, brief)
        self.assertEqual(report["evidence_consistency_result"], "PASS")

    def test_contract_and_format_repairs_preserve_candidate_binding(self):
        envelope, brief = base_data()
        envelope["brief_markdown"] = brief + "\n## Candidate Diagnostics\n\ncandidate_id: C1\n"
        repaired_contract = repair_envelope(envelope)
        repaired_brief = repaired_contract["brief_markdown"]
        self.assertEqual(validate_consistency(repaired_contract, repaired_brief)["evidence_consistency_result"], "PASS")
        extra_heading = repaired_brief.replace("## Why It Matters", "## 詳細：アジア\n\nAdditional detail.\n\n## Why It Matters")
        repaired_format = repair_text(extra_heading)
        self.assertIsNotNone(repaired_format)
        self.assertEqual(validate_consistency(repaired_contract, repaired_format)["evidence_consistency_result"], "PASS")
        self.assertEqual(repaired_contract["top3"], envelope["top3"])


if __name__ == "__main__":
    unittest.main()
