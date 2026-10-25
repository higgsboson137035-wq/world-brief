#!/usr/bin/env python3
"""Convert a gated Human Review draft and publish exactly five files."""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
import html
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import markdown

if __package__:
    from .review_draft_gate import SOURCE_LINK, validate_text
else:
    from review_draft_gate import SOURCE_LINK, validate_text


PUBLIC_PATHS = (
    "briefs/{date}.md",
    "{date}.html",
    "latest.html",
    "index.html",
    "archive.html",
)
EXPECTED_STATUS = {
    "briefs/{date}.md": "A",
    "{date}.html": "A",
    "latest.html": "M",
    "index.html": "M",
    "archive.html": "M",
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOTICE_MD = (
    "# World Brief — Human確認前・未公開",
    "> 記事の事実・出典・日時・リンクはHuman未確認です。",
)
NOTICE_HTML = (
    "Human確認前・未公開",
    "記事の事実・出典・日時・リンクはHuman未確認です。",
)
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "ol", "ul", "li",
    "strong", "em", "a", "hr", "br", "blockquote", "code", "pre",
}
VOID_TAGS = {"hr", "br"}


class PublishFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def git(root: Path, *args: str, index: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishFailure("git_command_failed") from exc
    return result


def require_git(root: Path, *args: str, index: Path | None = None) -> str:
    result = git(root, *args, index=index)
    if result.returncode != 0:
        raise PublishFailure("git_preflight_failed")
    return result.stdout


def exact_notice_removal(raw: bytes) -> bytes:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PublishFailure("draft_invalid_utf8") from exc
    lines = text.splitlines(keepends=True)
    if len(lines) < 4:
        raise PublishFailure("draft_notice_missing")
    normalized = [line.rstrip("\r\n") for line in lines[:4]]
    if normalized != [NOTICE_MD[0], "", NOTICE_MD[1], ""]:
        raise PublishFailure("draft_notice_unexpected")
    body = "".join(lines[4:])
    if any(token in body for token in (NOTICE_MD[0], "Human確認前・未公開", "Human未確認")):
        raise PublishFailure("review_notice_in_body")
    return body.encode("utf-8")


class PublicMarkdownSanitizer(HTMLParser):
    def __init__(self, approved_urls: set[str]):
        super().__init__(convert_charrefs=True)
        self.approved_urls = approved_urls
        self.output: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            raise PublishFailure("render_rejected_tag")
        if tag == "a":
            values = dict(attrs)
            href = values.get("href", "")
            if set(values) != {"href"} or href not in self.approved_urls or not href.startswith("https://"):
                raise PublishFailure("render_rejected_link")
            self.output.append('<a href="' + html.escape(href, quote=True) + '" rel="noopener noreferrer">')
        else:
            if attrs:
                raise PublishFailure("render_rejected_attribute")
            self.output.append(f"<{tag}>")
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            raise PublishFailure("render_invalid_nesting")
        self.stack.pop()
        self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(html.escape(data))


class DraftHTMLAudit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        for key, value in attrs:
            if key == "href" and value is not None:
                self.hrefs.append(value)


def verify_draft_html(path: Path, expected_date: str, expected_urls: set[str]) -> None:
    if path.is_symlink() or not path.is_file():
        raise PublishFailure("draft_html_missing")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublishFailure("draft_html_unreadable") from exc
    if not content or f"World Brief — {expected_date} — Human Review" not in content:
        raise PublishFailure("draft_html_invalid")
    if not all(token in content for token in NOTICE_HTML) or "../style.css" not in content:
        raise PublishFailure("draft_html_review_contract_invalid")
    audit = DraftHTMLAudit()
    try:
        audit.feed(content)
        audit.close()
    except Exception as exc:
        raise PublishFailure("draft_html_invalid") from exc
    actual_urls = {href for href in audit.hrefs if href.startswith("https://")}
    if actual_urls != expected_urls or any(
        href != "../style.css" and not href.startswith("https://") for href in audit.hrefs
    ):
        raise PublishFailure("draft_html_links_mismatch")


def render_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="style.css">
<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">
</head>
<body>
<article>
{body}
</article>
</body>
</html>
"""


def make_candidates(root: Path, run_date: str, stage: Path) -> tuple[dict[str, bytes], str]:
    draft_dir = root / "drafts"
    draft_md = draft_dir / f"{run_date}-human-review.md"
    draft_html = draft_dir / f"{run_date}-human-review.html"
    if draft_md.is_symlink() or not draft_md.is_file():
        raise PublishFailure("draft_markdown_missing")
    try:
        raw_md = draft_md.read_bytes()
    except OSError as exc:
        raise PublishFailure("draft_markdown_unreadable") from exc
    body_bytes = exact_notice_removal(raw_md)
    try:
        body_text = body_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PublishFailure("draft_invalid_utf8") from exc
    if validate_text(body_text, run_date) != "pass":
        raise PublishFailure("quality_gate_failed")

    urls = {
        match.group(1)
        for line in body_text.splitlines()
        if (match := SOURCE_LINK.fullmatch(line)) is not None
    }
    verify_draft_html(draft_html, run_date, urls)

    if re.search(r"<\s*/?\s*[A-Za-z!][^>]*>", body_text):
        raise PublishFailure("render_rejected_raw_html")
    rendered = markdown.markdown(body_text)
    sanitizer = PublicMarkdownSanitizer(urls)
    try:
        sanitizer.feed(rendered)
        sanitizer.close()
    except PublishFailure:
        raise
    except Exception as exc:
        raise PublishFailure("render_invalid_html") from exc
    if sanitizer.stack:
        raise PublishFailure("render_invalid_html")
    if not urls:
        raise PublishFailure("source_links_missing")

    public_page = render_page(f"World Brief - {run_date}", "".join(sanitizer.output))
    staged_md = stage / f"{run_date}.md"
    staged_md.write_bytes(body_bytes)
    md_mtime = datetime.fromtimestamp(staged_md.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    md_output = f"briefs/{run_date}.md"
    date_output = f"{run_date}.html"
    latest_output = "latest.html"
    index_output = "index.html"
    archive_output = "archive.html"
    index_body = f"""
<header>
<h1>World Brief</h1>
<p>毎日自動生成される世界情勢ブリーフィングです。</p>
<p><small>最終更新: {md_mtime}</small></p>

<nav>
<a href="archive.html">過去のブリーフ一覧</a>
</nav>
</header>

<hr>

{''.join(sanitizer.output)}
"""
    index_page = render_page("World Brief", index_body)

    brief_stems = []
    for path in (root / "briefs").glob("*.md"):
        if not DATE_RE.fullmatch(path.stem):
            raise PublishFailure("unexpected_brief_filename")
        brief_stems.append(path.stem)
    if run_date not in brief_stems:
        brief_stems.append(run_date)
    brief_stems = sorted(set(brief_stems), reverse=True)
    archive_items = "\n".join(
        f'<li><a href="{stem}.html">{html.escape(stem)}</a></li>' for stem in brief_stems
    )
    archive_body = f"""
<h1>World Brief Archive</h1>

<nav>
<a href="index.html">最新のブリーフへ戻る</a>
</nav>

<ul>
{archive_items}
</ul>
"""
    archive_page = render_page("World Brief Archive", archive_body)

    candidates = {
        md_output: body_bytes,
        date_output: public_page.encode("utf-8"),
        latest_output: public_page.encode("utf-8"),
        index_output: index_page.encode("utf-8"),
        archive_output: archive_page.encode("utf-8"),
    }
    public_pages = [data.decode("utf-8") for path, data in candidates.items() if path != md_output]
    if any(any(token in page for token in NOTICE_HTML) for page in public_pages):
        raise PublishFailure("review_notice_in_public_output")
    if "Human確認前" in body_text or "Human未確認" in body_text:
        raise PublishFailure("review_notice_in_public_output")
    return candidates, md_mtime


def allow_only_markdown_hardbreak_warnings(output: str, markdown_path: str) -> bool:
    if not output.strip():
        return True
    lines = output.splitlines()
    if len(lines) % 2:
        return False
    for index in range(0, len(lines), 2):
        if not re.fullmatch(re.escape(markdown_path) + r":\d+: trailing whitespace\.", lines[index]):
            return False
        added = lines[index + 1]
        if not added.startswith("+") or not re.search(r"[^ ][ ]{2}$", added):
            return False
    return True


def format_paths(run_date: str) -> tuple[str, ...]:
    return tuple(template.format(date=run_date) for template in PUBLIC_PATHS)


def preflight(root: Path, run_date: str, paths: tuple[str, ...]) -> str:
    if not DATE_RE.fullmatch(run_date):
        raise PublishFailure("date_invalid")
    top = require_git(root, "rev-parse", "--show-toplevel").strip()
    if Path(top).resolve() != root.resolve():
        raise PublishFailure("repository_root_mismatch")
    branch = require_git(root, "symbolic-ref", "--short", "HEAD").strip()
    if branch != "main":
        raise PublishFailure("branch_not_main")
    head = require_git(root, "rev-parse", "HEAD").strip()
    remote = require_git(root, "rev-parse", "origin/main").strip()
    if head != remote:
        raise PublishFailure("local_remote_diverged")
    staged = git(root, "diff", "--cached", "--quiet")
    if staged.returncode != 0:
        raise PublishFailure("preexisting_staged_changes")
    for path in paths:
        target = root / path
        if target.is_symlink():
            raise PublishFailure("publish_target_symlink")
        if path in (f"briefs/{run_date}.md", f"{run_date}.html"):
            if target.exists():
                raise PublishFailure("public_date_artifact_exists")
        else:
            if not target.is_file():
                raise PublishFailure("public_target_missing")
    dirty_targets = require_git(root, "status", "--porcelain", "--", *paths)
    if dirty_targets.strip():
        raise PublishFailure("publish_targets_dirty")
    return head


def validate_stage(root: Path, index: Path, run_date: str, paths: tuple[str, ...]) -> None:
    names = require_git(root, "diff", "--cached", "--name-only", index=index).splitlines()
    if set(names) != set(paths) or len(names) != 5:
        raise PublishFailure("staged_path_set_invalid")
    status_lines = require_git(root, "diff", "--cached", "--name-status", index=index).splitlines()
    statuses = {}
    for line in status_lines:
        parts = line.split("\t")
        if len(parts) != 2:
            raise PublishFailure("staged_status_invalid")
        statuses[parts[1]] = parts[0]
    expected = {key.format(date=run_date): value for key, value in EXPECTED_STATUS.items()}
    if statuses != expected:
        raise PublishFailure("staged_status_invalid")

    check = git(root, "diff", "--cached", "--check", index=index)
    check_text = check.stdout + check.stderr
    md_path = f"briefs/{run_date}.md"
    if check.returncode not in (0, 2) or not allow_only_markdown_hardbreak_warnings(check_text, md_path):
        raise PublishFailure("cached_diff_check_failed")

    numstat = require_git(root, "diff", "--cached", "--numstat", index=index).splitlines()
    if len(numstat) != 5:
        raise PublishFailure("cached_diff_stat_invalid")
    stat_paths = set()
    for line in numstat:
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-" or parts[1] == "-":
            raise PublishFailure("cached_diff_stat_invalid")
        stat_paths.add(parts[2])
    if stat_paths != set(paths):
        raise PublishFailure("cached_diff_stat_invalid")


def add_candidates_binary(root: Path, index: Path, candidates: dict[str, bytes]) -> None:
    require_git(root, "read-tree", "HEAD", index=index)
    for path, content in candidates.items():
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        try:
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"], cwd=root, env=env,
                input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublishFailure("candidate_blob_write_failed") from exc
        if blob.returncode != 0:
            raise PublishFailure("candidate_blob_write_failed")
        object_id = blob.stdout.decode("ascii", errors="strict").strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", object_id):
            raise PublishFailure("candidate_blob_write_failed")
        require_git(root, "update-index", "--add", "--cacheinfo", f"100644,{object_id},{path}", index=index)


def publish(root: Path, run_date: str, *, enforce_today: bool = True) -> str:
    if enforce_today and run_date != date.today().isoformat():
        raise PublishFailure("run_date_mismatch")
    paths = format_paths(run_date)
    base_head = preflight(root, run_date, paths)
    with tempfile.TemporaryDirectory(prefix=f".auto-publish-{run_date}-", dir=root / "drafts") as temp_name:
        temp = Path(temp_name)
        candidates, _index_time = make_candidates(root, run_date, temp)
        index = temp / "index"
        add_candidates_binary(root, index, candidates)
        validate_stage(root, index, run_date, paths)
        if require_git(root, "rev-parse", "HEAD").strip() != base_head:
            raise PublishFailure("head_changed_before_commit")
        message = f"Publish auto-reviewed World Brief for {run_date}"
        commit = git(root, "commit", "-m", message, index=index, timeout=120)
        if commit.returncode != 0:
            raise PublishFailure("commit_failed")

        new_head = require_git(root, "rev-parse", "HEAD").strip()
        parent = require_git(root, "rev-parse", "HEAD^1").strip()
        if parent != base_head:
            raise PublishFailure("commit_parent_invalid")
        committed_status = require_git(root, "show", "--format=", "--name-status", "HEAD").splitlines()
        committed_map = {}
        for line in committed_status:
            parts = line.split("\t")
            if len(parts) != 2:
                raise PublishFailure("commit_path_set_invalid")
            committed_map[parts[1]] = parts[0]
        expected = {key.format(date=run_date): value for key, value in EXPECTED_STATUS.items()}
        if committed_map != expected:
            raise PublishFailure("commit_path_set_invalid")
        commit_check = git(root, "diff", "HEAD^", "HEAD", "--check")
        commit_check_text = commit_check.stdout + commit_check.stderr
        if commit_check.returncode not in (0, 2) or not allow_only_markdown_hardbreak_warnings(
            commit_check_text, f"briefs/{run_date}.md"
        ):
            raise PublishFailure("commit_diff_check_failed")

        # The main index was proven clean before the isolated-index commit.
        require_git(root, "read-tree", "HEAD")
        for path, content in candidates.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_target = target.with_name(f".{target.name}.publish-sync-{new_head[:8]}")
            if temp_target.exists() or temp_target.is_symlink():
                raise PublishFailure("worktree_sync_temp_exists")
            temp_target.write_bytes(content)
            os.replace(temp_target, target)

        outgoing = require_git(root, "log", "origin/main..HEAD", "--oneline").splitlines()
        incoming = require_git(root, "log", "HEAD..origin/main", "--oneline").splitlines()
        if len(outgoing) != 1 or not outgoing[0].startswith(new_head[:7] + " ") or incoming:
            raise PublishFailure("remote_diff_preflight_failed")

        push = git(root, "push", "origin", "main", timeout=180)
        if push.returncode != 0:
            raise PublishFailure("push_failed_no_retry")
        remote_after = require_git(root, "rev-parse", "origin/main").strip()
        if remote_after != new_head:
            raise PublishFailure("push_tracking_ref_not_advanced")
        if require_git(root, "log", "origin/main..HEAD", "--oneline").strip():
            raise PublishFailure("push_outgoing_not_empty")
        if require_git(root, "log", "HEAD..origin/main", "--oneline").strip():
            raise PublishFailure("push_incoming_not_empty")
        return new_head


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    run_date = date.today().isoformat()
    try:
        sha = publish(root, run_date)
    except PublishFailure as exc:
        print(f"AUTO-PUBLISH=FAILED reason={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("AUTO-PUBLISH=FAILED reason=unexpected_error", file=sys.stderr)
        return 1
    print(f"AUTO-PUBLISH=PASS commit={sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
