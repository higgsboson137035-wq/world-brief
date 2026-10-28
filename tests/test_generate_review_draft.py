import json
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
DIAGNOSTICS = ROOT / "scripts" / "run_diagnostics.py"
REPAIR = ROOT / "scripts" / "format_repair.py"
JSON_CONTRACT = ROOT / "scripts" / "json_contract.py"
EVIDENCE_GATE = ROOT / "scripts" / "top3_evidence_gate.py"


def make_brief(duplicate=False, missing_top3=False, japan_heading_only=False, japan_short=False, japan_shallow=False, extra_heading=False):
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
        *( ["", "## 詳細：アジア", "Asian detail retained by repair."] if extra_heading else [] ),
        "",
    ])


def make_candidate_diagnostics():
    sources = [
        ("Reuters", "https://www.reuters.com/world/a"),
        ("AP", "https://apnews.com/article/b"),
        ("BBC", "https://www.bbc.com/news/articles/c"),
    ]
    return {
        "coverage": "COMPLETE",
        "candidates": [
            {
                "candidate_id": f"C{index}", "title": f"Article {index}",
                "source": source, "url": url, "published_at": "UNKNOWN",
                "verified_summary": f"{['First', 'Second', 'Third'][index - 1]} distinct event.",
                "within_24h": True, "source_verified": True, "duplicate": False,
                "selected_for_top3": True, "rejection_reason": None,
            }
            for index, (source, url) in enumerate(sources, start=1)
        ],
    }


def response_json(body, final_status="PUBLISH", diagnostics=None):
    diagnostics = diagnostics or make_candidate_diagnostics()
    top3 = [] if final_status == "NO_PUBLISH" else [
        {
            "candidate_id": item["candidate_id"],
            "headline": item["title"],
            "source_title": item["title"],
            "source": item["source"],
            "source_url": item["url"],
            "summary": item["verified_summary"],
        }
        for item in diagnostics["candidates"] if item.get("selected_for_top3") is True
    ]
    return json.dumps({
        "final_status": final_status,
        "brief_markdown": body,
        "top3": top3,
        "candidate_diagnostics": diagnostics,
    }, ensure_ascii=False)


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
        shutil.copy2(DIAGNOSTICS, self.root / "scripts" / DIAGNOSTICS.name)
        shutil.copy2(REPAIR, self.root / "scripts" / REPAIR.name)
        shutil.copy2(JSON_CONTRACT, self.root / "scripts" / JSON_CONTRACT.name)
        shutil.copy2(EVIDENCE_GATE, self.root / "scripts" / EVIDENCE_GATE.name)
        shutil.copy2(PROMPT, self.root / "prompts" / PROMPT.name)
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.calls = self.root / "codex-calls"
        self.git_marker = self.root / "git-was-called"
        self.git_marker.write_text("pre-existing dirty-worktree marker", encoding="utf-8")
        self._write_executable(self.fake_bin / "curl", "#!/bin/sh\n[ \"${WORLD_BRIEF_TEST_NETWORK_FAIL:-0}\" = 1 ] && exit 7\nexit 0\n")
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
            extra_heading=(mode == "extra_heading"),
        )
        publish_response = response_json(payload)
        no_publish_response = response_json(
            "INSUFFICIENT_VERIFIED_ARTICLES", "NO_PUBLISH",
            {"coverage": "PARTIAL", "candidates": []},
        )
        return (
            "#!/bin/sh\n"
            "PROMPT=$(cat)\n"
            "printf '%s\\n' \"$PROMPT\" | sed -n 's/^SEARCH_STAGE=//p' >> \"$WORLD_BRIEF_TEST_CALLS\"\n"
            f"MODE={mode!r}\n"
            "if [ \"$MODE\" = nonzero ]; then exit 7; fi\n"
            f"if [ \"$MODE\" = insufficient ]; then printf '%s' {self._shell_quote(no_publish_response)}; exit 0; fi\n"
            "if [ \"$MODE\" = insufficient_extra ]; then printf 'INSUFFICIENT_VERIFIED_ARTICLES\\nextra'; exit 0; fi\n"
            "if [ \"$MODE\" = insufficient_typo ]; then printf 'INSUFFICIENT_VERIFIED_ARTICLE'; exit 0; fi\n"
            "if [ \"$MODE\" = invalid_date ]; then "
            f"printf '%s' {self._shell_quote(response_json(payload.replace('_Date: 2026-10-04_', '_Date: 2026-10-03_', 1)))}; exit 0; fi\n"
            "if [ \"$MODE\" != empty ] && [ \"$MODE\" != nonzero ]; then\n"
            f"  printf '%s' {self._shell_quote(publish_response)}\n"
            "fi\n"
            "exit 0\n"
        )

    def _shell_quote(self, value):
        return "'" + value.replace("'", "'\\''") + "'"

    def _sequence_codex(self, sentinel_attempts):
        payload = make_brief()
        publish_response = response_json(payload)
        sentinel_response = response_json(
            "INSUFFICIENT_VERIFIED_ARTICLES", "NO_PUBLISH",
            {"coverage": "PARTIAL", "candidates": []},
        )
        self._write_executable(
            self.codex,
            "#!/bin/sh\n"
            "PROMPT=$(cat)\n"
            "printf '%s\\n' \"$PROMPT\" | sed -n 's/^SEARCH_STAGE=//p' >> \"$WORLD_BRIEF_TEST_CALLS\"\n"
            "count=$(wc -l < \"$WORLD_BRIEF_TEST_CALLS\")\n"
            f"if [ \"$count\" -le {sentinel_attempts} ]; then printf '%s' {self._shell_quote(sentinel_response)}; else printf '%s' {self._shell_quote(publish_response)}; fi\n",
        )

    def _set_response(self, response):
        self._write_executable(
            self.codex,
            "#!/bin/sh\nPROMPT=$(cat)\n"
            "printf '%s\\n' \"$PROMPT\" | sed -n 's/^SEARCH_STAGE=//p' >> \"$WORLD_BRIEF_TEST_CALLS\"\n"
            f"printf '%s' {self._shell_quote(response)}\n",
        )

    def _snapshot_public(self):
        return {relative: (self.root / relative).read_text(encoding="utf-8") for relative in self.public_files}

    def _run(self, mode="valid", python_bin=None, override=None):
        if mode != "custom":
            self._write_executable(self.codex, self._codex_script(mode))
        env = os.environ.copy()
        env.update({
            "PATH": str(self.fake_bin) + os.pathsep + env.get("PATH", ""),
            "WORLD_BRIEF_CODEX_BIN": str(self.codex),
            "WORLD_BRIEF_PYTHON_BIN": str(python_bin or self.python),
            "WORLD_BRIEF_TEST_CALLS": str(self.calls),
            "WORLD_BRIEF_TEST_GIT_MARKER": str(self.git_marker),
        })
        env.pop("WORLD_BRIEF_DRAFT_DIR", None)
        if override is not None:
            env["WORLD_BRIEF_DRAFT_DIR"] = override
        env["WORLD_BRIEF_TEST_NETWORK_FAIL"] = "1" if mode == "network_unavailable" else "0"
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

    def _assert_valid_generation_reached_renderer(self, result):
        if result.returncode != 0:
            self.assertIn("ModuleNotFoundError: No module named 'markdown'", result.stderr)
            self.assertIn("SAFE_DRAFT_FAILED: html_render_failed", result.stderr)
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(record["final_gate_reason"], "pass")

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
        run_dir = self.root / "logs/runs/2026-10-04"
        self.assertTrue((run_dir / "attempt-1-codex-output.md").is_file())
        self.assertTrue((run_dir / "attempt-1-gate.json").is_file())
        self.assertTrue((run_dir / "attempt-1-candidates.json").is_file())
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        self.assertFalse(record["format_repair_attempted"])
        self.assertFalse(record["contract_repair_attempted"])
        self.assertEqual(record["original_gate_result"], "PASS")
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
        self.assertIn("top3_evidence_inconsistent", result.stderr)
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertFalse(record["format_repair_attempted"])
        self.assertEqual(record["evidence_consistency_result"], "FAIL")
        self.assertEqual(record["evidence_consistency_reason"], "top3_evidence_inconsistent")
        self.assertEqual(record["original_gate_result"], "NOT_RUN")
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertEqual(len(self.calls.read_text(encoding="utf-8").splitlines()), 1)
        self._assert_public_unchanged(before)

    def test_empty_japan_impact_section_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("japan_empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("japan_impact_empty", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertEqual(len(self.calls.read_text(encoding="utf-8").splitlines()), 1)
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
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertFalse(record["format_repair_attempted"])
        self.assertEqual(record["original_gate_reason"], "japan_impact_too_shallow")
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
        self.assertTrue((self.root / "logs/runs/2026-10-04/attempt-1-codex-output.md").is_file())
        self.assertIn("codex_exit_nonzero", (self.root / "logs/runs/2026-10-04/attempt-1-gate.json").read_text(encoding="utf-8"))
        self._assert_public_unchanged(before)

    def test_network_preflight_failure_is_distinct_and_does_not_start_search_recovery(self):
        result = self._run("network_unavailable")
        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("network_unavailable", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_empty_output_fails_closed(self):
        before = self._snapshot_public()
        result = self._run("empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED_JSON_CONTRACT", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self._assert_public_unchanged(before)

    def test_exact_insufficient_sentinel_advances_through_distinct_search_stages(self):
        before = self._snapshot_public()
        result = self._run("insufficient")
        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("RETRIEVAL_EXHAUSTED", result.stdout)
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL", "TRUSTED_SOURCES", "CATEGORY_EXPANSION"])
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.html").exists())
        run_dir = self.root / "logs/runs/2026-10-04"
        self.assertEqual(json.loads((run_dir / "attempt-1-codex-output.md").read_text(encoding="utf-8"))["final_status"], "NO_PUBLISH")
        self.assertEqual((run_dir / "final-output.md").read_text(encoding="utf-8"), "INSUFFICIENT_VERIFIED_ARTICLES")
        self.assertIn('"status": "RECORDED"', (run_dir / "candidate-diagnostics.json").read_text(encoding="utf-8"))
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual([item["strategy"] for item in record["attempts"]], ["GENERAL", "TRUSTED_SOURCES", "CATEGORY_EXPANSION"])
        self.assertTrue(all(item["gate_result"] == "RETRY" and item["sentinel_seen"] for item in record["attempts"]))
        self.assertEqual([item["candidate_count"] for item in record["attempts"]], [0, 0, 0])
        self._assert_public_unchanged(before)

    def test_general_sentinel_then_trusted_source_success_stops_after_two_stages(self):
        self._sequence_codex(1)
        result = self._run("custom")
        self._assert_valid_generation_reached_renderer(result)
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL", "TRUSTED_SOURCES"])
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual([item["search_stage"] for item in record["attempts"]], ["GENERAL", "TRUSTED_SOURCES"])
        self.assertEqual(record["attempts"][0]["query_family"], "general_world_news")
        self.assertEqual(record["attempts"][1]["strategy"], "TRUSTED_SOURCES")

    def test_two_sentinels_then_category_expansion_success(self):
        self._sequence_codex(2)
        result = self._run("custom")
        self._assert_valid_generation_reached_renderer(result)
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL", "TRUSTED_SOURCES", "CATEGORY_EXPANSION"])
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(record["final_status"], "FAILED")  # generator records a draft run; wrapper owns publication state.
        self.assertEqual(record["attempts"][2]["query_family"], "geopolitics_conflict|world_politics_diplomacy|economy_energy|disasters|security|science_technology")

    def test_json_no_publish_envelope_saves_candidate_diagnostics_and_raw_output(self):
        candidate = {
            "candidate_id": "C1", "title": "One story", "source": "Reuters",
            "url": "https://www.reuters.com/world/a", "published_at": "UNKNOWN",
            "within_24h": "UNKNOWN", "source_verified": True, "duplicate": False,
            "selected_for_top3": False, "rejection_reason": "outside_24h",
        }
        response = json.dumps({
            "final_status": "NO_PUBLISH",
            "brief_markdown": "INSUFFICIENT_VERIFIED_ARTICLES",
            "top3": [],
            "candidate_diagnostics": {"coverage": "PARTIAL", "candidates": [candidate]},
        }, ensure_ascii=False)
        self._write_executable(
            self.codex,
            "#!/bin/sh\ncat >/dev/null\n"
            f"printf '%s' {self._shell_quote(response)}\n",
        )
        result = self._run("custom")
        self.assertEqual(result.returncode, 11, result.stderr)
        run_dir = self.root / "logs/runs/2026-10-04"
        saved = json.loads((run_dir / "candidate-diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "RECORDED")
        self.assertEqual(saved["coverage"], "PARTIAL")
        self.assertEqual(saved["candidates"], [candidate])
        self.assertIn('"candidate_diagnostics"', (run_dir / "attempt-1-codex-output.md").read_text(encoding="utf-8"))
        self.assertEqual((run_dir / "final-output.md").read_text(encoding="utf-8"), "INSUFFICIENT_VERIFIED_ARTICLES")
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_contract_repair_removes_embedded_diagnostics_before_quality_gate(self):
        body = make_brief() + (
            "\n## Candidate Diagnostics\n\n"
            "This stage reviewed candidates; coverage=PARTIAL.\n\n"
            "| ID | Article | Published At | Within 24h | Selection |\n"
            "|---|---|---|---|---|\n"
            "| C1 | Article 1 | UNKNOWN | true | selected_for_top3 |\n\n"
            "## Coverage Note\n\nSearch coverage was partial.\n"
        )
        expected_candidates = make_candidate_diagnostics()["candidates"]
        self._set_response(response_json(body))
        result = self._run("custom")
        self.assertEqual(result.returncode, 0, result.stderr)
        run_dir = self.root / "logs/runs/2026-10-04"
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        self.assertTrue(record["contract_repair_attempted"])
        self.assertEqual(record["contract_repair_result"], "PASS")
        self.assertEqual(record["json_contract_result"], "PASS")
        self.assertEqual(record["original_gate_result"], "PASS")
        self.assertTrue((run_dir / "attempt-1-original-json-output.json").is_file())
        self.assertTrue((run_dir / "attempt-1-contract-repaired-json.json").is_file())
        original = (run_dir / "attempt-1-original-brief-markdown.md").read_text(encoding="utf-8")
        repaired = (run_dir / "attempt-1-contract-repaired-brief-markdown.md").read_text(encoding="utf-8")
        self.assertIn("Candidate Diagnostics", original)
        self.assertNotIn("Candidate Diagnostics", repaired)
        saved = json.loads((run_dir / "attempt-1-candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["candidates"], expected_candidates)
        final_brief = (self.root / "drafts/2026-10-04-human-review.md").read_text(encoding="utf-8")
        self.assertNotIn("candidate_id", final_brief)
        self.assertNotIn("coverage=PARTIAL", final_brief)
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])

    def test_contract_repair_then_format_repair_run_in_order(self):
        body = make_brief(extra_heading=True) + (
            "\n## Candidate Diagnostics\n\n| candidate_id | title | selected_for_top3 |\n"
            "|---|---|---|\n| C1 | Article 1 | true |\n"
        )
        self._set_response(response_json(body))
        result = self._run("custom")
        self.assertEqual(result.returncode, 0, result.stderr)
        run_dir = self.root / "logs/runs/2026-10-04"
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        gate = json.loads((run_dir / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertTrue(record["contract_repair_attempted"])
        self.assertTrue(record["format_repair_attempted"])
        self.assertEqual(record["contract_repair_result"], "PASS")
        self.assertEqual(record["original_gate_reason"], "required_heading_order_invalid")
        self.assertEqual(record["repaired_gate_result"], "PASS")
        self.assertTrue((run_dir / gate["contract_repaired_brief_markdown"]).is_file())
        self.assertTrue((run_dir / gate["format_repaired_brief_markdown"]).is_file())
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])

    def test_top3_candidate_mismatch_fails_before_quality_gate_without_retry_or_repair(self):
        payload = json.loads(response_json(make_brief()))
        wrong = "韓国の7〜9月期成長率が市場予想を上回った。"
        payload["top3"][2]["headline"] = "South Korea economy grows faster than expected"
        payload["top3"][2]["source_title"] = "South Korea economy grows faster than expected"
        payload["top3"][2]["summary"] = wrong
        payload["brief_markdown"] = payload["brief_markdown"].replace("3. Third distinct event.", f"3. {wrong}")
        self._set_response(json.dumps(payload, ensure_ascii=False))
        before = self._snapshot_public()
        result = self._run("custom")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("top3_source_title_mismatch", result.stderr)
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(record["evidence_consistency_result"], "FAIL")
        self.assertEqual(record["evidence_consistency_reason"], "top3_source_title_mismatch")
        self.assertTrue(any(item.get("candidate_title") == "Article 3"
                            for item in record["evidence_consistency_details"]))
        self.assertEqual(record["original_gate_result"], "NOT_RUN")
        self.assertFalse(record["format_repair_attempted"])
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])
        self._assert_public_unchanged(before)

    def test_failed_contract_repair_stops_before_quality_gate(self):
        body = make_brief() + "\n## Candidate Diagnostics\n\n| candidate_id | title |\n|---|---|\n| C1 | Article 1 |\n"
        self._set_response(response_json(body))
        contract_path = self.root / "scripts/json_contract.py"
        contract_real = self.root / "scripts/json_contract_real.py"
        contract_path.rename(contract_real)
        self._write_executable(
            contract_path,
            "#!/usr/bin/env python3\nimport pathlib, runpy, shutil, sys\n"
            f"real = pathlib.Path({str(contract_real)!r})\n"
            "if sys.argv[1] == 'repair':\n"
            "    shutil.copyfile(sys.argv[2], sys.argv[3])\n"
            "    print('PASS none')\n"
            "else:\n"
            "    sys.path.insert(0, str(real.parent))\n"
            "    runpy.run_path(str(real), run_name='__main__')\n",
        )
        result = self._run("custom")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED_JSON_CONTRACT", result.stderr)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        run_dir = self.root / "logs/runs/2026-10-04"
        gate = json.loads((run_dir / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["json_contract_result"], "FAIL")
        self.assertEqual(gate["contract_repair_result"], "FAIL")
        self.assertEqual(gate["gate_reason"], "FAILED_JSON_CONTRACT")
        self.assertEqual(gate["original_gate_result"], "NOT_RUN")
        self.assertFalse(gate["format_repair_attempted"])

    def test_gate_failure_stops_after_one_strategy_and_logs_reason(self):
        result = self._run("invalid_date")
        self.assertNotEqual(result.returncode, 0)
        run_dir = self.root / "logs/runs/2026-10-04"
        self.assertTrue((run_dir / "attempt-1-codex-output.md").is_file())
        gate = json.loads((run_dir / "attempt-1-gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["gate_reason"], "date_header_invalid")
        self.assertEqual(gate["search_stage"], "GENERAL")
        self.assertFalse(gate["format_repair_attempted"])
        self.assertEqual(gate["original_gate_reason"], "date_header_invalid")
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(len(record["attempts"]), 1)

    def test_extra_heading_is_repaired_once_without_search_and_both_outputs_are_saved(self):
        result = self._run("extra_heading")
        self._assert_valid_generation_reached_renderer(result)
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])
        run_dir = self.root / "logs/runs/2026-10-04"
        record = json.loads((run_dir / "diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(record["original_gate_result"], "FAIL")
        self.assertEqual(record["original_gate_reason"], "required_heading_order_invalid")
        self.assertTrue(record["format_repair_attempted"])
        self.assertEqual(record["format_repair_result"], "PASS")
        self.assertEqual(record["repaired_gate_result"], "PASS")
        self.assertEqual(record["repaired_gate_reason"], "pass")
        original = (run_dir / "attempt-1-original-output.md").read_text(encoding="utf-8")
        repaired = (run_dir / "attempt-1-repaired-output.md").read_text(encoding="utf-8")
        self.assertIn("## 詳細：アジア", original)
        self.assertNotIn("## 詳細：アジア", repaired)
        self.assertIn("Asian detail retained by repair.", repaired)
        self.assertEqual((run_dir / "attempt-1-final-output.md").read_text(encoding="utf-8"), repaired)
        self.assertEqual((run_dir / "attempt-1-candidates.json").read_bytes(), (run_dir / "candidate-diagnostics.json").read_bytes())

    def test_repair_that_still_fails_gate_is_limited_to_one_attempt(self):
        calls = self.root / "repair-calls"
        fake_repair = (
            "import sys\nfrom pathlib import Path\n"
            "if sys.argv[1] == 'classify': print('FORMAT_REPAIRABLE')\n"
            "elif sys.argv[1] == 'repair':\n"
            f" p=Path({str(calls)!r}); p.write_text(p.read_text() + 'repair\\n' if p.exists() else 'repair\\n')\n"
            " Path(sys.argv[3]).write_text(Path(sys.argv[2]).read_text())\n"
        )
        (self.root / "scripts" / REPAIR.name).write_text(fake_repair, encoding="utf-8")
        result = self._run("extra_heading")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required_heading_order_invalid", result.stderr)
        self.assertEqual(calls.read_text(encoding="utf-8").splitlines(), ["repair"])
        record = json.loads((self.root / "logs/runs/2026-10-04/diagnostic.json").read_text(encoding="utf-8"))
        self.assertTrue(record["format_repair_attempted"])
        self.assertEqual(record["format_repair_result"], "PASS")
        self.assertEqual(record["repaired_gate_result"], "FAIL")
        self.assertEqual(record["repaired_gate_reason"], "required_heading_order_invalid")
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_sentinel_with_extra_text_fails_closed_as_invalid_output(self):
        result = self._run("insufficient_extra")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED_JSON_CONTRACT", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8").splitlines()), 1)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_typo_sentinel_fails_closed_as_invalid_output(self):
        result = self._run("insufficient_typo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAILED_JSON_CONTRACT", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8").splitlines()), 1)
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())

    def test_invalid_date_header_on_normal_draft_remains_a_gate_failure(self):
        result = self._run("invalid_date")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("date_header_invalid", result.stderr)
        self.assertEqual(len(self.calls.read_text(encoding="utf-8").splitlines()), 1)
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
        self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["GENERAL"])
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.md").exists())
        self.assertFalse((self.root / "drafts/2026-10-04-human-review.html").exists())
        self._assert_public_unchanged(before)

    def test_prompt_has_no_48_hour_fallback_and_requires_three_sources(self):
        prompt = PROMPT.read_text(encoding="utf-8")
        self.assertIn("直近24時間", prompt)
        self.assertIn("48時間へ対象を広げてはいけません", prompt)
        self.assertIn("少なくとも3件", prompt)
        self.assertIn("現在のsearch stageで公開可能なBriefを構成できなかった", prompt)
        self.assertIn("candidate_diagnostics", prompt)
        self.assertIn('"top3": [', prompt)
        self.assertIn('"source_url": "candidate URL exact copy"', prompt)
        self.assertIn("verified_summary", prompt)
        self.assertIn("本文へコピー・要約・表として再掲しません", prompt)
        self.assertIn("selected_for_top3", prompt)
        self.assertIn("候補診断表", prompt)
        self.assertIn("within_24h", prompt)
        self.assertIn('"final_status": "PUBLISH"', prompt)
        self.assertIn("brief_markdown`を`INSUFFICIENT_VERIFIED_ARTICLES`", prompt)
        self.assertIn("48時間への拡張、source条件の引き下げ", prompt)
        self.assertIn("現在のsearch stage", prompt)
        self.assertIn("## 🤖 AI・テクノロジー", prompt)
        self.assertIn("## 🔬 科学", prompt)
        self.assertIn("日本との具体的な接点", prompt)
        self.assertIn("短期・中期", prompt)
        self.assertIn("不確実性", prompt)
        self.assertIn("水増し", prompt)
        self.assertIn("列挙した`##`見出しだけ", prompt)
        self.assertIn("地域別・テーマ別の追加sectionは作らない", prompt)

    def test_script_has_no_git_publish_commands_or_public_builder(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bgit\s+(add|commit|push)\b", source))
        self.assertNotIn("build_html.py", source)
        self.assertNotIn("latest.html", source)
        self.assertNotIn("index.html", source)
        self.assertNotIn("archive.html", source)


if __name__ == "__main__":
    unittest.main()
