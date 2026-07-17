from pathlib import Path
import html
import markdown
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
BRIEFS = ROOT / "briefs"

briefs = sorted(BRIEFS.glob("*.md"), reverse=True)

if not briefs:
    raise SystemExit("No Markdown files found in briefs/")


def render_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<article>
{body}
</article>
</body>
</html>
"""


# すべての日付付きHTMLを生成
for brief in briefs:
    name = brief.stem

    body = markdown.markdown(
        brief.read_text(encoding="utf-8")
    )

    page = render_page(
        f"World Brief - {name}",
        body
    )

    output = ROOT / f"{name}.html"
    output.write_text(page, encoding="utf-8")

    print(f"Created {output.name}")


# 最新記事
latest_brief = briefs[0]
latest_name = latest_brief.stem

latest_body = markdown.markdown(
    latest_brief.read_text(encoding="utf-8")
)

latest_page = render_page(
    f"World Brief - {latest_name}",
    latest_body
)

(ROOT / "latest.html").write_text(
    latest_page,
    encoding="utf-8"
)

print("Updated latest.html")


# トップページに最新記事を直接表示
updated_at = datetime.fromtimestamp(
    latest_brief.stat().st_mtime
).strftime("%Y-%m-%d %H:%M")
index_body = f"""
<header>
<h1>World Brief</h1>
<p>毎日自動生成される世界情勢ブリーフィングです。</p>
<p><small>最終更新: {updated_at}</small></p>

<nav>
<a href="archive.html">過去のブリーフ一覧</a>
</nav>
</header>

<hr>

{latest_body}
"""

index_page = render_page(
    "World Brief",
    index_body
)

(ROOT / "index.html").write_text(
    index_page,
    encoding="utf-8"
)

print("Updated index.html")


# アーカイブ一覧
archive_items = "\n".join(
    f'<li><a href="{brief.stem}.html">{html.escape(brief.stem)}</a></li>'
    for brief in briefs
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

archive_page = render_page(
    "World Brief Archive",
    archive_body
)

(ROOT / "archive.html").write_text(
    archive_page,
    encoding="utf-8"
)

print("Created archive.html")