from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import unittest

from scripts import run_diagnostics as diagnostics


class RunDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-diagnostics-")
        self.original_runs = diagnostics.RUNS
        self.original_root = diagnostics.ROOT
        diagnostics.ROOT = Path(self.temp.name) / "project"
        diagnostics.RUNS = diagnostics.ROOT / "logs" / "runs"
        self.run_date = "2026-10-26"

    def tearDown(self):
        diagnostics.RUNS = self.original_runs
        diagnostics.ROOT = self.original_root
        self.temp.cleanup()

    def _published_paths(self):
        return (
            diagnostics.ROOT / "briefs" / f"{self.run_date}.md",
            diagnostics.ROOT / f"{self.run_date}.html",
        )

    def _finish(self, run_id, status):
        diagnostics.finalize(self.run_date, argparse.Namespace(
            run_id=run_id, final_status=status, gate_result="UNKNOWN",
            gate_reason=f"test_{status.lower()}", codex_exit_code=None,
            draft_saved=False, publish_started=False, commit_created=False,
            push_attempted=False, push_result="NOT_ATTEMPTED", commit_type=None,
            sentinel_seen=False, human_review_required=False,
        ))

    def test_missing_candidate_diagnostics_are_explicitly_unknown(self):
        root = Path(self.temp.name)
        raw = root / "raw.md"
        brief = root / "brief.md"
        candidates = root / "candidates.json"
        raw.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        status, diag_status = diagnostics.extract_response(raw, brief, candidates)
        saved = json.loads(candidates.read_text(encoding="utf-8"))
        self.assertEqual(status, "NO_PUBLISH")
        self.assertEqual(diag_status, "UNKNOWN")
        self.assertEqual(saved, {"status": "UNKNOWN", "coverage": "UNKNOWN", "candidates": []})

    def test_invalid_candidate_type_fails_closed(self):
        candidate = {
            "candidate_id": "C1", "title": "Title", "source": "Reuters", "url": "https://example.com",
            "published_at": "UNKNOWN", "within_24h": 1, "source_verified": "UNKNOWN",
            "duplicate": "UNKNOWN", "selected_for_top3": "UNKNOWN", "rejection_reason": "unknown",
        }
        with self.assertRaisesRegex(ValueError, "candidate_diagnostic_schema_invalid"):
            diagnostics.validate_candidate(candidate)

    def test_interrupted_run_is_preserved_as_failed(self):
        diagnostics.start_run(self.run_date)
        path = diagnostics.record_path(self.run_date)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["final_status"], "FAILED")
        self.assertEqual(saved["final_run_reason"], "run_incomplete")
        self.assertEqual(saved["run_id"], "run-01")
        self.assertEqual(saved["trigger"], "scheduled")

    def test_first_run_is_selected_as_scheduled_run01(self):
        selected = diagnostics.select_run(self.run_date, "0", *self._published_paths())
        self.assertEqual(selected, ("run-01", "scheduled", None))

    def test_same_day_second_run_without_flag_is_rejected(self):
        diagnostics.start_run(self.run_date)
        with self.assertRaisesRegex(ValueError, "diagnostic_run_already_exists"):
            diagnostics.select_run(self.run_date, "0", *self._published_paths())

    def test_failed_or_retrieval_exhausted_run_selects_separate_run02(self):
        for status in ("FAILED", "RETRIEVAL_EXHAUSTED"):
            with self.subTest(status=status):
                diagnostics.start_run(self.run_date)
                first = diagnostics.record_path(self.run_date)
                self._finish("run-01", status)
                original = first.read_bytes()
                selected = diagnostics.select_run(self.run_date, "1", *self._published_paths())
                self.assertEqual(selected, ("run-02", "human_rerun", status))
                diagnostics.start_run(self.run_date, "run-02", "human_rerun", status)
                rerun = json.loads(diagnostics.record_path(self.run_date, "run-02").read_text(encoding="utf-8"))
                self.assertEqual(rerun["run_id"], "run-02")
                self.assertEqual(rerun["trigger"], "human_rerun")
                self.assertEqual(rerun["previous_run_status"], status)
                self.assertEqual(first.read_bytes(), original)
                self.assertNotEqual(first.parent, diagnostics.run_dir(self.run_date, "run-02"))
                # Reset the isolated fixture for the second subtest without touching external files.
                diagnostics.RUNS = Path(self.temp.name) / f"runs-{status}"

    def test_published_or_incomplete_first_run_is_not_rerunnable(self):
        diagnostics.start_run(self.run_date)
        with self.assertRaisesRegex(ValueError, "diagnostic_run_in_progress"):
            diagnostics.select_run(self.run_date, "1", *self._published_paths())
        self._finish("run-01", "PUBLISH")
        with self.assertRaisesRegex(ValueError, "rerun_previous_status_not_eligible"):
            diagnostics.select_run(self.run_date, "1", *self._published_paths())

    def test_existing_publication_blocks_failed_run_rerun(self):
        diagnostics.start_run(self.run_date)
        self._finish("run-01", "FAILED")
        markdown, html = self._published_paths()
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text("already published", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "published_artifact_exists"):
            diagnostics.select_run(self.run_date, "1", markdown, html)

    def test_failed_run02_blocks_run03_and_preserves_run01(self):
        diagnostics.start_run(self.run_date)
        first_path = diagnostics.record_path(self.run_date)
        self._finish("run-01", "FAILED")
        original = first_path.read_bytes()
        diagnostics.start_run(self.run_date, "run-02", "human_rerun", "FAILED")
        self._finish("run-02", "FAILED")
        with self.assertRaisesRegex(ValueError, "diagnostic_run_already_exists"):
            diagnostics.select_run(self.run_date, "1", *self._published_paths())
        self.assertTrue(diagnostics.record_path(self.run_date, "run-02").is_file())
        self.assertEqual(first_path.read_bytes(), original)

    def test_human_rerun_requires_existing_failed_run(self):
        with self.assertRaisesRegex(ValueError, "rerun_previous_run_missing"):
            diagnostics.select_run(self.run_date, "1", *self._published_paths())

    def test_invalid_rerun_flag_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "rerun_flag_invalid"):
            diagnostics.select_run(self.run_date, "yes", *self._published_paths())

    def test_search_attempt_fields_and_retrieval_exhausted_are_persisted(self):
        diagnostics.start_run(self.run_date)
        root = Path(self.temp.name)
        raw, candidate, gate = root / "raw.md", root / "candidate.json", root / "gate.md"
        raw.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        candidate.write_text('{"status":"RECORDED","coverage":"PARTIAL","candidates":[]}', encoding="utf-8")
        gate.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        diagnostics.record_attempt(
            self.run_date, 1, 0, "RETRY", "no_publish_insufficient_verified_articles",
            True, "RECORDED", raw, candidate, gate, "TRUSTED_SOURCES",
            "TRUSTED_SOURCES", "source_targeted_AP_REUTERS_BBC",
        )
        diagnostics.finalize(self.run_date, argparse.Namespace(
            final_status="RETRIEVAL_EXHAUSTED", gate_result="RETRY",
            gate_reason="search_strategies_exhausted", codex_exit_code=None,
            draft_saved=False, publish_started=False, commit_created=False,
            push_attempted=False, push_result="NOT_ATTEMPTED", commit_type=None,
            sentinel_seen=True, human_review_required=True,
        ))
        attempt = json.loads((diagnostics.run_dir(self.run_date) / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(attempt["search_stage"], "TRUSTED_SOURCES")
        self.assertEqual(attempt["strategy"], "TRUSTED_SOURCES")
        self.assertEqual(attempt["candidate_count"], 0)
        self.assertEqual(attempt["gate_reason"], "no_publish_insufficient_verified_articles")
        record = json.loads(diagnostics.record_path(self.run_date).read_text(encoding="utf-8"))
        self.assertEqual(record["final_status"], "RETRIEVAL_EXHAUSTED")
        self.assertTrue(record["human_review_required"])

    def test_candidate_pool_and_selection_repair_metadata_are_saved_per_attempt(self):
        diagnostics.start_run(self.run_date)
        root = Path(self.temp.name)
        raw, candidate, gate, pool = [root / name for name in ("raw", "candidate.json", "gate.md", "pool.json")]
        raw.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        candidate.write_text(json.dumps({"status": "RECORDED", "coverage": "PARTIAL", "candidates": []}), encoding="utf-8")
        gate.write_text("INSUFFICIENT_VERIFIED_ARTICLES", encoding="utf-8")
        pool.write_text(json.dumps({"stage_candidate_count": 4, "stage_eligible_count": 4,
                                    "cumulative_candidate_count": 9, "cumulative_eligible_count": 4,
                                    "cumulative_candidate_ids": ["P1", "P2", "P3", "P4"],
                                    "cumulative_eligible_ids": ["P1", "P2", "P3", "P4"], "candidates": []}), encoding="utf-8")
        diagnostics.record_attempt(self.run_date, 1, 0, "FAIL", "selection_repair_failed", True, "RECORDED",
                                   raw, candidate, gate, "CATEGORY_EXPANSION", "CATEGORY_EXPANSION", "categories",
                                   candidate_pool_metadata=pool,
                                   selection_repair_metadata={"selection_inconsistent": True,
                                                              "selection_repair_attempted": True,
                                                              "selection_repair_result": "FAIL",
                                                              "selection_repair_reason": "selection_output_invalid"})
        record = json.loads(diagnostics.record_path(self.run_date).read_text(encoding="utf-8"))
        attempt = json.loads((diagnostics.run_dir(self.run_date) / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["cumulative_eligible_count"], 4)
        self.assertTrue(record["selection_inconsistent"])
        self.assertEqual(record["selection_repair_reason"], "selection_output_invalid")
        self.assertEqual(attempt["stage_candidate_count"], 4)
        self.assertEqual(attempt["cumulative_candidate_ids"], ["P1", "P2", "P3", "P4"])
        self.assertTrue((diagnostics.run_dir(self.run_date) / "attempt-1-cumulative-candidates.json").exists())

    def test_contract_repair_metadata_and_all_output_stages_are_saved(self):
        diagnostics.start_run(self.run_date)
        root = Path(self.temp.name)
        raw = root / "raw.json"
        candidate = root / "candidate.json"
        gate = root / "gate.md"
        original_json = root / "original.json"
        repaired_json = root / "repaired.json"
        original_brief = root / "original-brief.md"
        repaired_brief = root / "contract-repaired-brief.md"
        format_brief = root / "format-repaired-brief.md"
        evidence = root / "evidence-consistency.json"
        raw.write_text('{"final_status":"PUBLISH"}', encoding="utf-8")
        original_json.write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
        repaired_json.write_text('{"final_status":"PUBLISH","repaired":true}', encoding="utf-8")
        original_brief.write_text("## Candidate Diagnostics\n", encoding="utf-8")
        repaired_brief.write_text("Human-readable brief", encoding="utf-8")
        format_brief.write_text("Format-repaired brief", encoding="utf-8")
        evidence.write_text(json.dumps({
            "top3_candidate_ids": ["C1", "C2", "C3"],
            "selected_candidate_ids": ["C1", "C2", "C3"],
            "evidence_consistency_result": "PASS",
            "evidence_consistency_reason": "pass",
            "evidence_consistency_details": [],
        }), encoding="utf-8")
        candidate.write_text('{"status":"RECORDED","coverage":"PARTIAL","candidates":[]}', encoding="utf-8")
        gate.write_text("pass", encoding="utf-8")
        diagnostics.record_attempt(
            self.run_date, 1, 0, "PASS", "pass", False, "RECORDED",
            raw, candidate, gate, "GENERAL", "GENERAL", "general_world_news",
            original_output=repaired_brief, repaired_output=format_brief,
            repair_metadata={"original_gate_result": "FAIL", "original_gate_reason": "required_heading_order_invalid",
                             "format_repair_attempted": True, "format_repair_result": "PASS",
                             "repaired_gate_result": "PASS", "repaired_gate_reason": "pass"},
            contract_metadata={"json_contract_result": "PASS", "json_contract_reason": "diagnostics_removed",
                               "contract_repair_attempted": True, "contract_repair_result": "PASS",
                               "contract_repair_reason": "candidate_diagnostics_embedded_in_brief_markdown"},
            original_json_output=original_json, contract_repaired_json=repaired_json,
            contract_original_brief=original_brief, contract_repaired_brief=repaired_brief,
            evidence_consistency_output=evidence,
        )
        directory = diagnostics.run_dir(self.run_date)
        record = json.loads(diagnostics.record_path(self.run_date).read_text(encoding="utf-8"))
        attempt = json.loads((directory / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(record["json_contract_result"], "PASS")
        self.assertEqual(record["json_contract_reason"], "diagnostics_removed")
        self.assertTrue(record["contract_repair_attempted"])
        self.assertEqual(record["contract_repair_result"], "PASS")
        self.assertEqual(record["contract_repair_reason"], "candidate_diagnostics_embedded_in_brief_markdown")
        for artifact in (
            "attempt-1-original-json-output.json",
            "attempt-1-contract-repaired-json.json",
            "attempt-1-original-brief-markdown.md",
            "attempt-1-contract-repaired-brief-markdown.md",
            "attempt-1-format-repaired-brief-markdown.md",
        ):
            self.assertTrue((directory / artifact).is_file(), artifact)
        self.assertEqual(attempt["original_json_output"], "attempt-1-original-json-output.json")
        self.assertEqual(attempt["contract_repaired_json"], "attempt-1-contract-repaired-json.json")
        self.assertEqual(attempt["contract_original_brief_markdown"], "attempt-1-original-brief-markdown.md")
        self.assertEqual(attempt["contract_repaired_brief_markdown"], "attempt-1-contract-repaired-brief-markdown.md")
        self.assertEqual(attempt["format_repaired_brief_markdown"], "attempt-1-format-repaired-brief-markdown.md")
        saved_evidence = json.loads((directory / "attempt-1-evidence-consistency.json").read_text(encoding="utf-8"))
        self.assertEqual(saved_evidence["evidence_consistency_result"], "PASS")
        self.assertEqual(record["top3_candidate_ids"], ["C1", "C2", "C3"])
        self.assertEqual(record["selected_candidate_ids"], ["C1", "C2", "C3"])
        self.assertEqual(record["evidence_consistency_result"], "PASS")
        self.assertEqual(record["evidence_consistency_reason"], "pass")

    def test_rendered_trusted_source_prompt_has_individual_queries_and_no_template_markers(self):
        template = Path(__file__).resolve().parents[1] / "prompts/daily_review_draft.md"
        rendered = Path(self.temp.name) / "trusted.md"
        diagnostics.render_prompt(template, rendered, "TRUSTED_SOURCES")
        text = rendered.read_text(encoding="utf-8")
        self.assertIn("SEARCH_STAGE=TRUSTED_SOURCES", text)
        self.assertIn("SEARCH_STRATEGY=TRUSTED_SOURCES", text)
        self.assertIn("NEXT_SEARCH_STAGE=CATEGORY_EXPANSION", text)
        self.assertIn("MAX_SEARCH_STAGE=3", text)
        self.assertIn("AP、Reuters、BBCをそれぞれ個別", text)
        self.assertNotIn("__SEARCH_", text)


if __name__ == "__main__":
    unittest.main()
