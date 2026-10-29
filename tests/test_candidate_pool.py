from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.candidate_pool import build_selection_prompt, eligibility, normalized_url, update_pool, validate_selection
from scripts.review_draft_gate import validate_text
from scripts.top3_evidence_gate import validate_consistency
from tests.test_generate_review_draft import make_brief


ROOT = Path(__file__).resolve().parents[1]


def candidate(cid="C1", **changes):
    value = {
        "candidate_id": cid, "title": "Verified news title", "source": "Associated Press",
        "url": f"https://apnews.com/article/{cid}", "published_at": "2026-10-28T04:00:00Z",
        "within_24h": True, "source_verified": True, "duplicate": False,
        "selected_for_top3": False, "rejection_reason": "top3_not_selected",
        "verified_summary": None,
    }
    value.update(changes)
    return value


class CandidatePoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-candidate-pool-")
        self.root = Path(self.temp.name)
        self.pool = self.root / "pool.json"

    def tearDown(self):
        self.temp.cleanup()

    def _update(self, stage, records):
        source = self.root / f"{stage}.json"
        stats = self.root / f"{stage}-stats.json"
        source.write_text(json.dumps({"coverage": "PARTIAL", "candidates": records}), encoding="utf-8")
        result = update_pool(self.pool, source, stage, stats)
        return result

    def test_top3_not_selected_does_not_disqualify_otherwise_eligible_candidate(self):
        self.assertEqual(eligibility(candidate()), ("ELIGIBLE", "eligible"))

    def test_unknown_freshness_is_not_eligible(self):
        self.assertEqual(eligibility(candidate(within_24h="UNKNOWN"))[0], "UNKNOWN")

    def test_unknown_source_verification_is_not_eligible(self):
        self.assertEqual(eligibility(candidate(source_verified="UNKNOWN"))[0], "UNKNOWN")

    def test_explicit_rejection_and_duplicate_are_ineligible(self):
        self.assertEqual(eligibility(candidate(rejection_reason="outside_24h"))[0], "REJECTED")
        self.assertEqual(eligibility(candidate(duplicate=True))[0], "REJECTED")

    def test_missing_required_evidence_is_rejected(self):
        self.assertEqual(eligibility(candidate(url=None))[0], "REJECTED")

    def test_url_normalization_removes_tracking_and_fragment(self):
        self.assertEqual(
            normalized_url("https://www.APNews.com/article/x/?utm_source=x#top"),
            "https://apnews.com/article/x",
        )

    def test_identical_url_across_stages_is_counted_once(self):
        first = candidate("C1")
        one = self._update("GENERAL", [first])
        second = candidate("C1", url="https://www.apnews.com/article/C1/?utm_campaign=brief", title="Updated title")
        two = self._update("CATEGORY_EXPANSION", [second])
        self.assertEqual(one["cumulative_candidate_count"], 1)
        self.assertEqual(two["cumulative_candidate_count"], 1)
        self.assertEqual(two["cumulative_eligible_count"], 1)
        self.assertEqual(two["candidates"][0]["originating_stages"], ["GENERAL", "CATEGORY_EXPANSION"])

    def test_two_plus_one_candidates_reaches_cumulative_three(self):
        self._update("GENERAL", [candidate("C1"), candidate("C2")])
        result = self._update("TRUSTED_SOURCES", [candidate("C1", url="https://apnews.com/article/C3")])
        self.assertEqual(result["cumulative_eligible_count"], 3)
        self.assertEqual(len(result["cumulative_eligible_ids"]), 3)

    def test_2026_10_29_fixture_has_four_cumulative_eligible_candidates(self):
        run_dir = ROOT / "logs/runs/2026-10-29"
        self.assertTrue(run_dir.is_dir(), "saved 2026-10-29 diagnostic fixture missing")
        for number, stage in enumerate(("GENERAL", "TRUSTED_SOURCES", "CATEGORY_EXPANSION"), 1):
            result = self._update(stage, json.loads((run_dir / f"attempt-{number}-candidates.json").read_text(encoding="utf-8"))["candidates"])
        self.assertEqual(result["stage_candidate_count"], 7)
        self.assertEqual(result["stage_eligible_count"], 4)
        self.assertEqual(result["cumulative_eligible_count"], 4)
        self.assertEqual(len(result["cumulative_eligible_ids"]), 4)
        self.assertEqual(result["candidates"][0]["originating_stage"], "GENERAL")

    def test_selection_prompt_contains_only_eligible_evidence_without_search(self):
        self._update("GENERAL", [candidate("C1"), candidate("C2"), candidate("C3")])
        output = self.root / "selection.md"
        build_selection_prompt(self.pool, output, "2026-10-29")
        prompt = output.read_text(encoding="utf-8")
        self.assertIn("Do not use web search", prompt)
        self.assertIn("MUST NOT return INSUFFICIENT_VERIFIED_ARTICLES", prompt)
        self.assertIn("_Date: 2026-10-29_", prompt)
        self.assertIn('"candidate_id": "P1"', prompt)
        payload = prompt.split("ELIGIBLE CANDIDATES (verified information only):", 1)[1]
        self.assertNotIn("originating_stages", payload)
        self.assertNotIn("selected_for_top3", payload)

    def test_2026_10_29_fixture_selection_repair_can_bind_three_and_pass_all_gates(self):
        run_dir = ROOT / "logs/runs/2026-10-29"
        for number, stage in enumerate(("GENERAL", "TRUSTED_SOURCES", "CATEGORY_EXPANSION"), 1):
            fixture = json.loads((run_dir / f"attempt-{number}-candidates.json").read_text(encoding="utf-8"))
            result = self._update(stage, fixture["candidates"])
        pool = result["candidates"]
        self.assertEqual(result["cumulative_eligible_count"], 4)
        selection_prompt = self.root / "fixture-selection-prompt.md"
        build_selection_prompt(self.pool, selection_prompt, "2026-10-29")
        prompt_text = selection_prompt.read_text(encoding="utf-8")
        self.assertIn('"candidate_id": "P1"', prompt_text)
        self.assertIn('"candidate_id": "P12"', prompt_text)
        by_title = {item["title"]: item for item in pool if eligibility(item)[0] == "ELIGIBLE"}
        choices = [
            next(item for title, item in by_title.items() if title.startswith("UN holds annual vote")),
            next(item for title, item in by_title.items() if title.startswith("Cyclone Arnab")),
            next(item for title, item in by_title.items() if title.startswith("Asian stocks rise")),
        ]
        summaries = [
            "国連総会が米国の対キューバ禁輸を非難する年次決議を採択した。",
            "サイクロンArnabがインドのアーンドラ・プラデーシュ州に上陸し、数万人が避難した。",
            "アジア株はウォール街の上昇後に上げ、原油価格も小幅に上昇した。",
        ]
        selected_ids = {item["candidate_id"] for item in choices}
        candidates = []
        for item in pool:
            if eligibility(item)[0] != "ELIGIBLE":
                continue
            record = {key: item.get(key) for key in (
                "candidate_id", "title", "source", "url", "published_at", "within_24h",
                "source_verified", "duplicate", "rejection_reason", "verified_summary",
            )}
            record["selected_for_top3"] = False
            record["rejection_reason"] = "top3_not_selected"
            if item["candidate_id"] in selected_ids:
                index = next(i for i, selected in enumerate(choices) if selected["candidate_id"] == item["candidate_id"])
                record["selected_for_top3"] = True
                record["rejection_reason"] = None
                record["verified_summary"] = summaries[index]
            candidates.append(record)
        selected_order = {candidate_id: index for index, candidate_id in enumerate(
            [item["candidate_id"] for item in choices]
        )}
        candidates.sort(key=lambda item: selected_order.get(item["candidate_id"], 99))
        top3 = [{"candidate_id": item["candidate_id"], "headline": item["title"],
                 "source_title": item["title"], "source": item["source"], "source_url": item["url"],
                 "summary": summaries[index]} for index, item in enumerate(choices)]
        brief = make_brief().replace("_Date: 2026-10-04_", "_Date: 2026-10-29_")
        for index, summary in enumerate(summaries, 1):
            old = ("First distinct event.", "Second distinct event.", "Third distinct event.")[index - 1]
            brief = brief.replace(f"{index}. {old}", f"{index}. {summary}")
        envelope = {"final_status": "PUBLISH", "brief_markdown": brief, "top3": top3,
                    "candidate_diagnostics": {"coverage": "PARTIAL", "candidates": candidates}}
        selection_file = self.root / "fixture-selection.json"
        selection_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(validate_selection(self.pool, selection_file), "PASS")
        evidence_report = validate_consistency(envelope, brief)
        self.assertEqual(evidence_report["evidence_consistency_result"], "PASS", evidence_report)
        self.assertEqual(validate_text(brief, "2026-10-29"), "pass")
        self.assertEqual(len(top3), 3)

    def test_selection_validation_rejects_added_candidate_and_evidence_mutation(self):
        originals = [candidate(f"C{i}") for i in range(1, 4)]
        result = self._update("GENERAL", originals)
        pool_doc = {"candidates": result["candidates"]}
        pool_file = self.root / "pool-for-validation.json"
        pool_file.write_text(json.dumps(pool_doc), encoding="utf-8")
        chosen = result["candidates"]
        output_candidates = []
        top3 = []
        for item in chosen:
            record = {key: item.get(key) for key in (
                "candidate_id", "title", "source", "url", "published_at", "within_24h",
                "source_verified", "duplicate", "selected_for_top3", "rejection_reason", "verified_summary",
            )}
            record["selected_for_top3"] = True
            record["rejection_reason"] = None
            record["verified_summary"] = f"Summary {item['candidate_id']}"
            output_candidates.append(record)
            top3.append({"candidate_id": item["candidate_id"], "headline": item["title"],
                         "source_title": item["title"], "source": item["source"],
                         "source_url": item["url"], "summary": record["verified_summary"]})
        envelope = {"final_status": "PUBLISH", "top3": top3,
                    "candidate_diagnostics": {"coverage": "COMPLETE", "candidates": output_candidates}}
        selection_file = self.root / "selection.json"
        selection_file.write_text(json.dumps(envelope), encoding="utf-8")
        self.assertEqual(validate_selection(pool_file, selection_file), "PASS")
        envelope["candidate_diagnostics"]["candidates"].append(candidate("P999"))
        selection_file.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "selection_candidate_pool_changed"):
            validate_selection(pool_file, selection_file)
        envelope["candidate_diagnostics"]["candidates"].pop()
        envelope["candidate_diagnostics"]["candidates"][0]["url"] = "https://example.com/changed"
        selection_file.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "selection_candidate_url_changed"):
            validate_selection(pool_file, selection_file)

    def test_selection_prompt_refuses_pool_below_three(self):
        self._update("GENERAL", [candidate("C1"), candidate("C2")])
        with self.assertRaisesRegex(ValueError, "selection_pool_below_minimum"):
            build_selection_prompt(self.pool, self.root / "selection.md")


if __name__ == "__main__":
    unittest.main()
