from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts import resume_saved_run
from scripts.resume_saved_run import ResumeFailure, prepare_resume


DATE = "2026-10-29"
RUN = "run-02"
SOURCE_RUN = Path(__file__).resolve().parents[1] / "logs/runs/2026-10-29/run-02"


class ResumeSavedRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-resume-")
        self.root = Path(self.temp.name) / "repo"
        (self.root / "drafts").mkdir(parents=True)
        (self.root / "briefs").mkdir()
        run_dir = self.root / "logs/runs" / DATE / RUN
        shutil.copytree(SOURCE_RUN, run_dir)
        self.run_dir = run_dir
        self.originals = {
            p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()
        }
        self.output_dir = self.root / "drafts/preflight-2026-10-29"

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        path = self.run_dir / "attempt-1-original-json-output.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def save_payload(self, payload):
        (self.run_dir / "attempt-1-original-json-output.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_saved_run02_replays_all_gates_and_builds_normal_review_drafts(self):
        result = prepare_resume(
            self.root, DATE, RUN, output_dir=self.output_dir,
            resumed_at="2026-10-29T05:00:00Z",
        )
        self.assertEqual(result["resume_json_contract"], "PASS")
        self.assertEqual(result["resume_evidence_consistency"], "PASS")
        self.assertEqual(result["resume_quality_gate"], "pass")
        self.assertEqual(result["final_status"], "RESUME_DRAFT_READY")
        md = Path(result["draft_markdown"]).read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# World Brief — Human確認前・未公開\n\n"))
        self.assertIn(self.payload()["brief_markdown"], md)
        self.assertTrue(Path(result["draft_html"]).is_file())
        record = json.loads((self.run_dir / "resume-publication.json").read_text(encoding="utf-8"))
        self.assertEqual(record["resume_from_run"], RUN)
        self.assertEqual(record["resume_source_status"], "FAILED")
        self.assertEqual(record["resumed_at"], "2026-10-29T05:00:00Z")
        self.assertFalse(record["publish_started"])
        self.assertFalse(record["commit_created"])
        self.assertFalse(record["push_attempted"])

    def test_json_contract_failure_refuses_without_drafts(self):
        payload = self.payload()
        payload["final_status"] = "NO_PUBLISH"
        self.save_payload(payload)
        with self.assertRaisesRegex(ResumeFailure, "json_contract_failed"):
            prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        self.assertFalse(self.output_dir.exists())

    def test_evidence_consistency_failure_refuses_without_drafts(self):
        payload = self.payload()
        payload["top3"][1]["source_url"] += "?wrong=1"
        self.save_payload(payload)
        with self.assertRaisesRegex(ResumeFailure, "evidence_consistency_failed"):
            prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        self.assertFalse(self.output_dir.exists())

    def test_quality_failure_refuses_without_drafts(self):
        payload = self.payload()
        payload["brief_markdown"] = payload["brief_markdown"].replace(
            "## 🇯🇵 日本への影響", "## 🇯🇵 日本への影響\n\n", 1
        )
        # Remove the section body while preserving structurally valid JSON and evidence.
        start = payload["brief_markdown"].index("## 🇯🇵 日本への影響")
        payload["brief_markdown"] = payload["brief_markdown"][:start] + "## 🇯🇵 日本への影響\n\n"
        self.save_payload(payload)
        with self.assertRaisesRegex(ResumeFailure, "quality_gate_failed"):
            prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        self.assertFalse(self.output_dir.exists())

    def test_already_published_date_refuses(self):
        (self.root / "briefs" / f"{DATE}.md").write_text("published", encoding="utf-8")
        with self.assertRaisesRegex(ResumeFailure, "public_date_artifact_exists"):
            prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)

    def test_nonexistent_run_refuses(self):
        with self.assertRaisesRegex(ResumeFailure, "saved_run_not_found"):
            prepare_resume(self.root, DATE, "run-03", output_dir=self.output_dir)

    def test_saved_run02_fixture_can_be_prepared_without_production_draft(self):
        result = prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        self.assertTrue(Path(result["draft_markdown"]).is_file())
        self.assertFalse((self.root / "drafts" / f"{DATE}-human-review.md").exists())
        self.assertFalse((self.root / "drafts" / f"{DATE}-human-review.html").exists())

    def test_resume_does_not_invoke_search_or_codex_and_only_publication_flag_is_false(self):
        with patch("scripts.resume_saved_run.validate_envelope", wraps=resume_saved_run.validate_envelope) as contract_gate:
            result = prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        contract_gate.assert_called_once()
        self.assertEqual(result["final_status"], "RESUME_DRAFT_READY")
        self.assertFalse(result["publish_started"])

    def test_source_run_artifacts_are_not_modified(self):
        prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)
        for name, content in self.originals.items():
            self.assertEqual((self.run_dir / name).read_bytes(), content, name)
        # Provenance is additive and separate from every original diagnostic artifact.
        self.assertTrue((self.run_dir / "resume-publication.json").is_file())

    def test_completed_run_is_not_resumable(self):
        path = self.run_dir / "diagnostic.json"
        diagnostic = json.loads(path.read_text(encoding="utf-8"))
        diagnostic["final_status"] = "PUBLISHED"
        path.write_text(json.dumps(diagnostic), encoding="utf-8")
        with self.assertRaisesRegex(ResumeFailure, "saved_run_status_not_resumable"):
            prepare_resume(self.root, DATE, RUN, output_dir=self.output_dir)


if __name__ == "__main__":
    unittest.main()
