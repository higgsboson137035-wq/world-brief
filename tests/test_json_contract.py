from copy import deepcopy
import json
import unittest

from scripts.json_contract import (
    CONTRACT_REPAIRABLE,
    ContractError,
    DIAGNOSTICS_EMBEDDED,
    contains_candidate_diagnostics,
    repair_envelope,
    validate_envelope,
)


def candidate():
    return {
        "candidate_id": "C1",
        "title": "One verified story",
        "source": "Reuters",
        "url": "https://www.reuters.com/world/one",
        "published_at": "2026-10-27 06:00 UTC",
        "within_24h": True,
        "source_verified": True,
        "duplicate": False,
        "selected_for_top3": True,
        "rejection_reason": None,
    }


def envelope(body):
    return {
        "final_status": "PUBLISH",
        "brief_markdown": body,
        "candidate_diagnostics": {"coverage": "PARTIAL", "candidates": [candidate()]},
    }


class JsonContractTests(unittest.TestCase):
    def setUp(self):
        self.body = (
            "_Date: 2026-10-27_\n\n## Today's Top 3\n\n"
            "1. First story.\n2. Second story.\n3. Third story.\n\n"
            "## Executive Summary\n\nMarkets changed after a central bank decision.\n\n"
            "## Why It Matters\n\nThis affects trade.\n\n"
            "## Big Picture\n\nPolicy and markets interact.\n\n"
            "## Tomorrow's Watchlist\n\n- Meeting\n\n"
            "## 🌍 世界情勢\n\nWorld story.\n\n"
            "## 💹 経済\n\nEconomy story.\n\n"
            "## 🤖 AI・テクノロジー\n\n今回は掲載候補なし\n\n"
            "## 🔬 科学\n\n今回は掲載候補なし\n\n"
            "## 🇯🇵 日本への影響\n\nJapan impact pathway and decision point.\n"
        )

    def test_normal_json_passes_without_repair(self):
        value = envelope(self.body)
        self.assertEqual(validate_envelope(value)[0], "PUBLISH")
        self.assertFalse(contains_candidate_diagnostics(self.body))

    def test_diagnostics_in_dedicated_field_pass(self):
        value = envelope(self.body)
        status, _, saved = validate_envelope(value)
        self.assertEqual(status, "PUBLISH")
        self.assertEqual(saved["candidates"], [candidate()])

    def test_diagnostics_section_is_contract_repairable_and_removed(self):
        contaminated = self.body + (
            "\n## Candidate Diagnostics\n\n"
            "このstageでは候補一覧を確認しました。coverage=PARTIAL\n\n"
            "| ID | 記事 | 公開日時 | 24時間以内 | 採否・理由 |\n"
            "|---|---|---|---|---|\n"
            "| C1 | One verified story | 06:00 | Yes | selected_for_top3 |\n\n"
            "## Coverage Note\n\n検索結果の網羅性は未確認。\n"
        )
        original = envelope(contaminated)
        saved_diagnostics = deepcopy(original["candidate_diagnostics"])
        with self.assertRaisesRegex(ContractError, DIAGNOSTICS_EMBEDDED):
            validate_envelope(original)
        repaired = repair_envelope(original)
        expected_brief = contaminated.split("\n## Candidate Diagnostics", 1)[0].rstrip() + "\n"
        self.assertEqual(repaired["candidate_diagnostics"], saved_diagnostics)
        self.assertEqual(repaired["final_status"], original["final_status"])
        self.assertEqual(repaired["brief_markdown"], expected_brief)
        self.assertNotIn("Candidate Diagnostics", repaired["brief_markdown"])
        self.assertNotIn("coverage=PARTIAL", repaired["brief_markdown"])
        self.assertNotIn("C1 | One verified story", repaired["brief_markdown"])
        self.assertIn("Markets changed after a central bank decision.", repaired["brief_markdown"])
        self.assertFalse(contains_candidate_diagnostics(repaired["brief_markdown"]))

    def test_candidate_table_without_heading_is_repairable(self):
        table = (
            "\n| candidate_id | title | source_verified | selected_for_top3 | rejection_reason |\n"
            "|---|---|---|---|---|\n| C1 | One story | true | true | none |\n"
        )
        original = envelope(self.body.replace("## Executive Summary", "## Executive Summary" + table))
        repaired = repair_envelope(original)
        self.assertNotIn("candidate_id", repaired["brief_markdown"])
        self.assertIn("Markets changed after a central bank decision.", repaired["brief_markdown"])

    def test_unstructured_natural_wording_does_not_trigger_false_positive(self):
        body = self.body.replace(
            "Markets changed after a central bank decision.",
            "Candidates across several regions reflected changing market coverage and policy priorities.",
        )
        self.assertFalse(contains_candidate_diagnostics(body))
        self.assertEqual(validate_envelope(envelope(body))[0], "PUBLISH")

    def test_json_contract_requires_all_envelope_fields_and_candidate_shape(self):
        value = envelope(self.body)
        del value["candidate_diagnostics"]
        with self.assertRaisesRegex(ContractError, "candidate_diagnostics_missing_or_invalid"):
            validate_envelope(value)
        value = envelope(self.body)
        value["candidate_diagnostics"]["candidates"] = [{"candidate_id": "C1"}]
        with self.assertRaisesRegex(ContractError, "candidate_diagnostic_schema_invalid"):
            validate_envelope(value)

    def test_contract_repair_refuses_if_no_diagnostic_material_is_present(self):
        with self.assertRaisesRegex(ContractError, "contract_repair_not_applicable"):
            repair_envelope(envelope(self.body))

    def test_no_publish_json_sentinel_is_valid(self):
        value = envelope("INSUFFICIENT_VERIFIED_ARTICLES")
        value["final_status"] = "NO_PUBLISH"
        value["candidate_diagnostics"] = {"coverage": "UNKNOWN", "candidates": []}
        self.assertEqual(validate_envelope(value)[0], "NO_PUBLISH")


if __name__ == "__main__":
    unittest.main()
