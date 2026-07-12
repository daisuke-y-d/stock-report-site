"""
fundamental_news_report.py

Finnhub の無料APIを使って世界の企業ファンダメンタルニュース
(決算・M&A・マクロ経済)を取得し、Markdown / HTML レポートとして
ローカルに保存するスクリプト。

事前準備:
    1. https://finnhub.io で無料アカウントを作成し、APIキーを取得する
    2. 環境変数 FINNHUB_API_KEY にキーを設定する
       (Mac/Linux) export FINNHUB_API_KEY="あなたのキー"
       (Windows PowerShell) $Env:FINNHUB_API_KEY = "あなたのキー"
    3. pip install -r requirements.txt

実行:
    python fundamental_news_report.py

毎日自動実行したい場合は末尾の「自動実行の設定方法」を参照。
"""

import os
import sys
import time
import datetime
import requests
from deep_translator import GoogleTranslator

import technical_analysis

try:
    import publish_to_web
except ImportError:
    publish_to_web = None

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

API_KEY = os.environ.get("FINNHUB_API_KEY")
BASE_URL = "https://finnhub.io/api/v1"

# GitHub Actions上で実行されているかどうか(Actionsが自動的にセットする環境変数)
RUNNING_IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# レポートの保存先
if RUNNING_IN_GITHUB_ACTIONS:
    # Actions上では、このスクリプト自体がstock-report-siteリポジトリ直下にあり、
    # docs フォルダがそのままGitHub Pagesの公開対象になる
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "reports")
else:
    # PC上ではこれまで通り、スクリプトと同じ場所に reports フォルダを作る
    OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")

# 1回のレポートに含める記事数の上限(カテゴリごと)
MAX_ARTICLES_PER_CATEGORY = 15

# 見出し・要約を日本語に自動翻訳するかどうか
# (無料の翻訳サービスを使うため、件数が多いと少し時間がかかります)
TRANSLATE_TO_JAPANESE = True

# 日経225のテクニカル指標スキャンをレポートに含めるかどうか
# (yfinanceで225銘柄分の株価を取得するため、数分程度かかることがあります)
INCLUDE_TECHNICAL_ANALYSIS = True

# レポートをGitHub Pages経由でスマホからも見られるように自動公開するかどうか
# (publish_to_web.py 側の REPO_DIR 設定が必要です。設定前は False のままにしてください)
PUBLISH_TO_WEB = False

_translator = GoogleTranslator(source="auto", target="ja")


def translate_text(text: str) -> str:
    """英語などのテキストを日本語に翻訳する。失敗したら元の文章をそのまま返す。"""
    if not TRANSLATE_TO_JAPANESE or not text:
        return text
    try:
        translated = _translator.translate(text)
        # 無料サービスへの負荷を抑えるため、少し間隔をあける
        time.sleep(0.3)
        return translated or text
    except Exception:
        # 翻訳に失敗しても処理を止めず、元の文章のまま続行する
        return text

# ニュース本文をカテゴリ分けするためのキーワード
# (英語ニュースが中心なので英単語ベース。必要に応じて調整してください)
CATEGORY_KEYWORDS = {
    "決算": [
        "earnings", "quarterly results", "q1", "q2", "q3", "q4",
        "revenue", "profit", "eps", "guidance", "beat estimates",
        "misses estimates", "fiscal year", "net income",
    ],
    "M&A": [
        "acquisition", "acquire", "merger", "merge", "takeover",
        "buyout", "deal", "stake", "combine with",
    ],
    "マクロ経済": [
        "interest rate", "central bank", "federal reserve", "fed ",
        "inflation", "cpi", "gdp", "unemployment", "tariff",
        "currency", "yen", "dollar", "ecb", "boj", "rate hike",
        "rate cut", "recession",
    ],
}


def classify(headline: str, summary: str) -> str:
    """記事の見出しと要約からカテゴリを推定する(単純なキーワード一致)。"""
    text = f"{headline} {summary}".lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return category
    return "その他"


def fetch_general_news():
    """全般の市場ニュースを取得する。"""
    resp = requests.get(
        f"{BASE_URL}/news",
        params={"category": "general", "token": API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_merger_news():
    """M&A専用カテゴリのニュースを取得する。"""
    resp = requests.get(
        f"{BASE_URL}/news",
        params={"category": "merger", "token": API_KEY},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def collect_articles():
    """複数カテゴリのニュースを取得し、重複を除いて1つのリストにまとめる。"""
    if not API_KEY:
        print("エラー: 環境変数 FINNHUB_API_KEY が設定されていません。")
        sys.exit(1)

    raw_articles = []
    raw_articles += fetch_general_news()
    raw_articles += fetch_merger_news()

    seen_ids = set()
    articles = []
    for a in raw_articles:
        article_id = a.get("id") or a.get("url")
        if article_id in seen_ids:
            continue
        seen_ids.add(article_id)

        headline = a.get("headline", "").strip()
        summary = a.get("summary", "").strip()
        if not headline:
            continue

        articles.append({
            "headline": headline,
            "summary": summary,
            "source": a.get("source", "不明"),
            "url": a.get("url", ""),
            "datetime": a.get("datetime", 0),
            "category": classify(headline, summary),
        })

    articles.sort(key=lambda x: x["datetime"], reverse=True)
    return articles


def group_by_category(articles):
    grouped = {"決算": [], "M&A": [], "マクロ経済": [], "その他": []}
    for a in articles:
        grouped[a["category"]].append(a)
    for cat in grouped:
        grouped[cat] = grouped[cat][:MAX_ARTICLES_PER_CATEGORY]
    return grouped


def translate_grouped(grouped):
    """レポートに実際に載る記事だけを日本語に翻訳する(件数を絞ってから翻訳することで無駄なAPI呼び出しを減らす)。"""
    if not TRANSLATE_TO_JAPANESE:
        return
    total = sum(len(v) for v in grouped.values())
    done = 0
    for items in grouped.values():
        for a in items:
            a["headline"] = translate_text(a["headline"])
            if a["summary"]:
                a["summary"] = translate_text(a["summary"])
            done += 1
            print(f"  翻訳中... {done}/{total}", end="\r")
    print()


def format_datetime(unix_ts):
    if not unix_ts:
        return ""
    return datetime.datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d %H:%M")


def build_markdown(grouped, report_date):
    lines = [f"# ファンダメンタルニュース レポート ({report_date})", ""]
    for category in ["決算", "M&A", "マクロ経済", "その他"]:
        items = grouped[category]
        lines.append(f"## {category} ({len(items)}件)")
        lines.append("")
        if not items:
            lines.append("該当ニュースなし")
            lines.append("")
            continue
        for a in items:
            lines.append(f"- **{a['headline']}**")
            if a["summary"]:
                lines.append(f"  - {a['summary']}")
            lines.append(f"  - 出典: {a['source']} / {format_datetime(a['datetime'])} / [記事リンク]({a['url']})")
        lines.append("")
    return "\n".join(lines)


def build_html(grouped, report_date):
    category_colors = {
        "決算": "#0C447C",
        "M&A": "#3C3489",
        "マクロ経済": "#854F0B",
        "その他": "#5F5E5A",
    }
    html = [
        "<!DOCTYPE html>",
        "<html lang='ja'><head><meta charset='utf-8'>",
        f"<title>ファンダメンタルニュース レポート {report_date}</title>",
        "<style>",
        "body{font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 16px;color:#222;}",
        "h1{font-size:22px;} h2{font-size:18px;margin-top:32px;}",
        ".card{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin-bottom:10px;}",
        ".tag{display:inline-block;font-size:12px;color:#fff;padding:2px 8px;border-radius:4px;margin-bottom:6px;}",
        ".meta{font-size:12px;color:#888;margin-top:6px;}",
        ".summary{font-size:14px;color:#444;margin:4px 0 0;}",
        "a{color:#185FA5;text-decoration:none;}",
        "</style></head><body>",
        f"<h1>ファンダメンタルニュース レポート ({report_date})</h1>",
    ]
    for category in ["決算", "M&A", "マクロ経済", "その他"]:
        items = grouped[category]
        color = category_colors[category]
        html.append(f"<h2>{category} ({len(items)}件)</h2>")
        if not items:
            html.append("<p>該当ニュースなし</p>")
            continue
        for a in items:
            html.append("<div class='card'>")
            html.append(f"<span class='tag' style='background:{color}'>{category}</span>")
            html.append(f"<div><strong>{a['headline']}</strong></div>")
            if a["summary"]:
                html.append(f"<div class='summary'>{a['summary']}</div>")
            html.append(
                f"<div class='meta'>{a['source']} / {format_datetime(a['datetime'])} "
                f"/ <a href='{a['url']}' target='_blank'>記事リンク</a></div>"
            )
            html.append("</div>")
    html.append("</body></html>")
    return "\n".join(html)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report_date = datetime.date.today().isoformat()

    print("ニュースを取得しています...")
    articles = collect_articles()
    grouped = group_by_category(articles)

    if TRANSLATE_TO_JAPANESE:
        print("見出し・要約を日本語に翻訳しています...")
        translate_grouped(grouped)

    md_content = build_markdown(grouped, report_date)
    html_content = build_html(grouped, report_date)

    technical_signals = None
    if INCLUDE_TECHNICAL_ANALYSIS:
        print("日経225のテクニカル指標をスキャンしています...")
        try:
            technical_signals = technical_analysis.run_scan()
            md_content += "\n\n---\n\n" + technical_analysis.build_technical_markdown(
                technical_signals, report_date
            )
            html_content = html_content.replace(
                "</body></html>",
                "<hr>" + technical_analysis.build_technical_html(technical_signals, report_date)
                + "</body></html>",
            )
        except Exception as e:
            print(f"  警告: テクニカル分析のスキャンに失敗しました ({e})")
            print("  ニュースレポートのみ出力します。")

    md_path = os.path.join(OUTPUT_DIR, f"{report_date}_fundamental_news.md")
    html_path = os.path.join(OUTPUT_DIR, f"{report_date}_fundamental_news.html")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    total = sum(len(v) for v in grouped.values())
    print(f"完了: {total}件のニュースを分類しました。")
    if technical_signals is not None:
        total_signals = sum(len(v) for v in technical_signals.values())
        print(f"      テクニカルシグナル {total_signals}件を検出しました。")
    print(f"  - {md_path}")
    print(f"  - {html_path}")

    if RUNNING_IN_GITHUB_ACTIONS:
        # Actions上では git push はワークフロー側(YAMLファイル)が行うので、
        # ここでは index.html / manifest.json / icons を組み立てるだけでよい
        print("スマホ用サイトのページを組み立てています...")
        try:
            import build_site
            build_site.build()
        except Exception as e:
            print(f"  警告: サイトの組み立てに失敗しました ({e})")
    elif PUBLISH_TO_WEB and publish_to_web is not None:
        print("スマホ用サイトに公開しています...")
        publish_to_web.publish(
            source_reports_dir=OUTPUT_DIR,
            icons_source_dir=os.path.dirname(os.path.abspath(__file__)),
        )


if __name__ == "__main__":
    main()

# ------------------------------------------------------------
# 自動実行の設定方法
# ------------------------------------------------------------
#
# [Mac/Linux] cron で毎朝7時に実行する例:
#   crontab -e
#   0 7 * * * /usr/bin/python3 /path/to/fundamental_news_report.py >> /path/to/log.txt 2>&1
#
# [Windows] タスクスケジューラで新しいタスクを作成し、
#   プログラム: python.exe
#   引数: C:\path\to\fundamental_news_report.py
#   トリガー: 毎日 指定時刻
#
# 環境変数 FINNHUB_API_KEY は、cron/タスクスケジューラの実行環境にも
# 設定しておく必要があります(ターミナルで export しただけでは
# 別プロセスから見えないことがあります)。
