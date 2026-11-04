import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.article_completeness_repair import (
    INVALID, PASS, repair_envelope, validate_completeness,
)
from scripts.json_contract import validate_envelope
from scripts.review_draft_gate import validate_text
from scripts.top3_evidence_gate import validate_consistency
from scripts.resume_saved_run import prepare_resume


ROOT = Path(__file__).resolve().parents[1]


def envelope_for_fixture():
    return json.loads((ROOT / "logs/runs/2026-11-04/attempt-1-original-json-output.json").read_text())


def brief_for_fixture():
    return (ROOT / "logs/runs/2026-11-04/attempt-1-original-brief-markdown.md").read_text()


def brief_with_three_articles():
    envelope = envelope_for_fixture()
    body = brief_for_fixture()
    result = repair_envelope(envelope, body)
    assert result
    return result[1]


class ArticleCompletenessRepairTests(unittest.TestCase):
    def test_a_complete_top3_and_three_body_articles_pass(self):
        env = envelope_for_fixture()
        self.assertEqual(validate_completeness(env, brief_with_three_articles()), PASS)

    def test_b_two_body_articles_fail(self):
        self.assertEqual(validate_completeness(envelope_for_fixture(), brief_for_fixture()), INVALID)

    def test_c_two_metadata_blocks_fail(self):
        env = envelope_for_fixture()
        body = brief_for_fixture().replace("情報源：Associated Press\n🔗", "情報源なし\n🔗", 1)
        self.assertEqual(validate_completeness(env, body), INVALID)

    def test_d_missing_c2_reconstructed_from_saved_summary(self):
        env = envelope_for_fixture()
        result = repair_envelope(env, brief_for_fixture())
        self.assertIsNotNone(result)
        repaired = result[1]
        self.assertIn(env["top3"][1]["summary"], repaired)
        self.assertIn(f"英語記事タイトル：{env['top3'][1]['source_title']}", repaired)
        self.assertIn(f"情報源：{env['top3'][1]['source']}", repaired)
        self.assertIn(f"🔗 [原文を読む]({env['top3'][1]['source_url']})", repaired)

    def test_e_binding_changes_are_rejected(self):
        for field, value in (("source_title", "Changed title"), ("source", "Changed source"),
                             ("source_url", "https://example.com/changed")):
            with self.subTest(field=field):
                env = envelope_for_fixture()
                env["top3"][1][field] = value
                self.assertIsNone(repair_envelope(env, brief_for_fixture()))

    def test_f_new_candidate_is_rejected(self):
        env = envelope_for_fixture()
        env["top3"][1]["candidate_id"] = "C99"
        self.assertIsNone(repair_envelope(env, brief_for_fixture()))

    def test_g_missing_verified_summary_fails_closed(self):
        env = envelope_for_fixture()
        env["candidate_diagnostics"]["candidates"][1]["verified_summary"] = None
        self.assertIsNone(repair_envelope(env, brief_for_fixture()))

    def test_h_one_call_is_the_only_repair_operation(self):
        env = envelope_for_fixture()
        result = repair_envelope(env, brief_for_fixture())
        self.assertIsNotNone(result)
        self.assertIsNone(repair_envelope(*result))

    def test_i_2026_11_04_saved_fixture_passes_all_gates_after_repair(self):
        env, body = repair_envelope(envelope_for_fixture(), brief_for_fixture())
        self.assertEqual(validate_envelope(env)[0], "PUBLISH")
        self.assertEqual(validate_completeness(env, body), PASS)
        self.assertEqual(validate_consistency(env, body)["evidence_consistency_result"], "PASS")
        self.assertEqual(validate_text(body, "2026-11-04"), "pass")

    def test_repair_does_not_change_structured_bindings_or_candidates(self):
        env = envelope_for_fixture()
        before = copy.deepcopy((env["top3"], env["candidate_diagnostics"]))
        repaired, _ = repair_envelope(env, brief_for_fixture())
        self.assertEqual((repaired["top3"], repaired["candidate_diagnostics"]), before)

    def test_saved_run_01_can_resume_offline_into_human_review_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = ROOT / "logs/runs/2026-11-04"
            target = root / "logs/runs/2026-11-04"
            target.mkdir(parents=True)
            shutil.copy(source / "diagnostic.json", target / "diagnostic.json")
            shutil.copy(source / "attempt-1-original-json-output.json", target / "attempt-1-original-json-output.json")
            (root / "drafts").mkdir()
            result = prepare_resume(root, "2026-11-04", "run-01", resumed_at="2026-11-04T00:00:00Z")
            self.assertEqual(result["resume_article_completeness_repair"], "PASS")
            draft = Path(result["draft_markdown"]).read_text(encoding="utf-8")
            self.assertEqual(validate_text(draft[draft.index("_Date:"):], "2026-11-04"), "pass")
            self.assertTrue((target / "resume-publication.json").is_file())


if __name__ == "__main__":
    unittest.main()
