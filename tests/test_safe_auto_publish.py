from pathlib import Path
from datetime import date
import json
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/safe_auto_publish.sh"


class SafeAutoPublishWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-auto-wrapper-")
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        (self.root / ".venv/bin").mkdir(parents=True)
        self.generated = self.root / "generated"
        self.published = self.root / "published"
        self.recorded = self.root / "recorded"
        self.run_metadata = self.root / "run-metadata"
        wrapper_target = scripts / WRAPPER.name
        wrapper_target.write_bytes(WRAPPER.read_bytes())
        wrapper_target.chmod(0o700)
        (scripts / "run_diagnostics.py").write_bytes((ROOT / "scripts/run_diagnostics.py").read_bytes())
        self._write_executable(
            scripts / "generate_review_draft.sh",
            "#!/bin/bash\n"
            f"printf 'started' >> '{self.generated}'\n"
            f"printf '%s %s %s' \"$WORLD_BRIEF_RUN_ID\" \"$WORLD_BRIEF_TRIGGER\" \"$WORLD_BRIEF_PREVIOUS_RUN_STATUS\" > '{self.run_metadata}'\n"
            "if [ -n \"${WORLD_BRIEF_DRAFT_DIR:-}\" ]; then exit 41; fi\n"
            "if [ \"${WORLD_BRIEF_TEST_GENERATION_NO_PUBLISH:-0}\" = 1 ]; then printf 'RETRIEVAL_EXHAUSTED\\n'; exit 11; fi\n"
            "if [ \"${WORLD_BRIEF_TEST_NETWORK_FAIL:-0}\" = 1 ]; then exit 12; fi\n"
            "if [ \"${WORLD_BRIEF_TEST_GENERATION_FAIL:-0}\" = 1 ]; then exit 42; fi\n",
        )
        self.python_stub = self.root / ".venv/bin/python"
        self._write_executable(
            self.python_stub,
            "#!/bin/sh\n"
            f"if [ \"$2\" = \"{scripts / 'run_diagnostics.py'}\" ]; then exec \"$WORLD_BRIEF_TEST_REAL_PYTHON\" \"$@\"; fi\n"
            f"if [ \"$2\" = \"{scripts / 'record_no_publish.py'}\" ]; then printf 'called' > '{self.recorded}'; printf 'NO_PUBLISH_STATUS=PASS commit=0123456789abcdef\\n'; exit \"${{WORLD_BRIEF_TEST_RECORD_EXIT:-0}}\"; fi\n"
            f"if [ \"$2\" = \"{scripts / 'publish_auto_draft.py'}\" ]; then printf 'called' > '{self.published}'; printf 'AUTO-PUBLISH=PASS commit=0123456789abcdef\\n'; fi\n"
            "exit 0\n",
        )
        self.env = os.environ.copy()
        self.env["WORLD_BRIEF_PYTHON_BIN"] = str(self.python_stub)
        self.env["WORLD_BRIEF_TEST_REAL_PYTHON"] = os.sys.executable
        self.env["WORLD_BRIEF_DRAFT_DIR"] = "drafts/test-must-not-leak"

    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, path, contents):
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o700)

    def _run(self, generation_fail=False, no_publish=False, record_fail=False, network_fail=False, allow_rerun=False):
        env = self.env.copy()
        env["WORLD_BRIEF_TEST_GENERATION_FAIL"] = "1" if generation_fail else "0"
        env["WORLD_BRIEF_TEST_GENERATION_NO_PUBLISH"] = "1" if no_publish else "0"
        env["WORLD_BRIEF_TEST_RECORD_EXIT"] = "8" if record_fail else "0"
        env["WORLD_BRIEF_TEST_NETWORK_FAIL"] = "1" if network_fail else "0"
        if allow_rerun:
            env["WORLD_BRIEF_ALLOW_RERUN"] = "1"
        else:
            env.pop("WORLD_BRIEF_ALLOW_RERUN", None)
        return subprocess.run(
            ["/bin/bash", str(self.root / "scripts/safe_auto_publish.sh")],
            cwd=self.root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10,
        )

    def test_wrapper_runs_generation_before_publish_and_clears_test_directory_override(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.generated.read_text(encoding="utf-8"), "started")
        self.assertEqual(self.published.read_text(encoding="utf-8"), "called")
        self.assertEqual(self.run_metadata.read_text(encoding="utf-8"), "run-01 scheduled NONE")
        self.assertEqual(self._diagnostic()["trigger"], "scheduled")

    def test_generation_failure_stops_before_publish(self):
        result = self._run(generation_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.generated.read_text(encoding="utf-8"), "started")
        self.assertFalse(self.published.exists())
        self.assertFalse(self.recorded.exists())

    def test_same_day_run_without_human_flag_is_rejected_before_generation(self):
        self._seed_run("FAILED")
        original = self._diagnostic_path().read_bytes()
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("diagnostic_run_already_exists", result.stderr)
        self.assertFalse(self.generated.exists())
        self.assertEqual(self._diagnostic_path().read_bytes(), original)

    def test_human_rerun_uses_run02_and_preserves_run01(self):
        self._seed_run("FAILED")
        first_path = self._diagnostic_path()
        original = first_path.read_bytes()
        result = self._run(allow_rerun=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self._diagnostic("run-02")
        self.assertEqual(record["run_id"], "run-02")
        self.assertEqual(record["trigger"], "human_rerun")
        self.assertEqual(record["previous_run_status"], "FAILED")
        self.assertEqual(record["final_status"], "PUBLISH")
        self.assertEqual(self.run_metadata.read_text(encoding="utf-8"), "run-02 human_rerun FAILED")
        self.assertEqual(first_path.read_bytes(), original)

    def test_failed_run02_does_not_allow_run03(self):
        self._seed_run("RETRIEVAL_EXHAUSTED")
        first_path = self._diagnostic_path()
        original = first_path.read_bytes()
        first_result = self._run(generation_fail=True, allow_rerun=True)
        self.assertNotEqual(first_result.returncode, 0)
        run02 = self._diagnostic("run-02")
        self.assertEqual(run02["final_status"], "FAILED")
        generated_count = len(self.generated.read_text(encoding="utf-8"))
        second_result = self._run(allow_rerun=True)
        self.assertNotEqual(second_result.returncode, 0)
        self.assertIn("diagnostic_run_already_exists", second_result.stderr)
        self.assertEqual(len(self.generated.read_text(encoding="utf-8")), generated_count)
        self.assertEqual(first_path.read_bytes(), original)

    def test_published_run_and_existing_public_file_reject_human_rerun(self):
        self._seed_run("PUBLISH")
        result = self._run(allow_rerun=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rerun_previous_status_not_eligible", result.stderr)
        self.assertFalse(self.generated.exists())

        self.root.joinpath("logs/runs", date.today().isoformat(), "run-02").mkdir()
        # A separate failed run with an existing public file is also denied.
        other = self.root / "other-fixture"
        (other / "scripts").mkdir(parents=True)
        for name in ("safe_auto_publish.sh", "run_diagnostics.py"):
            source = ROOT / "scripts" / name
            (other / "scripts" / name).write_bytes(source.read_bytes())
        # The direct diagnostics decision covers the public-file guard without invoking generation.
        from scripts import run_diagnostics as diagnostics
        saved_runs, saved_root = diagnostics.RUNS, diagnostics.ROOT
        try:
            diagnostics.ROOT = other
            diagnostics.RUNS = other / "logs" / "runs"
            diagnostics.start_run(date.today().isoformat())
            diagnostics.finalize(date.today().isoformat(), __import__("argparse").Namespace(
                run_id="run-01", final_status="FAILED", gate_result="FAIL", gate_reason="failed",
                codex_exit_code=None, draft_saved=False, publish_started=False,
                commit_created=False, push_attempted=False, push_result="NOT_ATTEMPTED",
                commit_type=None, sentinel_seen=False, human_review_required=True,
            ))
            public_md = other / "briefs" / f"{date.today().isoformat()}.md"
            public_md.parent.mkdir(parents=True, exist_ok=True)
            public_md.write_text("published", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "published_artifact_exists"):
                diagnostics.select_run(date.today().isoformat(), "1", public_md,
                                       other / f"{date.today().isoformat()}.html")
        finally:
            diagnostics.RUNS, diagnostics.ROOT = saved_runs, saved_root

    def test_network_failure_is_failed_without_recovery_or_hp_update(self):
        result = self._run(network_fail=True)
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("network_unavailable", result.stderr)
        self.assertFalse(self.published.exists())
        self.assertFalse(self.recorded.exists())
        record = self._diagnostic()
        self.assertEqual(record["final_status"], "FAILED")
        self.assertEqual(record["final_run_reason"], "network_unavailable")

    def test_retrieval_exhausted_is_human_review_and_stops_without_publication(self):
        result = self._run(no_publish=True)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("AUTO-PUBLISH=RETRIEVAL_EXHAUSTED", result.stderr)
        self.assertFalse(self.recorded.exists())
        self.assertFalse(self.published.exists())
        record = self._diagnostic()
        self.assertEqual(record["final_status"], "RETRIEVAL_EXHAUSTED")
        self.assertTrue(record["human_review_required"])
        self.assertFalse(record["draft_saved"])
        self.assertFalse(record["publish_started"])
        self.assertFalse(record["commit_created"])
        self.assertFalse(record["push_attempted"])

    def test_legacy_no_publish_exit_fails_closed_without_status_update(self):
        result = self._run(no_publish=True, record_fail=True)
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertNotIn("AUTO-PUBLISH=NO_PUBLISH", result.stdout + result.stderr)
        self.assertFalse(self.recorded.exists())
        self.assertFalse(self.published.exists())
        self.assertEqual(self._diagnostic()["final_status"], "RETRIEVAL_EXHAUSTED")

    def test_publish_run_records_publish_lifecycle(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self._diagnostic()
        self.assertEqual(record["final_status"], "PUBLISH")
        self.assertTrue(record["draft_saved"])
        self.assertTrue(record["publish_started"])
        self.assertTrue(record["commit_created"])
        self.assertTrue(record["push_attempted"])
        self.assertEqual(record["push_result"], "PASS")

    def _diagnostic_path(self, run_id="run-01"):
        path = self.root / "logs/runs" / date.today().isoformat()
        if run_id != "run-01":
            path /= run_id
        return path / "diagnostic.json"

    def _diagnostic(self, run_id="run-01"):
        return json.loads(self._diagnostic_path(run_id).read_text(encoding="utf-8"))

    def _seed_run(self, status):
        script = self.root / "scripts/run_diagnostics.py"
        run_date = date.today().isoformat()
        start = subprocess.run([os.sys.executable, "-B", str(script), "start", run_date],
                               cwd=self.root, text=True, capture_output=True, check=False)
        self.assertEqual(start.returncode, 0, start.stderr)
        finish = subprocess.run(
            [os.sys.executable, "-B", str(script), "finish", run_date, status,
             "UNKNOWN", "seeded_status", "--run-id", "run-01"],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertEqual(finish.returncode, 0, finish.stderr)


if __name__ == "__main__":
    unittest.main()
