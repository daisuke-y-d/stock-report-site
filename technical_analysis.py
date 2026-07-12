"""
technical_analysis.py

日経225の構成銘柄について、株価データ(yfinance / Yahoo Finance経由、無料)を取得し、
移動平均線・MACD・ボリンジャーバンド・RSIを計算してシグナルを検出するモジュール。

Finnhubの無料プランは日本株(東証)の株価データに対応していないため、
株価取得には yfinance を使用しています。
(ニュース取得は引き続き fundamental_news_report.py 側で Finnhub を使用)

事前準備:
    pip install yfinance pandas numpy

銘柄リスト:
    nikkei225_tickers.csv (code, name, sector 列を持つCSV) を同じフォルダに置いてください。
    日経225の構成銘柄は年に数回見直されるため、このCSVは
    https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225
    を参照して定期的に更新することをおすすめします。
"""

import os
import csv
import time
import datetime
import pandas as pd
import numpy as np

try:
    import yfinance as yf
except ImportError:
    yf = None

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TICKERS_CSV = os.path.join(SCRIPT_DIR, "nikkei225_tickers.csv")

# 株価取得期間(移動平均75日線などを安定して計算するため、少し長めに取る)
PRICE_PERIOD = "9mo"
PRICE_INTERVAL = "1d"

# 移動平均線の期間
MA_SHORT = 5
MA_MID = 25
MA_LONG = 75

# MACDのパラメータ
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ボリンジャーバンドのパラメータ
BB_PERIOD = 20
BB_STD = 2

# RSIのパラメータ
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# 一度に yfinance へ渡す銘柄数(あまり多すぎると失敗しやすくなるため分割する)
BATCH_SIZE = 50


# ------------------------------------------------------------
# 銘柄リストの読み込み
# ------------------------------------------------------------

def load_tickers(csv_path: str = TICKERS_CSV):
    """CSVから銘柄コード・銘柄名を読み込み、yfinance用のティッカー(.T付き)を付与する。"""
    tickers = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["code"].strip()
            tickers.append({
                "code": code,
                "name": row["name"].strip(),
                "sector": row.get("sector", "").strip(),
                "yf_ticker": f"{code}.T",
            })
    return tickers


# ------------------------------------------------------------
# 株価取得
# ------------------------------------------------------------

def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def fetch_price_history(tickers):
    """
    複数銘柄の株価をまとめて取得する。
    戻り値: { yf_ticker: DataFrame(Open/High/Low/Close/Volume) }
    取得に失敗した銘柄は結果に含めない。
    """
    if yf is None:
        raise RuntimeError(
            "yfinance がインストールされていません。 pip install yfinance を実行してください。"
        )

    all_data = {}
    symbols = [t["yf_ticker"] for t in tickers]

    for batch in _chunked(symbols, BATCH_SIZE):
        try:
            df = yf.download(
                batch,
                period=PRICE_PERIOD,
                interval=PRICE_INTERVAL,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            print(f"  警告: バッチ取得に失敗しました ({e})")
            continue

        # 銘柄が1つだけの場合、yfinanceはgroup_byの階層を作らないことがあるため吸収する
        if len(batch) == 1:
            symbol = batch[0]
            if not df.empty:
                all_data[symbol] = df
            continue

        for symbol in batch:
            try:
                sub = df[symbol].dropna(how="all")
                if not sub.empty:
                    all_data[symbol] = sub
            except (KeyError, Exception):
                continue

        # Yahoo Finance側への負荷を抑えるための小休止
        time.sleep(0.5)

    return all_data


# ------------------------------------------------------------
# 指標計算
# ------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """終値ベースで移動平均・MACD・ボリンジャーバンド・RSIを計算して列を追加する。"""
    out = df.copy()
    close = out["Close"]

    # 移動平均線
    out[f"MA{MA_SHORT}"] = close.rolling(MA_SHORT).mean()
    out[f"MA{MA_MID}"] = close.rolling(MA_MID).mean()
    out[f"MA{MA_LONG}"] = close.rolling(MA_LONG).mean()

    # MACD
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    out["MACD"] = ema_fast - ema_slow
    out["MACD_signal"] = out["MACD"].ewm(span=MACD_SIGNAL, adjust=False).mean()

    # ボリンジャーバンド
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std()
    out["BB_mid"] = mid
    out["BB_upper"] = mid + BB_STD * std
    out["BB_lower"] = mid - BB_STD * std

    # RSI (Wilderのスムージング)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["RSI"] = 100 - (100 / (1 + rs))
    out["RSI"] = out["RSI"].fillna(50)

    return out


# ------------------------------------------------------------
# シグナル判定
# ------------------------------------------------------------

def detect_signals(code: str, name: str, sector: str, df: pd.DataFrame):
    """
    直近2営業日分のデータからシグナルを検出する。
    戻り値: シグナルのdictのリスト。無ければ空リスト。
    """
    signals = []
    if len(df) < max(MA_LONG, BB_PERIOD, MACD_SLOW) + 2:
        # データが短すぎて指標が安定しない銘柄はスキップ
        return signals

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    latest_date = df.index[-1].strftime("%Y-%m-%d")

    def add(signal_type, detail):
        signals.append({
            "code": code,
            "name": name,
            "sector": sector,
            "type": signal_type,
            "detail": detail,
            "date": latest_date,
        })

    # ゴールデンクロス / デッドクロス (短期線と中期線)
    if pd.notna(prev[f"MA{MA_SHORT}"]) and pd.notna(prev[f"MA{MA_MID}"]):
        was_below = prev[f"MA{MA_SHORT}"] < prev[f"MA{MA_MID}"]
        is_above = latest[f"MA{MA_SHORT}"] >= latest[f"MA{MA_MID}"]
        was_above = prev[f"MA{MA_SHORT}"] > prev[f"MA{MA_MID}"]
        is_below = latest[f"MA{MA_SHORT}"] <= latest[f"MA{MA_MID}"]

        if was_below and is_above:
            add("ゴールデンクロス", f"{MA_SHORT}日線が{MA_MID}日線を上抜け")
        elif was_above and is_below:
            add("デッドクロス", f"{MA_SHORT}日線が{MA_MID}日線を下抜け")

    # MACDクロス
    if pd.notna(prev["MACD"]) and pd.notna(prev["MACD_signal"]):
        was_below = prev["MACD"] < prev["MACD_signal"]
        is_above = latest["MACD"] >= latest["MACD_signal"]
        was_above = prev["MACD"] > prev["MACD_signal"]
        is_below = latest["MACD"] <= latest["MACD_signal"]

        if was_below and is_above:
            add("MACD買いシグナル", "MACDがシグナルラインを上抜け")
        elif was_above and is_below:
            add("MACD売りシグナル", "MACDがシグナルラインを下抜け")

    # RSI
    if pd.notna(latest["RSI"]):
        if latest["RSI"] >= RSI_OVERBOUGHT:
            add("RSI買われすぎ", f"RSI = {latest['RSI']:.1f}")
        elif latest["RSI"] <= RSI_OVERSOLD:
            add("RSI売られすぎ", f"RSI = {latest['RSI']:.1f}")

    # ボリンジャーバンド(±2σタッチ)
    if pd.notna(latest["BB_upper"]) and pd.notna(latest["BB_lower"]):
        if latest["Close"] >= latest["BB_upper"]:
            add("ボリンジャーバンド+2σタッチ", "上限バンドに到達(過熱感に注意)")
        elif latest["Close"] <= latest["BB_lower"]:
            add("ボリンジャーバンド-2σタッチ", "下限バンドに到達(反発の可能性)")

    return signals


# ------------------------------------------------------------
# スキャン実行(全銘柄をまとめて処理)
# ------------------------------------------------------------

def run_scan(csv_path: str = TICKERS_CSV):
    """
    日経225全銘柄をスキャンし、シグナルが出ている銘柄のリストを返す。
    戻り値: { "ゴールデンクロス": [signal, ...], "RSI買われすぎ": [...], ... }
    """
    tickers = load_tickers(csv_path)
    print(f"株価データを取得しています... ({len(tickers)}銘柄)")
    price_data = fetch_price_history(tickers)
    print(f"  取得成功: {len(price_data)}/{len(tickers)}銘柄")

    grouped = {}
    for t in tickers:
        df = price_data.get(t["yf_ticker"])
        if df is None or df.empty:
            continue
        indicators = compute_indicators(df)
        signals = detect_signals(t["code"], t["name"], t["sector"], indicators)
        for s in signals:
            grouped.setdefault(s["type"], []).append(s)

    return grouped


# ------------------------------------------------------------
# レポート出力用の整形
# ------------------------------------------------------------

SIGNAL_ORDER = [
    "ゴールデンクロス",
    "デッドクロス",
    "MACD買いシグナル",
    "MACD売りシグナル",
    "RSI買われすぎ",
    "RSI売られすぎ",
    "ボリンジャーバンド+2σタッチ",
    "ボリンジャーバンド-2σタッチ",
]

SIGNAL_NOTE = {
    "ゴールデンクロス": "短期的な上昇トレンドへの転換シグナルとされます。",
    "デッドクロス": "短期的な下降トレンドへの転換シグナルとされます。",
    "MACD買いシグナル": "トレンドの勢いが上向きに転じた可能性を示します。",
    "MACD売りシグナル": "トレンドの勢いが下向きに転じた可能性を示します。",
    "RSI買われすぎ": "短期的な過熱感があり、反落に注意が必要とされる水準です。",
    "RSI売られすぎ": "短期的な売られすぎで、反発に注意が必要とされる水準です。",
    "ボリンジャーバンド+2σタッチ": "統計的にはやや行き過ぎた価格帯とされます。",
    "ボリンジャーバンド-2σタッチ": "統計的にはやや売られ過ぎた価格帯とされます。",
}


def build_technical_markdown(grouped, report_date):
    lines = [f"# テクニカル分析シグナル (日経225 / {report_date})", ""]
    lines.append(
        "※ 株価データは Yahoo Finance(yfinance)経由で取得しています。"
        "テクニカル指標は過去データに基づく機械的な計算であり、将来の株価を保証するものではありません。"
    )
    lines.append("")
    for signal_type in SIGNAL_ORDER:
        items = grouped.get(signal_type, [])
        lines.append(f"## {signal_type} ({len(items)}銘柄)")
        lines.append("")
        note = SIGNAL_NOTE.get(signal_type)
        if note:
            lines.append(f"_{note}_")
            lines.append("")
        if not items:
            lines.append("該当銘柄なし")
            lines.append("")
            continue
        for s in items:
            lines.append(f"- **{s['name']}** ({s['code']}) - {s['sector']}")
            lines.append(f"  - {s['detail']} / {s['date']}")
        lines.append("")
    return "\n".join(lines)


def build_technical_html(grouped, report_date):
    signal_colors = {
        "ゴールデンクロス": "#1B7A3D",
        "デッドクロス": "#B03A2E",
        "MACD買いシグナル": "#1B7A3D",
        "MACD売りシグナル": "#B03A2E",
        "RSI買われすぎ": "#B0730E",
        "RSI売られすぎ": "#0C5FA8",
        "ボリンジャーバンド+2σタッチ": "#B0730E",
        "ボリンジャーバンド-2σタッチ": "#0C5FA8",
    }
    html = [f"<h1>テクニカル分析シグナル (日経225 / {report_date})</h1>"]
    html.append(
        "<p style='font-size:12px;color:#888;'>"
        "※ 株価データは Yahoo Finance(yfinance)経由で取得しています。"
        "テクニカル指標は過去データに基づく機械的な計算であり、将来の株価を保証するものではありません。"
        "</p>"
    )
    for signal_type in SIGNAL_ORDER:
        items = grouped.get(signal_type, [])
        color = signal_colors.get(signal_type, "#5F5E5A")
        html.append(f"<h2>{signal_type} ({len(items)}銘柄)</h2>")
        note = SIGNAL_NOTE.get(signal_type)
        if note:
            html.append(f"<p style='font-size:13px;color:#666;'>{note}</p>")
        if not items:
            html.append("<p>該当銘柄なし</p>")
            continue
        for s in items:
            html.append("<div class='card'>")
            html.append(f"<span class='tag' style='background:{color}'>{signal_type}</span>")
            html.append(f"<div><strong>{s['name']}</strong> ({s['code']}) - {s['sector']}</div>")
            html.append(f"<div class='meta'>{s['detail']} / {s['date']}</div>")
            html.append("</div>")
    return "\n".join(html)


if __name__ == "__main__":
    # このモジュール単体でも動作確認できるようにしておく
    report_date = datetime.date.today().isoformat()
    result = run_scan()
    md = build_technical_markdown(result, report_date)
    print(md)
