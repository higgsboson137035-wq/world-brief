import copy
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from scripts.article_completeness_repair import PASS as COMPLETENESS_PASS, validate_completeness
from scripts.json_contract import validate_envelope
from scripts.review_draft_gate import validate_text
from scripts.resume_saved_run import prepare_resume
from scripts.top3_evidence_gate import validate_consistency
from scripts.top3_summary_binding_repair import check_summary_binding, repair_summary_binding


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "logs/runs/2026-11-05"


def fixture():
    envelope = json.loads((RUN / "attempt-1-original-json-output.json").read_text(encoding="utf-8"))
    body = (RUN / "attempt-1-original-brief-markdown.md").read_text(encoding="utf-8")
    return envelope, body


class Top3SummaryBindingRepairTests(unittest.TestCase):
    def test_a_exact_verified_summary_binding_passes_without_repair(self):
        envelope, body = fixture()
        envelope, body = repair_summary_binding(envelope, body)
        self.assertIsNone(repair_summary_binding(envelope, body))
        self.assertEqual(validate_consistency(envelope, body)["evidence_consistency_result"], "PASS")

    def test_b_near_meaning_but_different_wording_is_repaired(self):
        envelope, body = fixture()
        result = repair_summary_binding(envelope, body)
        self.assertIsNotNone(result)
        repaired_envelope, repaired_body = result
        section = repaired_body.splitlines()
        top_start = section.index("## Today's Top 3") + 1
        rendered = []
        for line in section[top_start:]:
            if line.startswith("## "):
                break
            match = re.match(r"^[1-3]\. (.*)$", line)
            if match:
                rendered.append(match.group(1))
        for index, item in enumerate(repaired_envelope["top3"]):
            candidate = next(c for c in repaired_envelope["candidate_diagnostics"]["candidates"]
                             if c["candidate_id"] == item["candidate_id"])
            self.assertEqual(item["summary"], candidate["verified_summary"])
            self.assertEqual(rendered[index], candidate["verified_summary"])

    def test_b1_structured_summary_only_mismatch_is_repaired(self):
        envelope, body = fixture()
        envelope, body = repair_summary_binding(envelope, body)
        envelope["top3"][0]["summary"] = "different structured wording"
        repaired, repaired_body = repair_summary_binding(envelope, body)
        self.assertEqual(repaired["top3"][0]["summary"], repaired["candidate_diagnostics"]["candidates"][0]["verified_summary"])
        self.assertEqual(check_summary_binding(repaired, repaired_body), "PASS")
        self.assertEqual(validate_consistency(repaired, repaired_body)["evidence_consistency_result"], "PASS")

    def test_b2_brief_summary_only_mismatch_is_repaired(self):
        envelope, body = fixture()
        envelope, body = repair_summary_binding(envelope, body)
        envelope["brief_markdown"] = body
        mismatched = body.replace("1. " + envelope["top3"][0]["summary"], "1. brief only mismatch", 1)
        repaired, repaired_body = repair_summary_binding(envelope, mismatched)
        self.assertEqual(repaired["top3"][0]["summary"], envelope["top3"][0]["summary"])
        self.assertEqual(check_summary_binding(repaired, repaired_body), "PASS")

    def test_b3_structured_and_brief_mismatches_are_repaired_once(self):
        envelope, body = fixture()
        envelope["top3"][0]["summary"] = "structured mismatch"
        body = body.replace("1. " + envelope["top3"][0]["summary"], "1. brief mismatch", 1)
        repaired, repaired_body = repair_summary_binding(envelope, body)
        self.assertIsNone(repair_summary_binding(repaired, repaired_body))
        self.assertEqual(check_summary_binding(repaired, repaired_body), "PASS")

    def test_c_repair_changes_only_structured_and_rendered_summaries(self):
        envelope, body = fixture()
        before = copy.deepcopy(envelope)
        repaired, repaired_body = repair_summary_binding(envelope, body)
        self.assertEqual(repaired["candidate_diagnostics"], before["candidate_diagnostics"])
        for old, new in zip(before["top3"], repaired["top3"]):
            self.assertEqual({k: v for k, v in old.items() if k != "summary"},
                             {k: v for k, v in new.items() if k != "summary"})
        self.assertEqual(repaired_body.replace("## Today's Top 3", "", 1).replace(
            "\n1. " + repaired["top3"][0]["summary"], "", 1).replace(
            "\n2. " + repaired["top3"][1]["summary"], "", 1).replace(
            "\n3. " + repaired["top3"][2]["summary"], "", 1),
            body.replace("## Today's Top 3", "", 1).replace(
                "\n1. " + before["top3"][0]["summary"], "", 1).replace(
                "\n2. " + before["top3"][1]["summary"], "", 1).replace(
                "\n3. " + before["top3"][2]["summary"], "", 1))

    def test_e_source_headline_url_or_candidate_changes_fail(self):
        for field, value in (("headline", "changed"), ("source_title", "changed"),
                             ("source", "changed"), ("source_url", "https://example.invalid"),
                             ("candidate_id", "C99")):
            with self.subTest(field=field):
                envelope, body = fixture()
                envelope["top3"][0][field] = value
                self.assertIsNone(repair_summary_binding(envelope, body))

    def test_f_verified_summary_is_never_modified_or_inferred(self):
        envelope, body = fixture()
        envelope["candidate_diagnostics"]["candidates"][0]["verified_summary"] = None
        self.assertIsNone(repair_summary_binding(envelope, body))

    def test_g_2026_11_05_fixture_passes_all_gates_after_repairs(self):
        envelope, body = fixture()
        envelope, body = repair_summary_binding(envelope, body)
        self.assertEqual(validate_envelope(envelope)[0], "PUBLISH")
        self.assertEqual(validate_consistency(envelope, body)["evidence_consistency_result"], "PASS")
        if validate_completeness(envelope, body) != COMPLETENESS_PASS:
            envelope, body = self._repair_completeness(envelope, body)
        self.assertEqual(validate_completeness(envelope, body), COMPLETENESS_PASS)
        self.assertEqual(validate_consistency(envelope, body)["evidence_consistency_result"], "PASS")
        self.assertEqual(validate_text(body, "2026-11-05"), "pass")

    @staticmethod
    def _repair_completeness(envelope, body):
        from scripts.article_completeness_repair import repair_envelope
        return repair_envelope(envelope, body)

    def test_h_run01_resume_uses_offline_binding_and_completeness_repairs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "logs/runs/2026-11-05"
            target.mkdir(parents=True)
            shutil.copy(RUN / "diagnostic.json", target / "diagnostic.json")
            shutil.copy(RUN / "attempt-1-original-json-output.json", target / "attempt-1-original-json-output.json")
            (root / "drafts").mkdir()
            result = prepare_resume(root, "2026-11-05", "run-01", resumed_at="2026-11-05T00:00:00Z")
            self.assertEqual(result["resume_top3_summary_binding_repair"], "PASS")
            self.assertEqual(result["resume_article_completeness_repair"], "NOT_NEEDED")
            self.assertEqual(result["resume_article_completeness"], "PASS")
            self.assertEqual(result["resume_evidence_consistency"], "PASS")
            self.assertEqual(result["resume_quality_gate"], "pass")
            self.assertTrue(Path(result["draft_markdown"]).is_file())
            self.assertTrue((target / "resume-publication.json").is_file())

    def test_i_2026_11_18_saved_run_fixture_repairs_offline_through_quality_gate(self):
        run = ROOT / "logs/runs/2026-11-18"
        envelope = json.loads((run / "attempt-1-original-json-output.json").read_text(encoding="utf-8"))
        status, body, _ = validate_envelope(envelope)
        self.assertEqual(status, "PUBLISH")
        self.assertEqual(validate_consistency(envelope, body)["evidence_consistency_result"], "FAIL")
        before_candidates = copy.deepcopy(envelope["candidate_diagnostics"])
        before_items = copy.deepcopy(envelope["top3"])

        repaired = repair_summary_binding(envelope, body)
        self.assertIsNotNone(repaired)
        fixed, fixed_body = repaired
        self.assertEqual(validate_envelope(fixed)[0], "PUBLISH")
        self.assertEqual(check_summary_binding(fixed, fixed_body), "PASS")
        self.assertEqual(validate_consistency(fixed, fixed_body)["evidence_consistency_result"], "PASS")
        self.assertEqual(fixed["candidate_diagnostics"], before_candidates)
        self.assertEqual([x["candidate_id"] for x in fixed["top3"]], [x["candidate_id"] for x in before_items])
        for old, new in zip(before_items, fixed["top3"]):
            self.assertEqual({k: v for k, v in old.items() if k != "summary"},
                             {k: v for k, v in new.items() if k != "summary"})
        for item in fixed["top3"]:
            candidate = next(c for c in fixed["candidate_diagnostics"]["candidates"]
                             if c["candidate_id"] == item["candidate_id"])
            self.assertEqual(item["summary"], candidate["verified_summary"])

        if validate_completeness(fixed, fixed_body) != COMPLETENESS_PASS:
            fixed, fixed_body = self._repair_completeness(fixed, fixed_body)
        self.assertEqual(validate_completeness(fixed, fixed_body), COMPLETENESS_PASS)
        self.assertEqual(validate_consistency(fixed, fixed_body)["evidence_consistency_result"], "PASS")
        self.assertEqual(validate_text(fixed_body, "2026-11-18"), "pass")


if __name__ == "__main__":
    unittest.main()
