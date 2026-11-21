import copy
import json
from pathlib import Path
import unittest

from scripts.article_completeness_repair import validate_completeness
from scripts.candidate_identity_binding import evaluate_identity_binding, repair_identity_binding
from scripts.json_contract import validate_envelope
from scripts.review_draft_gate import validate_text
from scripts.top3_evidence_gate import validate_consistency
from scripts.top3_summary_binding_repair import check_summary_binding


ROOT = Path(__file__).resolve().parents[1]
RUN_21 = ROOT / "logs/runs/2026-11-21"
RUN_18 = ROOT / "logs/runs/2026-11-18"


def saved_run(directory: Path):
    envelope = json.loads((directory / "attempt-1-original-json-output.json").read_text(encoding="utf-8"))
    brief = (directory / "attempt-1-original-brief-markdown.md").read_text(encoding="utf-8")
    return envelope, brief


class CandidateIdentityBindingTests(unittest.TestCase):
    def test_a_correct_ids_pass_without_repair(self):
        envelope, _ = saved_run(RUN_21)
        for item in envelope["top3"]:
            match = next(c for c in envelope["candidate_diagnostics"]["candidates"]
                         if c["url"] == item["source_url"] and c["title"] == item["headline"]
                         and c["source"] == item["source"] and c["selected_for_top3"] is True)
            item["candidate_id"] = match["candidate_id"]
        self.assertEqual(evaluate_identity_binding(envelope)["identity_binding_result"], "PASS")
        self.assertIsNone(repair_identity_binding(envelope))

    def test_b_rank_renumbering_is_repaired_deterministically(self):
        envelope, _ = saved_run(RUN_21)
        self.assertEqual([x["candidate_id"] for x in envelope["top3"]], ["C1", "C2", "C3"])
        result = repair_identity_binding(envelope)
        self.assertIsNotNone(result)
        fixed, report = result
        self.assertEqual(report["original_top3_candidate_ids"], ["C1", "C2", "C3"])
        self.assertEqual(report["repaired_top3_candidate_ids"], ["C3", "C2", "C7"])

    def test_c_unselected_duplicate_does_not_ambiguate_identity(self):
        envelope, _ = saved_run(RUN_21)
        report = evaluate_identity_binding(envelope)
        self.assertEqual(report["identity_binding_reason"], "candidate_id_mismatch")
        fixed, _ = repair_identity_binding(envelope)
        ids = [c["candidate_id"] for c in fixed["top3"]]
        self.assertEqual(ids, ["C3", "C2", "C7"])
        c8 = next(c for c in fixed["candidate_diagnostics"]["candidates"] if c["candidate_id"] == "C8")
        self.assertFalse(c8["selected_for_top3"])
        self.assertTrue(c8["duplicate"])

    def test_d_multiple_selected_metadata_matches_are_rejected(self):
        envelope, _ = saved_run(RUN_21)
        candidates = envelope["candidate_diagnostics"]["candidates"]
        c2 = next(c for c in candidates if c["candidate_id"] == "C2")
        c3 = next(c for c in candidates if c["candidate_id"] == "C3")
        c2.update(title=c3["title"], source=c3["source"], url=c3["url"])
        envelope["top3"][1].update(headline=c3["title"], source_title=c3["title"],
                                  source=c3["source"], source_url=c3["url"])
        self.assertIsNone(repair_identity_binding(envelope))
        self.assertEqual(evaluate_identity_binding(envelope)["identity_binding_reason"],
                         "selected_metadata_match_not_unique")

    def test_e_zero_metadata_matches_are_rejected(self):
        envelope, _ = saved_run(RUN_21)
        envelope["top3"][0]["source_url"] = "https://example.invalid/not-a-candidate"
        self.assertIsNone(repair_identity_binding(envelope))

    def test_f_top3_set_must_equal_selected_set(self):
        envelope, _ = saved_run(RUN_21)
        envelope["top3"][0]["headline"] = "unselected article"
        envelope["top3"][0]["source_title"] = "unselected article"
        self.assertIsNone(repair_identity_binding(envelope))

    def test_g_metadata_must_not_be_rewritten_to_enable_binding(self):
        envelope, _ = saved_run(RUN_21)
        envelope["top3"][0]["source"] = "Other source"
        self.assertIsNone(repair_identity_binding(envelope))

    def test_h_repair_changes_candidate_ids_only(self):
        envelope, _ = saved_run(RUN_21)
        before = copy.deepcopy(envelope)
        fixed, _ = repair_identity_binding(envelope)
        self.assertEqual(fixed["brief_markdown"], before["brief_markdown"])
        self.assertEqual(fixed["candidate_diagnostics"], before["candidate_diagnostics"])
        for old, new in zip(before["top3"], fixed["top3"]):
            self.assertEqual({k: v for k, v in old.items() if k != "candidate_id"},
                             {k: v for k, v in new.items() if k != "candidate_id"})

    def test_i_2026_11_21_fixture_passes_all_downstream_gates(self):
        envelope, brief = saved_run(RUN_21)
        fixed, _ = repair_identity_binding(envelope)
        self.assertEqual(validate_envelope(fixed)[0], "PUBLISH")
        self.assertEqual(evaluate_identity_binding(fixed)["identity_binding_result"], "PASS")
        self.assertEqual(check_summary_binding(fixed, brief), "PASS")
        self.assertEqual(validate_consistency(fixed, brief)["evidence_consistency_result"], "PASS")
        self.assertEqual(validate_completeness(fixed, brief), "PASS")
        self.assertEqual(validate_text(brief, "2026-11-21"), "pass")

    def test_j_2026_11_18_identity_binding_needs_no_repair(self):
        envelope, brief = saved_run(RUN_18)
        self.assertEqual(evaluate_identity_binding(envelope)["identity_binding_result"], "PASS")
        self.assertIsNone(repair_identity_binding(envelope))
        from scripts.top3_summary_binding_repair import repair_summary_binding
        repaired, repaired_brief = repair_summary_binding(envelope, brief)
        self.assertEqual(check_summary_binding(repaired, repaired_brief), "PASS")
        self.assertEqual(validate_consistency(repaired, repaired_brief)["evidence_consistency_result"], "PASS")
        self.assertEqual(validate_completeness(repaired, repaired_brief), "PASS")
        self.assertEqual(validate_text(repaired_brief, "2026-11-18"), "pass")


if __name__ == "__main__":
    unittest.main()
