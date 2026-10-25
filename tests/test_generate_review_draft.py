import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_review_draft.sh"
PROMPT = ROOT / "prompts" / "daily_review_draft.md"
GATE = ROOT / "scripts" / "review_draft_gate.py"


def make_brief(duplicate=False, missing_top3=False, japan_heading_only=False, japan_short=False, japan_shallow=False):
    top3 = ["1. First distinct event.", "2. Second distinct event."]
    if not missing_top3:
        top3.append("3. Third distinct event.")
    links = [
        "🔗 [原文を読む](https://www.reuters.com/world/a)",
        "🔗 [原文を読む](https://apnews.com/article/b)",
        "🔗 [原文を読む](https://www.bbc.com/news/articles/c)",
    ]
    repeated = [
        "英語記事タイトル：Repeated article",
        "情報源：Repeated source",
        "",
        links[0],
    ] if duplicate else []
    if japan_heading_only:
        japan_impact = []
    elif japan_short:
        japan_impact = ["日本への具体的な影響は、今回の確認資料からは判断できない。"]
    elif japan_shallow:
        japan_impact = ["選挙後の関係は判断材料となる。今後の状況を確認したい。"]
    else:
        japan_impact = [
            "備蓄放出が燃料供給や海外輸送コストにどう波及するかは、日本との具体的な接点になる。短期には実際の供給や費用への反映を確認する。",
            "",
            "中期には市場や制度の変化が判断材料となるが、個別企業への影響は根拠がない限り断定しない。",
        ]
    article = lambda index: [
        f"英語記事タイトル：Article {index}",
        f"情報源：Source {index}",
        "",
        links[index - 1],
    ]
    return "\n".join([
        "_Date: 2026-10-04_",
        "",
        "## Today's Top 3",
        *top3,
        "",
        "## Executive Summary",
        "A concise summary.",
        "",
        "## Why It Matters",
        "A concise rationale.",
        "",
        "## Big Picture",
        "A concise picture.",
        "",
        "## Tomorrow's Watchlist",
        "- One item.",
        "- Two item.",
        "- Three item.",
        "",
        "## 🌍 世界情勢",
        "A world item.",
        *article(1),
        "",
        "## 💹 経済",
        "An economy item.",
        *article(2),
        "",
        "## 🤖 AI・テクノロジー",
        "今回は掲載候補なし",
        "",
        "## 🔬 科学",
        "今回は掲載候補なし",
        "",
        "## 🇯🇵 日本への影響",
        *japan_impact,
        *article(3),
        *repeated,
        "",
    ])


class GenerateReviewDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="world-brief-review-test-")
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "prompts").mkdir()
        (self.root / "drafts").mkdir()
        (self.root / ".venv" / "bin").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.root / "scripts" / SCRIPT.name)
        shutil.copy2(GATE, self.root / "scripts" / GATE.name)
        shutil.copy2(PROMPT, self.root / "prompts" / PROMPT.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.calls = self.root / "codex-calls"
        self.git_marker = self.root / "git-was-called"
        self.git_marker.write_text("pre-existing dirty-worktree marker", encoding="utf-8")
        self._write_executable(self.fake_bin / "curl", "#!/bin/sh\nexit 0\n")
        self._write_executable(self.fake_bin / "sleep", "#!/bin/sh\nexit 0\n")
        self._write_executable(
            self.fake_bin / "date",
            "#!/bin/sh\nif [ \"$1\" = \"+%F\" ]; then printf '2026-10-04\\n'; else printf 'test date\\n'; fi\n",
        )
        self._write_executable(
            self.fake_bin / "git",
            "#!/bin/sh\nprintf called > \"$WORLD_BRIEF_TEST_GIT_MARKER\"\nexit 0\n",
        )
        self.codex = self.root / "fake-codex"
        self._write_executable(self.codex, self._codex_script("valid"))
        self.python = Path(os.environ.get("WORLD_BRIEF_TEST_PYTHON", os.sys.executable))

        self.public_files = {
            "briefs/2026-10-04.md": "existing brief sentinel",
            "2026-10-04.html": "existing date html sentinel",
            "latest.html": "existing latest sentinel",
            "index.html": "existing index sentinel",
            "archive.html": "existing archive sentinel",
        }
        for relative, content in self.public_files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _write_executable(self, path, text):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o700)

    def _codex_script(self, mode):
        payload = make_brief(
            missing_top3=(mode == "top3"),
            duplicate=(mode == "duplicate"),
            japan_heading_only=(mode == "japan_empty"),
            japan_short=(mode == "japan_short"),
            japan_shallow=(mode == "japan_shallow"),
        )
        return (
            "#!/bin/sh\n"
            "cat >/dev/null\n"
            "printf x >> \"$WORLD_BRIEF_TEST_CALLS\"\n"
            f"MODE={mode!r}\n"
            "if [ \"$MODE\" = nonzero ]; then exit 7; fi\n"
            "if [ \"$MODE\" = insufficient ]; then printf 'INSUFFICIENT_VERIFIED_ARTICLES'; exit 0; fi\n"
            "if [ \"$MODE\" = insufficient_extra ]; then printf 'INSUFFICIENT_VERIFIED_ARTICLES\\nextra'; exit 0; fi\n"
            "if [ \"$MODE\" = insufficient_typo ]; then printf 'INSUFFICIENT_VERIFIED_ARTICLE'; exit 0; fi\n"
            "if [ \"$MODE\" = invalid_date ]; then "
            f"printf '%s' {self._shell_quote(payload.replace('_Date: 2026-10-04_', '_Date: 2026-10-03_', 1))}; exit 0; fi\n"
            "if [ \"$MODE\" != empty ] && [ \"$MODE\" != nonzero ]; then\n"
            f"  printf '%s' {self._shell_quote(payload)}\n"
            "fi\n"
            "exit 0\n"
        )

    def _shell_quote(self, value):
        return "'" + value.replace("'", "'\\''") + "'"

    def _snapshot_public(self):
        return {relative: (self.root / relative).read_text(encoding="utf-8") for relative in self.public_files}

    def _run(self, mode="valid", python_bin=None, override=None):
        self._write_executable(self.codex, self._codex_script(mode))
        env = os.environ.copy()
        env.update({
            "PATH": str(self.fake_bin) + os.pathsep + env.get("PATH", ""),
            "WORLD_BRIEF_CODEX_BIN": str(self.codex),
            "WORLD_BRIEF_PYTHON_BIN": str(python_bin or self.python),
            "WORLD_BRIEF_MAX_ATTEMPTS": "4",
            "WORLD_BRIEF_TEST_CALLS": str(self.calls),
            "WORLD_BRIEF_TEST_GIT_MARKER": str(self.git_marker),
        })
        env.pop("WORLD_BRIEF_DRAFT_DIR", None)
        if override is not None:
            env["WORLD_BRIEF_DRAFT_DIR"] = override
        return subprocess.run(
            ["/bin/bash", str(self.root / "scripts" / SCRIPT.name)],
            cwd=self.root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

    def _assert_public_unchanged(self, before):
        self.assertEqual(before, self._snapshot_public())
        self.assertFalse(self.git_marker.with_name("git-was-called").read_text(encoding="utf-8") == "called")

    def test_valid_output_creates_only_human_review_drafts(self):
        before = self._snapshot_public()
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        md = self.root / "drafts/2026-10-04-human-review.md"
        page = self.root / "drafts/2026-10-04-human-review.html"
        self.assertTrue(md.is_file())
        self.assertTrue(page.is_file())
        md_text = md.read_text(encoding="utf-8")
        html_text = page.read_text(encoding="utf-8")
        self.assertIn("Human確認前・未公開", md_text)
        self.assertIn("Human未確認", md_text)
        self.assertIn("Human確認前・未公開", html_text)
        self.assertIn("../style.css", html_text)
        self.assertIn('href="https://www.reuters.com/world/a"', html_text)
        self.assertIn("SAFE-AUTO-DRAFT=READY", result.stdout)
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "x")
        self._assert_public_unchanged(before)

    def test_override_writes_only_to_specified_draft_directory(self):
        before = self._snapshot_public()
        result = self._run(override="drafts/live-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / "drafts/live-test"
        self.assertTrue((target / "2026-10-04-human-review.md").is_file())
        self.assertTrue((target / "2026-10-04-human-review.html").is_file())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.html").exists())
        self._assert_public_unchanged(before)

    def test_override_does_not_touch_existing_normal_draft(self):
        normal_md = self.root / "drafts/2026-10-04-human-review.md"
        normal_html = self.root / "drafts/2026-10-04-human-review.html"
        normal_md.write_text("approved markdown sentinel", encoding="utf-8")
        normal_html.write_text("approved html sentinel", encoding="utf-8")
        before = self._snapshot_public()
        result = self._run(override="drafts/live-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(normal_md.read_text(encoding="utf-8"), "approved markdown sentinel")
        self.assertEqual(normal_html.read_text(encoding="utf-8"), "approved html sentinel")
        self._assert_public_unchanged(before)

    def test_existing_override_output_stops_before_codex(self):
        target = self.root / "drafts/live-test"
        target.mkdir()
        (target / "2026-10-04-human-review.md").write_text("keep", encoding="utf-8")
        before = self._snapshot_public()
        result = self._run(override="drafts/live-test")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("draft_already_exists", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual((target / "2026-10-04-human-review.md").read_text(encoding="utf-8"), "keep")
        self._assert_public_unchanged(before)

    def test_top3_shortage_fails_without_public_or_draft_output(self):
        before = self._snapshot_public()
        result = self._run("top3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("top3_invalid", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertEqual(len(self.calls.read_text(encoding="utf-8")), 4)
        self._assert_public_unchanged(before)

    def test_empty_japan_impact_section_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("japan_empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("japan_impact_empty", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertEqual(len(self.calls.read_text(encoding="utf-8")), 4)
        self._assert_public_unchanged(before)

    def test_single_short_japan_impact_sentence_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("japan_short")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("japan_impact_too_shallow", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_shallow_two_sentence_single_paragraph_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("japan_shallow")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("japan_impact_too_shallow", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_duplicate_urls_fail_closed(self):
        before = self._snapshot_public()
        result = self._run("duplicate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate_source_url", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_nonzero_codex_exit_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("nonzero")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("codex_exit_nonzero", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_empty_output_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty_output", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_exact_insufficient_sentinel_returns_no_publish_without_retry_or_writes(self):
        before = self._snapshot_public()
        result = self._run("insufficient")
        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIn("NO_PUBLISH reason=insufficient_verified_articles", result.stdout)
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "x")
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.html").exists())
        self._assert_public_unchanged(before)

    def test_sentinel_with_extra_text_fails_closed_as_invalid_output(self):
        result = self._run("insufficient_extra")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_output", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8")), 4)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_typo_sentinel_fails_closed_as_invalid_output(self):
        result = self._run("insufficient_typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_output", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8")), 4)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_invalid_date_header_on_normal_draft_remains_a_gate_failure(self):
        result = self._run("invalid_date")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("date_header_invalid", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8")), 4)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_html_render_failure_fails_before_draft_or_public_writes(self):
        before = self._snapshot_public()
        wrapper = self.root / "python-fail-second-call"
        real_python = self.python
        wrapper.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-B\" ] && [ \"$2\" = \"-\" ] && [ \"$#\" -eq 7 ]; then exit 9; fi\n"
            f"exec {self._shell_quote(str(real_python))} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        result = self._run("valid", wrapper)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("html_render_failed", result.stderr)
        self.assertEqual(self.calls.read_text(encoding="utf-8"), "x")
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.html").exists())
        self._assert_public_unchanged(before)

    def test_prompt_has_no_48_hour_fallback_and_requires_three_sources(self):
        prompt = PROMPT.read_text(encoding="utf-8")
        self.assertIn("直近24時間", prompt)
        self.assertIn("48時間へ対象を広げてはいけません", prompt)
        self.assertIn("少なくとも3件", prompt)
        self.assertIn("固定文 `INSUFFICIENT_VERIFIED_ARTICLES` だけ", prompt)
        self.assertIn("この場合は日付ヘッダーを含めず", prompt)
        self.assertIn("48時間への拡張、source件数の引き下げ", prompt)
        self.assertIn("sentinel後の再試行", prompt)
        self.assertIn("## 🤖 AI・テクノロジー", prompt)
        self.assertIn("## 🔬 科学", prompt)
        self.assertIn("日本との具体的な接点", prompt)
        self.assertIn("短期・中期", prompt)
        self.assertIn("不確実性", prompt)
        self.assertIn("水増し", prompt)

    def test_script_has_no_git_publish_commands_or_public_builder(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bgit\s+(add|commit|push)\b", source))
        self.assertNotIn("build_html.py", source)
        self.assertNotIn("latest.html", source)
        self.assertNotIn("index.html", source)
        self.assertNotIn("archive.html", source)


if __name__ == "__main__":
    unittest.main()
