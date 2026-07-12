"""
build_site.py

GitHub Actions上で実行される軽量版。
fundamental_news_report.py が docs/reports に直接レポートを書き込んだ後、
このモジュールが index.html (レポート一覧) と manifest.json、アイコンを
docs フォルダの中に組み立てる。

git add / commit / push はこのモジュールでは行わない
(GitHub Actionsのワークフロー側 .github/workflows/daily_report.yml が行う)。

PC上での手動運用時は publish_to_web.py(git pushまで行う版)を使うため、
このファイルはActions専用。
"""

import os
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(SCRIPT_DIR, "docs")
REPORTS_DIR = os.path.join(DOCS_DIR, "reports")

SITE_TITLE = "AI株式レポート"


def write_manifest():
    manifest = f"""{{
  "name": "{SITE_TITLE}",
  "short_name": "{SITE_TITLE}",
  "start_url": "./index.html",
  "display": "standalone",
  "background_color": "#F4F5F7",
  "theme_color": "#0C447C",
  "icons": [
    {{ "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png" }},
    {{ "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png" }}
  ]
}}
"""
    with open(os.path.join(DOCS_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(manifest)


def ensure_icons():
    """リポジトリ直下に置いたアイコンを docs/icons にコピーする(なければ)。"""
    import shutil
    icons_dir = os.path.join(DOCS_DIR, "icons")
    os.makedirs(icons_dir, exist_ok=True)
    for name in ("icon-192.png", "icon-512.png"):
        dest = os.path.join(icons_dir, name)
        if not os.path.exists(dest):
            src = os.path.join(SCRIPT_DIR, name)
            if os.path.exists(src):
                shutil.copy2(src, dest)


def build_index():
    files = sorted(
        glob.glob(os.path.join(REPORTS_DIR, "*_fundamental_news.html")),
        reverse=True,
    )

    rows = []
    for path in files:
        filename = os.path.basename(path)
        date_str = filename.split("_")[0]
        rows.append(
            f"<a class='card' href='reports/{filename}'>"
            f"<span class='date'>{date_str}</span>"
            f"<span class='arrow'>&rsaquo;</span>"
            f"</a>"
        )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{SITE_TITLE}</title>

<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="icons/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0C447C">

<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
    background: #F4F5F7;
    margin: 0;
    padding: 24px 16px 48px;
    color: #222;
  }}
  h1 {{ font-size: 20px; margin: 8px 4px 20px; }}
  .card {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    background: #fff;
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 10px;
    text-decoration: none;
    color: #222;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  }}
  .date {{ font-size: 16px; font-weight: 600; }}
  .arrow {{ color: #999; font-size: 20px; }}
  .empty {{ color: #888; padding: 24px 4px; }}
</style>
</head>
<body>
  <h1>{SITE_TITLE}</h1>
  {"".join(rows) if rows else "<p class='empty'>まだレポートがありません。</p>"}
</body>
</html>
"""
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def build():
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ensure_icons()
    write_manifest()
    build_index()


if __name__ == "__main__":
    build()
