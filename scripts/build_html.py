from pathlib import Path
import html
import markdown
import shutil

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

# すべてのMarkdownから日付付きHTMLを生成
for brief in briefs:
    name = brief.stem

    body = markdown.markdown(
        brief.read_text(encoding="utf-8")
    )

    page = render_page(f"World Brief - {name}", body)

    output = ROOT / f"{name}.html"
    output.write_text(page, encoding="utf-8")

    print(f"Created {output.name}")

# 最新記事を latest.html にコピー
latest_dated_html = ROOT / f"{briefs[0].stem}.html"
shutil.copy2(latest_dated_html, ROOT / "latest.html")

print("Updated latest.html")

# アーカイブ一覧を生成
archive_items = "\n".join(
    f'<li><a href="{brief.stem}.html">{html.escape(brief.stem)}</a></li>'
    for brief in briefs
)

archive_body = f"""
<h1>World Brief Archive</h1>

<p><a href="index.html">ホームへ戻る</a></p>

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