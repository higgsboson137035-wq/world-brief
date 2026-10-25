from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.record_no_publish import NoPublishFailure, record_no_publish


RUN_DATE = "2026-10-25"
LATEST_DATE = "2026-10-24"


class RecordNoPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-no-publish-")
        temp_root = Path(self.temp.name)
        self.root = temp_root / "repo"
        self.root.mkdir()
        (self.root / "briefs").mkdir()
        (self.root / "briefs/2026-10-24.md").write_text(
            "_Date: 2026-10-24_\nPrevious published brief.\n", encoding="utf-8"
        )
        self.previous_html = "<!doctype html><html><body>Previous public article content</body></html>\n"
        (self.root / "2026-10-24.html").write_text(self.previous_html, encoding="utf-8")
        (self.root / "latest.html").write_text(self.previous_html, encoding="utf-8")
        (self.root / "archive.html").write_text(
            '<!doctype html><html><body><a href="2026-10-24.html">2026-10-24</a></body></html>\n',
            encoding="utf-8",
        )
        self.index = self.root / "index.html"
        self.index.write_text(
            "<!doctype html>\n<html lang=\"ja\"><body><article>\n"
            "<header>\n<h1>World Brief</h1>\n"
            "<p><small>最終更新: 2026-10-24 07:00</small></p>\n"
            "</header>\n\n<hr>\n"
            "<p><em>Date: 2026-10-24</em></p>\n"
            "<main>Previous published article remains here.</main>\n"
            "</article></body></html>\n",
            encoding="utf-8",
        )
        self.dev_file = self.root / "development-notes.txt"
        self.dev_file.write_text("tracked base\n", encoding="utf-8")

        self.env = os.environ.copy()
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test Author",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test Committer",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        })
        self._git("init", "--template=", "--initial-branch=main")
        self._git("config", "user.name", "Test Publisher")
        self._git("config", "user.email", "publisher@example.invalid")
        self._git("add", "--", "briefs/2026-10-24.md", "2026-10-24.html", "latest.html", "index.html", "archive.html", "development-notes.txt")
        self._git("commit", "-m", "fixture base")
        self.base_head = self._git("rev-parse", "HEAD")
        self.remote = temp_root / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "--template=", str(self.remote)],
            check=True, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._git("remote", "add", "origin", str(self.remote))
        self._git("push", "-u", "origin", "main")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()

    def _files_in_head_commit(self) -> list[str]:
        return self._git("show", "--format=", "--name-only", "HEAD").splitlines()

    def test_no_publish_updates_index_only_and_preserves_previous_latest_and_archive(self):
        protected = {
            path: (self.root / path).read_bytes()
            for path in ("latest.html", "archive.html", "2026-10-24.html", "briefs/2026-10-24.md")
        }
        sha = record_no_publish(self.root, RUN_DATE, enforce_today=False)
        self.assertIsNotNone(sha)

        index = self.index.read_text(encoding="utf-8")
        self.assertIn("状態更新: 2026-10-25", index)
        self.assertIn("2026年10月25日 — 本日は品質基準により公開なし", index)
        self.assertIn("所定の確認基準を満たす記事を十分に確保できなかったため", index)
        self.assertIn('<a href="2026-10-24.html">2026年10月24日版 World Brief</a>', index)
        self.assertIn("<h2>最新の公開記事 — 2026年10月24日</h2>", index)
        self.assertLess(index.index("最新の公開記事 — 2026年10月24日"), index.index("Date: 2026-10-24"))
        self.assertNotIn("最終更新: 2026-10-24", index)
        self.assertIn("Date: 2026-10-24", index)
        self.assertIn("Previous published article remains here.", index)
        self.assertNotIn("INSUFFICIENT_VERIFIED_ARTICLES", index)
        self.assertNotIn("2026-10-25.html", index)
        for path, content in protected.items():
            self.assertEqual(content, (self.root / path).read_bytes())
        self.assertFalse((self.root / "briefs/2026-10-25.md").exists())
        self.assertFalse((self.root / "2026-10-25.html").exists())
        self.assertEqual(self._files_in_head_commit(), ["index.html"])
        self.assertEqual(
            self._git("log", "-1", "--format=%s"),
            "Record World Brief no-publish status for 2026-10-25",
        )
        self.assertEqual(self._git("rev-parse", "origin/main"), sha)
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self._git("log", "origin/main..HEAD", "--oneline"), "")
        self.assertEqual(self._git("log", "HEAD..origin/main", "--oneline"), "")

        previous_head = self._git("rev-parse", "HEAD")
        self.assertIsNone(record_no_publish(self.root, RUN_DATE, enforce_today=False))
        self.assertEqual(self._git("rev-parse", "HEAD"), previous_head)

    def test_dirty_unrelated_worktree_is_preserved_and_not_committed(self):
        self.dev_file.write_text("uncommitted development change\n", encoding="utf-8")
        untracked = self.root / "untracked-development.txt"
        untracked.write_text("keep this file\n", encoding="utf-8")

        record_no_publish(self.root, RUN_DATE, enforce_today=False)

        self.assertEqual(self._files_in_head_commit(), ["index.html"])
        self.assertEqual(self.dev_file.read_text(encoding="utf-8"), "uncommitted development change\n")
        self.assertEqual(untracked.read_text(encoding="utf-8"), "keep this file\n")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")
        self.assertIn("development-notes.txt", self._git("status", "--short"))
        self.assertIn("untracked-development.txt", self._git("status", "--short"))

    def test_existing_staged_changes_stop_before_index_update(self):
        self.dev_file.write_text("staged development change\n", encoding="utf-8")
        self._git("add", "--", "development-notes.txt")
        before = self.index.read_bytes()

        with self.assertRaises(NoPublishFailure) as raised:
            record_no_publish(self.root, RUN_DATE, enforce_today=False)

        self.assertEqual(raised.exception.code, "preexisting_staged_changes")
        self.assertEqual(self.index.read_bytes(), before)
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_head)
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "development-notes.txt")

    def test_existing_same_day_public_artifact_stops_without_status(self):
        target = self.root / "2026-10-25.html"
        target.write_text("existing dated public page", encoding="utf-8")

        with self.assertRaises(NoPublishFailure) as raised:
            record_no_publish(self.root, RUN_DATE, enforce_today=False)

        self.assertEqual(raised.exception.code, "public_date_artifact_exists")
        self.assertNotIn("WORLD_BRIEF_NO_PUBLISH_STATUS", self.index.read_text(encoding="utf-8"))
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_head)

    def test_origin_divergence_stops_before_index_update(self):
        base_tree = self._git("rev-parse", f"{self.base_head}^{{tree}}")
        remote_only = self._git("commit-tree", base_tree, "-m", "remote-only divergence")
        self._git("update-ref", "refs/remotes/origin/main", remote_only)
        before = self.index.read_bytes()

        with self.assertRaises(NoPublishFailure) as raised:
            record_no_publish(self.root, RUN_DATE, enforce_today=False)

        self.assertEqual(raised.exception.code, "local_remote_diverged")
        self.assertEqual(self.index.read_bytes(), before)
        self.assertEqual(self._git("rev-parse", "HEAD"), self.base_head)

    def test_push_failure_is_reported_and_not_retried(self):
        hook = self.remote / "hooks/pre-receive"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        remote_before = self._git("rev-parse", "origin/main")

        with self.assertRaises(NoPublishFailure) as raised:
            record_no_publish(self.root, RUN_DATE, enforce_today=False)

        self.assertEqual(raised.exception.code, "status_push_failed")
        self.assertEqual(self._git("rev-parse", "origin/main"), remote_before)
        self.assertEqual(self._files_in_head_commit(), ["index.html"])
        self.assertEqual(self._git("log", "origin/main..HEAD", "--oneline").count("\n"), 0)


if __name__ == "__main__":
    unittest.main()
