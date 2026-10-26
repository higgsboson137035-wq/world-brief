#!/usr/bin/env python3
"""Record an officially confirmed no-publish result on the site's index page."""

from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import re
import subprocess
import sys


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUS_START = "<!-- WORLD_BRIEF_NO_PUBLISH_STATUS_START -->"
STATUS_END = "<!-- WORLD_BRIEF_NO_PUBLISH_STATUS_END -->"
STATUS_META_START = "<!-- WORLD_BRIEF_NO_PUBLISH_META_START -->"
STATUS_META_END = "<!-- WORLD_BRIEF_NO_PUBLISH_META_END -->"
ARTICLE_HEADING_START = "<!-- WORLD_BRIEF_NO_PUBLISH_ARTICLE_HEADING_START -->"
ARTICLE_HEADING_END = "<!-- WORLD_BRIEF_NO_PUBLISH_ARTICLE_HEADING_END -->"
STATUS_PATH = "index.html"


class NoPublishFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def git(root: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, env=os.environ.copy(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NoPublishFailure("git_command_failed") from exc


def require_git(root: Path, *args: str, timeout: int = 60) -> str:
    result = git(root, *args, timeout=timeout)
    if result.returncode != 0:
        raise NoPublishFailure("git_preflight_failed")
    return result.stdout


def valid_date(value: str) -> bool:
    if not DATE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def latest_public_date(root: Path, run_date: str) -> str:
    published = []
    for path in (root / "briefs").glob("*.md"):
        if not valid_date(path.stem):
            raise NoPublishFailure("unexpected_brief_filename")
        if path.stem >= run_date:
            raise NoPublishFailure("public_brief_not_older_than_no_publish_date")
        published.append(path.stem)
    if not published:
        raise NoPublishFailure("latest_public_brief_missing")
    latest = max(published)
    latest_html = root / f"{latest}.html"
    if latest_html.is_symlink() or not latest_html.is_file():
        raise NoPublishFailure("latest_public_html_missing")
    return latest


def status_block(run_date: str, latest_date: str) -> str:
    run = datetime.strptime(run_date, "%Y-%m-%d")
    latest = datetime.strptime(latest_date, "%Y-%m-%d")
    run_label = f"{run.year}年{run.month}月{run.day}日"
    latest_label = f"{latest.year}年{latest.month}月{latest.day}日"
    return "\n".join((
        STATUS_START,
        '<section role="status" aria-label="本日の公開状況" '
        'style="border:1px solid #cbd5e1;border-left:4px solid #2563eb;'
        'border-radius:8px;background:#f8fafc;padding:1rem 1.25rem;margin:1.25rem 0">',
        "<h2>本日の状態</h2>",
        f"<p><strong>{run_label} — 本日は品質基準により公開なし</strong></p>",
        "<p>本日の自動選定では、公開基準を満たすWorld Briefを構成できなかったため、公開を見送りました。</p>",
        f'<p>最新の公開記事：<a href="{latest_date}.html">{latest_label}版 World Brief</a></p>',
        "</section>",
        STATUS_END,
    ))


def replace_managed_block(
    source: str,
    start_marker: str,
    end_marker: str,
    block: str,
    anchor: str | re.Pattern[str],
    missing_marker_code: str,
    malformed_marker_code: str,
    *,
    replace_match: bool = False,
) -> tuple[str, bool]:
    start_count = source.count(start_marker)
    end_count = source.count(end_marker)
    if start_count or end_count:
        if start_count != 1 or end_count != 1:
            raise NoPublishFailure(malformed_marker_code)
        start = source.index(start_marker)
        end = source.index(end_marker)
        if end < start:
            raise NoPublishFailure(malformed_marker_code)
        end += len(end_marker)
        updated = source[:start] + block + source[end:]
        return updated, updated != source

    if re.search(r"<h2>最新の公開記事 — .*?</h2>", source):
        raise NoPublishFailure(missing_marker_code)
    if isinstance(anchor, re.Pattern):
        matches = list(anchor.finditer(source))
        if len(matches) != 1:
            raise NoPublishFailure(missing_marker_code)
        match = matches[0]
        if replace_match:
            updated = source[:match.start()] + block + source[match.end():]
        else:
            updated = source[:match.start()] + block + "\n\n" + source[match.start():]
        return updated, True
    if source.count(anchor) != 1:
        raise NoPublishFailure(missing_marker_code)
    updated = source.replace(anchor, block, 1)
    return updated, True


def apply_no_publish_display(
    index_text: str, run_date: str, latest_date: str
) -> tuple[str, bool]:
    """Separate the no-publish update date from the latest published article date."""
    updated = index_text
    changed = False

    meta_block = "\n".join((
        STATUS_META_START,
        f"<p><small>状態更新: {run_date}</small></p>",
        STATUS_META_END,
    ))
    updated, did_change = replace_managed_block(
        updated,
        STATUS_META_START,
        STATUS_META_END,
        meta_block,
        re.compile(r"<p><small>最終更新: \d{4}-\d{2}-\d{2} \d{2}:\d{2}</small></p>"),
        "index_update_meta_anchor_invalid",
        "no_publish_meta_markers_invalid",
        replace_match=True,
    )
    changed |= did_change

    updated, did_change = replace_managed_block(
        updated,
        STATUS_START,
        STATUS_END,
        status_block(run_date, latest_date),
        "</header>\n\n<hr>",
        "index_header_anchor_invalid",
        "no_publish_status_markers_invalid",
    )
    changed |= did_change

    latest = datetime.strptime(latest_date, "%Y-%m-%d")
    latest_label = f"{latest.year}年{latest.month}月{latest.day}日"
    article_heading = "\n".join((
        ARTICLE_HEADING_START,
        f"<h2>最新の公開記事 — {latest_label}</h2>",
        ARTICLE_HEADING_END,
    ))
    updated, did_change = replace_managed_block(
        updated,
        ARTICLE_HEADING_START,
        ARTICLE_HEADING_END,
        article_heading,
        re.compile(rf"<p><em>Date: {re.escape(latest_date)}</em></p>"),
        "latest_article_date_anchor_invalid",
        "no_publish_article_heading_markers_invalid",
    )
    changed |= did_change

    article_date = re.compile(
        rf"<p><em>Date: {re.escape(latest_date)}</em></p>\n?"
    )
    date_matches = article_date.findall(updated)
    if len(date_matches) == 1:
        updated, removed = article_date.subn("", updated, count=1)
        changed |= removed == 1
    elif len(date_matches) == 0 and article_heading in updated:
        pass
    else:
        raise NoPublishFailure("latest_article_date_anchor_invalid")
    return updated, changed


def preflight(root: Path, run_date: str) -> str:
    if not valid_date(run_date):
        raise NoPublishFailure("date_invalid")
    top = require_git(root, "rev-parse", "--show-toplevel").strip()
    if Path(top).resolve() != root.resolve():
        raise NoPublishFailure("repository_root_mismatch")
    if require_git(root, "symbolic-ref", "--short", "HEAD").strip() != "main":
        raise NoPublishFailure("branch_not_main")
    head = require_git(root, "rev-parse", "HEAD").strip()
    remote = require_git(root, "rev-parse", "origin/main").strip()
    if head != remote:
        raise NoPublishFailure("local_remote_diverged")
    staged = git(root, "diff", "--cached", "--quiet")
    if staged.returncode != 0:
        raise NoPublishFailure("preexisting_staged_changes")

    for path in (root / "briefs" / f"{run_date}.md", root / f"{run_date}.html"):
        if path.exists() or path.is_symlink():
            raise NoPublishFailure("public_date_artifact_exists")
    for relative in (STATUS_PATH, "latest.html", "archive.html"):
        target = root / relative
        if target.is_symlink() or not target.is_file():
            raise NoPublishFailure("public_target_missing")
    if require_git(root, "status", "--porcelain", "--", STATUS_PATH).strip():
        raise NoPublishFailure("no_publish_target_dirty")
    latest_public_date(root, run_date)
    return head


def record_no_publish(root: Path, run_date: str, *, enforce_today: bool = True) -> str | None:
    if enforce_today and run_date != date.today().isoformat():
        raise NoPublishFailure("run_date_mismatch")
    base_head = preflight(root, run_date)
    index_path = root / STATUS_PATH
    try:
        index_text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NoPublishFailure("index_unreadable") from exc

    latest_date = latest_public_date(root, run_date)
    updated, changed = apply_no_publish_display(index_text, run_date, latest_date)
    if not changed:
        return None

    try:
        index_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        raise NoPublishFailure("index_write_failed") from exc

    if git(root, "add", "--", STATUS_PATH).returncode != 0:
        raise NoPublishFailure("status_stage_failed")
    staged_paths = require_git(root, "diff", "--cached", "--name-only").splitlines()
    if staged_paths != [STATUS_PATH]:
        raise NoPublishFailure("status_stage_path_set_invalid")
    staged_status = require_git(root, "diff", "--cached", "--name-status").splitlines()
    if staged_status != [f"M\t{STATUS_PATH}"]:
        raise NoPublishFailure("status_stage_status_invalid")
    diff_check = git(root, "diff", "--cached", "--check")
    if diff_check.returncode != 0:
        raise NoPublishFailure("status_diff_check_failed")
    if require_git(root, "rev-parse", "HEAD").strip() != base_head:
        raise NoPublishFailure("head_changed_before_commit")

    commit_message = f"Record World Brief no-publish status for {run_date}"
    commit = git(root, "commit", "-m", commit_message, timeout=120)
    if commit.returncode != 0:
        raise NoPublishFailure("status_commit_failed")
    new_head = require_git(root, "rev-parse", "HEAD").strip()
    if require_git(root, "rev-parse", "HEAD^1").strip() != base_head:
        raise NoPublishFailure("status_commit_parent_invalid")
    commit_paths = require_git(root, "show", "--format=", "--name-only", "HEAD").splitlines()
    if commit_paths != [STATUS_PATH]:
        raise NoPublishFailure("status_commit_path_set_invalid")

    outgoing = require_git(root, "log", "origin/main..HEAD", "--oneline").splitlines()
    incoming = require_git(root, "log", "HEAD..origin/main", "--oneline").splitlines()
    if len(outgoing) != 1 or not outgoing[0].startswith(new_head[:7] + " ") or incoming:
        raise NoPublishFailure("remote_diff_preflight_failed")
    push = git(root, "push", "origin", "main", timeout=180)
    if push.returncode != 0:
        raise NoPublishFailure("status_push_failed")
    if require_git(root, "rev-parse", "origin/main").strip() != new_head:
        raise NoPublishFailure("status_push_tracking_ref_not_advanced")
    if require_git(root, "log", "origin/main..HEAD", "--oneline").strip():
        raise NoPublishFailure("status_push_outgoing_not_empty")
    if require_git(root, "log", "HEAD..origin/main", "--oneline").strip():
        raise NoPublishFailure("status_push_incoming_not_empty")
    return new_head


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    run_date = date.today().isoformat()
    if sys.argv[1:] != ["insufficient_verified_articles"]:
        print("NO_PUBLISH_STATUS=FAILED reason=no_publish_reason_invalid", file=sys.stderr)
        return 1
    try:
        sha = record_no_publish(root, run_date)
    except NoPublishFailure as exc:
        print(f"NO_PUBLISH_STATUS=FAILED reason={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("NO_PUBLISH_STATUS=FAILED reason=unexpected_error", file=sys.stderr)
        return 1
    if sha is None:
        print(f"NO_PUBLISH_STATUS=PASS state=already_current date={run_date}")
    else:
        print(f"NO_PUBLISH_STATUS=PASS commit={sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
