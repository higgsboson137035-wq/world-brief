from pathlib import Path
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
        self._write_executable(
            scripts / "generate_review_draft.sh",
            "#!/bin/bash\n"
            f"printf 'started' > '{self.generated}'\n"
            "if [ -n \"${WORLD_BRIEF_DRAFT_DIR:-}\" ]; then exit 41; fi\n"
            "if [ \"${WORLD_BRIEF_TEST_GENERATION_NO_PUBLISH:-0}\" = 1 ]; then printf 'NO_PUBLISH reason=insufficient_verified_articles\\n'; exit 10; fi\n"
            "if [ \"${WORLD_BRIEF_TEST_GENERATION_FAIL:-0}\" = 1 ]; then exit 42; fi\n",
        )
        self.python_stub = self.root / ".venv/bin/python"
        self._write_executable(
            self.python_stub,
            "#!/bin/sh\n"
            f"if [ \"$2\" = \"{scripts / 'record_no_publish.py'}\" ]; then printf 'called' > '{self.recorded}'; exit \"${{WORLD_BRIEF_TEST_RECORD_EXIT:-0}}\"; fi\n"
            f"if [ \"$2\" = \"{scripts / 'publish_auto_draft.py'}\" ]; then printf 'called' > '{self.published}'; fi\n"
            "exit 0\n",
        )
        self.env = os.environ.copy()
        self.env["WORLD_BRIEF_PYTHON_BIN"] = str(self.python_stub)
        self.env["WORLD_BRIEF_DRAFT_DIR"] = "drafts/test-must-not-leak"

    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, path, contents):
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o700)

    def _run(self, generation_fail=False, no_publish=False, record_fail=False):
        env = self.env.copy()
        env["WORLD_BRIEF_TEST_GENERATION_FAIL"] = "1" if generation_fail else "0"
        env["WORLD_BRIEF_TEST_GENERATION_NO_PUBLISH"] = "1" if no_publish else "0"
        env["WORLD_BRIEF_TEST_RECORD_EXIT"] = "8" if record_fail else "0"
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

    def test_no_publish_is_logged_as_expected_outcome_and_stops_before_publish(self):
        result = self._run(no_publish=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NO_PUBLISH reason=insufficient_verified_articles", result.stdout)
        self.assertIn("AUTO-PUBLISH=NO_PUBLISH reason=insufficient_verified_articles", result.stdout)
        self.assertEqual(self.recorded.read_text(encoding="utf-8"), "called")
        self.assertFalse(self.published.exists())

    def test_failed_status_recording_is_not_reported_as_no_publish_success(self):
        result = self._run(no_publish=True, record_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AUTO-PUBLISH=FAILED reason=no_publish_status_update_failed", result.stderr)
        self.assertNotIn("AUTO-PUBLISH=NO_PUBLISH", result.stdout)
        self.assertFalse(self.published.exists())


if __name__ == "__main__":
    unittest.main()
