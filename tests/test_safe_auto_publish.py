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
        wrapper_target = scripts / WRAPPER.name
        wrapper_target.write_bytes(WRAPPER.read_bytes())
        wrapper_target.chmod(0o700)
        (scripts / "run_diagnostics.py").write_bytes((ROOT / "scripts/run_diagnostics.py").read_bytes())
        self._write_executable(
            scripts / "generate_review_draft.sh",
            "#!/bin/bash\n"
            f"printf 'started' > '{self.generated}'\n"
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

    def _run(self, generation_fail=False, no_publish=False, record_fail=False, network_fail=False):
        env = self.env.copy()
        env["WORLD_BRIEF_TEST_GENERATION_FAIL"] = "1" if generation_fail else "0"
        env["WORLD_BRIEF_TEST_GENERATION_NO_PUBLISH"] = "1" if no_publish else "0"
        env["WORLD_BRIEF_TEST_RECORD_EXIT"] = "8" if record_fail else "0"
        env["WORLD_BRIEF_TEST_NETWORK_FAIL"] = "1" if network_fail else "0"
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

    def test_generation_failure_stops_before_publish(self):
        result = self._run(generation_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.generated.read_text(encoding="utf-8"), "started")
        self.assertFalse(self.published.exists())
        self.assertFalse(self.recorded.exists())

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

    def _diagnostic(self):
        path = self.root / "logs/runs" / date.today().isoformat() / "diagnostic.json"
        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
