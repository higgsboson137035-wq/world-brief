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
        diagnostics.RUNS = Path(self.temp.name) / "runs"
        self.run_date = "2026-10-26"

    def tearDown(self):
        diagnostics.RUNS = self.original_runs
        self.temp.cleanup()

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
