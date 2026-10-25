from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import scripts.publish_auto_draft as publisher
from scripts.publish_auto_draft import PublishFailure, publish
from scripts.review_draft_gate import classify_text, validate_text
from scripts.record_no_publish import (
    ARTICLE_HEADING_END,
    ARTICLE_HEADING_START,
    STATUS_END,
    STATUS_META_END,
    STATUS_META_START,
    STATUS_START,
    apply_no_publish_display,
)


RUN_DATE = "2026-10-06"
URLS = (
    "https://www.reuters.com/world/a",
    "https://apnews.com/article/b",
    "https://www.bbc.com/news/articles/c",
)


def fixture_markdown(*, top3_count=3, duplicate=False, japan="good"):
    top = [f"{n}. Event {n}." for n in range(1, top3_count + 1)]
    urls = list(URLS)
    if duplicate:
        urls[2] = urls[1]
    if japan == "empty":
        impact = []
    elif japan == "shallow":
        impact = ["影響を確認したい。"]
    else:
        impact = [
            "供給経路が変化すれば、日本企業の調達や物流に影響し得る。短期には実際の供給量を確認する。",
            "中期には政策の具体化を判断材料として見る。",
        ]
    articles = []
    for number, url in enumerate(urls, 1):
        articles.extend([
            f"Article summary {number}.",
            f"英語記事タイトル：Article {number}",
            f"情報源：Source {number}",
            "",
            f"🔗 [原文を読む]({url})",
            "",
        ])
    sections = [
        f"_Date: {RUN_DATE}_",
        "",
        "## Today's Top 3",
        *top,
        "",
        "## Executive Summary",
        "Summary.",
        "",
        "## Why It Matters",
        "Why.",
        "",
        "## Big Picture",
        "Picture.",
        "",
        "## Tomorrow's Watchlist",
        "- Watch one.",
        "- Watch two.",
        "- Watch three.",
        "",
        "## 🌍 世界情勢",
        *articles[:18],
        "## 💹 経済",
        "Economy.",
        "## 🤖 AI・テクノロジー",
        "今回は掲載候補なし",
        "## 🔬 科学",
        "今回は掲載候補なし",
        "## 🇯🇵 日本への影響",
        *impact,
        "",
    ]
    return "\n".join(sections)


def review_html(markdown_text: str) -> str:
    return (
        f'<html><head><title>World Brief — {RUN_DATE} — Human Review</title>'
        '<link rel="stylesheet" href="../style.css"></head><body>'
        '<aside>Human確認前・未公開。記事の事実・出典・日時・リンクはHuman未確認です。</aside>'
        + "".join(f'<a href="{url}">原文を読む</a>' for url in URLS)
        + "</body></html>"
    )


class AutoPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-auto-publish-")
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "drafts").mkdir()
        (self.root / "briefs").mkdir()
        source_root = Path(__file__).resolve().parents[1]
        shutil.copy2(source_root / "scripts/review_draft_gate.py", self.root / "scripts/review_draft_gate.py")
        (self.root / "style.css").write_text("article { color: black; }\n", encoding="utf-8")
        (self.root / "briefs/2026-10-04.md").write_text("_Date: 2026-10-04_\n", encoding="utf-8")
        prev_page = "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>prior</body></html>\n"
        (self.root / "2026-10-04.html").write_text(prev_page, encoding="utf-8")
        (self.root / "latest.html").write_text(prev_page, encoding="utf-8")
        previous_index = (
            "<!doctype html><html lang=\"ja\"><body><article>\n"
            "<header><h1>World Brief</h1>\n"
            "<p><small>最終更新: 2026-10-04 07:00</small></p></header>\n\n"
            "<hr>\n<p><em>Date: 2026-10-04</em></p>\n"
            "<main>yesterday's status and article</main>\n"
            "</article></body></html>\n"
        )
        previous_index, _ = apply_no_publish_display(
            previous_index, "2026-10-05", "2026-10-04"
        )
        (self.root / "index.html").write_text(previous_index, encoding="utf-8")
        (self.root / "archive.html").write_text(prev_page, encoding="utf-8")
        (self.root / "prompts").mkdir()
        (self.root / "prompts/daily_review_draft.md").write_text("unchanged\n", encoding="utf-8")
        (self.root / "drafts/2026-10-06-human-review.md").write_text(
            "# World Brief — Human確認前・未公開\n\n"
            "> 記事の事実・出典・日時・リンクはHuman未確認です。\n\n"
            + fixture_markdown(), encoding="utf-8"
        )
        (self.root / "drafts/2026-10-06-human-review.html").write_text(
            review_html(fixture_markdown()), encoding="utf-8"
        )

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
        self._git("add", "--", "style.css", "briefs/2026-10-04.md", "2026-10-04.html", "latest.html", "index.html", "archive.html", "prompts/daily_review_draft.md")
        self._git("commit", "-m", "fixture base")
        self.base_head = self._git("rev-parse", "HEAD")
        self.remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", "--template=", str(self.remote)], check=True, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._git("remote", "add", "origin", str(self.remote))
        self._git("push", "-u", "origin", "main")

    def tearDown(self):
        self.temp.cleanup()

    def _git(self, *args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd or self.root, env=self.env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()

    def _files_in_head_commit(self):
        output = self._git("show", "--format=", "--name-only", "HEAD")
        return {line for line in output.splitlines() if line}

    def _snapshot(self, paths):
        return {path: (self.root / path).read_bytes() for path in paths}

    def test_normal_publish_commits_and_pushes_exact_five_paths_with_dirty_worktree(self):
        dirty_tracked = self.root / "prompts/daily_review_draft.md"
        dirty_tracked.write_text("preexisting dirty development change\n", encoding="utf-8")
        unrelated = self.root / "untracked-development.txt"
        unrelated.write_text("keep\n", encoding="utf-8")
        before_drafts = self._snapshot(("drafts/2026-10-06-human-review.md", "drafts/2026-10-06-human-review.html"))

        sha = publish(self.root, RUN_DATE, enforce_today=False)
        expected = {
            "briefs/2026-10-06.md", "2026-10-06.html", "latest.html", "index.html", "archive.html"
        }
        self.assertEqual(self._files_in_head_commit(), expected)
        self.assertEqual(self._git("rev-parse", "origin/main"), sha)
        self.assertEqual(self._git("log", "origin/main..HEAD", "--oneline"), "")
        self.assertEqual(self._git("log", "HEAD..origin/main", "--oneline"), "")
        self.assertEqual(dirty_tracked.read_text(encoding="utf-8"), "preexisting dirty development change\n")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(self._git("diff", "--cached", "--name-only"), "")
        current_index = (self.root / "index.html").read_text(encoding="utf-8")
        self.assertNotIn(STATUS_START, current_index)
        self.assertNotIn(STATUS_END, current_index)
        self.assertNotIn(STATUS_META_START, current_index)
        self.assertNotIn(STATUS_META_END, current_index)
        self.assertNotIn(ARTICLE_HEADING_START, current_index)
        self.assertNotIn(ARTICLE_HEADING_END, current_index)
        self.assertNotIn("本日は品質基準により公開なし", current_index)
        self.assertNotIn("最新の公開記事 —", current_index)
        self.assertIn("最終更新:", current_index)
        self.assertIn("Date: 2026-10-06", current_index)
        public_md = (self.root / "briefs/2026-10-06.md").read_text(encoding="utf-8")
        public_html = (self.root / "2026-10-06.html").read_text(encoding="utf-8")
        self.assertNotIn("Human確認前・未公開", public_md + public_html)
        self.assertNotIn("Human未確認", public_md + public_html)
        self.assertEqual(public_md, fixture_markdown())
        self.assertEqual(before_drafts, self._snapshot(("drafts/2026-10-06-human-review.md", "drafts/2026-10-06-human-review.html")))
        self.assertIn("2026-10-06.html", (self.root / "archive.html").read_text(encoding="utf-8"))
        self.assertEqual(public_html, (self.root / "latest.html").read_text(encoding="utf-8"))

    def test_quality_failures_never_commit_or_change_public_targets(self):
        cases = (
            (fixture_markdown(top3_count=2), "top3_invalid"),
            (fixture_markdown(duplicate=True), "fewer_than_three_distinct_urls"),
            (fixture_markdown(japan="empty"), "japan_impact_empty"),
            (fixture_markdown(japan="shallow"), "japan_impact_too_shallow"),
        )
        for text, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(validate_text(text, RUN_DATE), reason)
                self._replace_draft(text)
                before = self._snapshot(("latest.html", "index.html", "archive.html", "2026-10-04.html"))
                head = self._git("rev-parse", "HEAD")
                with self.assertRaises(PublishFailure):
                    publish(self.root, RUN_DATE, enforce_today=False)
                self.assertEqual(self._git("rev-parse", "HEAD"), head)
                self.assertEqual(before, self._snapshot(("latest.html", "index.html", "archive.html", "2026-10-04.html")))

    def test_response_state_classification_is_strict(self):
        self.assertEqual(classify_text(fixture_markdown(), RUN_DATE), ("VALID_DRAFT", "pass"))
        self.assertEqual(
            classify_text("INSUFFICIENT_VERIFIED_ARTICLES", RUN_DATE),
            ("INSUFFICIENT_VERIFIED_ARTICLES", "no_publish_insufficient_verified_articles"),
        )
        self.assertEqual(
            classify_text("INSUFFICIENT_VERIFIED_ARTICLES\n", RUN_DATE),
            ("INSUFFICIENT_VERIFIED_ARTICLES", "no_publish_insufficient_verified_articles"),
        )
        self.assertEqual(
            classify_text("INSUFFICIENT_VERIFIED_ARTICLES\nextra", RUN_DATE),
            ("INVALID_OUTPUT", "invalid_output"),
        )
        self.assertEqual(
            classify_text("INSUFFICIENT_VERIFIED_ARTICLE", RUN_DATE),
            ("INVALID_OUTPUT", "invalid_output"),
        )
        malformed_date = fixture_markdown().replace(
            f"_Date: {RUN_DATE}_", "_Date: 2026-10-05_", 1
        )
        self.assertEqual(
            classify_text(malformed_date, RUN_DATE),
            ("INVALID_OUTPUT", "date_header_invalid"),
        )

    def _replace_draft(self, text):
        (self.root / "drafts/2026-10-06-human-review.md").write_text(
            "# World Brief — Human確認前・未公開\n\n"
            "> 記事の事実・出典・日時・リンクはHuman未確認です。\n\n"
            + text, encoding="utf-8"
        )

    def test_date_target_exists_stops_without_overwrite(self):
        target = self.root / "2026-10-06.html"
        target.write_text("existing protected page", encoding="utf-8")
        with self.assertRaises(PublishFailure) as raised:
            publish(self.root, RUN_DATE, enforce_today=False)
        self.assertEqual(raised.exception.code, "public_date_artifact_exists")
        self.assertEqual(target.read_text(encoding="utf-8"), "existing protected page")
        self.assertFalse((self.root / "briefs/2026-10-06.md").exists())

    def test_preexisting_stage_or_remote_divergence_stops(self):
        (self.root / "prompts/daily_review_draft.md").write_text("stage sentinel\n", encoding="utf-8")
        self._git("add", "--", "prompts/daily_review_draft.md")
        with self.assertRaises(PublishFailure) as staged:
            publish(self.root, RUN_DATE, enforce_today=False)
        self.assertEqual(staged.exception.code, "preexisting_staged_changes")
        self._git("reset", "--", "prompts/daily_review_draft.md")

        base = self._git("rev-parse", "HEAD")
        foreign = self._git("commit-tree", f"{base}^{{tree}}", "-m", "remote-only divergence")
        self._git("update-ref", "refs/remotes/origin/main", foreign)
        with self.assertRaises(PublishFailure) as diverged:
            publish(self.root, RUN_DATE, enforce_today=False)
        self.assertEqual(diverged.exception.code, "local_remote_diverged")
        self.assertEqual(self._git("rev-parse", "HEAD"), base)

    def test_sixth_cached_path_injection_stops_before_commit(self):
        base = self._git("rev-parse", "HEAD")
        target_before = self._snapshot(("latest.html", "index.html", "archive.html", "2026-10-04.html"))
        original_add = publisher.add_candidates_binary

        def inject_sixth_path(root, index, candidates):
            original_add(root, index, candidates)
            env = dict(self.env)
            env["GIT_INDEX_FILE"] = str(index)
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=root, env=env,
                input=b"unexpected", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode().strip()
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},unexpected.txt"],
                cwd=root, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

        with patch.object(publisher, "add_candidates_binary", side_effect=inject_sixth_path):
            with self.assertRaises(PublishFailure) as raised:
                publish(self.root, RUN_DATE, enforce_today=False)
        self.assertEqual(raised.exception.code, "staged_path_set_invalid")
        self.assertEqual(self._git("rev-parse", "HEAD"), base)
        self.assertEqual(target_before, self._snapshot(("latest.html", "index.html", "archive.html", "2026-10-04.html")))
        self.assertFalse((self.root / "unexpected.txt").exists())

    def test_push_failure_is_single_attempt_and_does_not_pull_or_force(self):
        hook = self.remote / "hooks/pre-receive"
        count = Path(self.temp.name) / "push-hook-count"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(f"#!/bin/sh\nprintf x >> '{count}'\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        with self.assertRaises(PublishFailure) as raised:
            publish(self.root, RUN_DATE, enforce_today=False)
        self.assertEqual(raised.exception.code, "push_failed_no_retry")
        self.assertEqual(count.read_text(encoding="utf-8"), "x")
        self.assertEqual(self._git("rev-parse", "origin/main"), self._git("rev-parse", "HEAD^"))
        self.assertEqual(self._files_in_head_commit(), {
            "briefs/2026-10-06.md", "2026-10-06.html", "latest.html", "index.html", "archive.html"
        })


if __name__ == "__main__":
    unittest.main()
