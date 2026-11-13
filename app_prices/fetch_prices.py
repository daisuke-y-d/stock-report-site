"""
株価アプリ用：Yahoo!ファイナンス(yfinance)から、日経225＋監視銘柄の株価・決算日・指標を集めて JSON にします。
GitHub Actions で毎営業日に自動実行します（家のPCはYahoo!に止められることがあるため）。
入力: nikkei225_tickers.csv（リポジトリ直下。列: code,name,sector）
出力: app_prices/prices.json（コンパクト形式 v2）
"""
import csv
import datetime as dt
import json
import os
import sys
import time

# 必ず取得する監視銘柄（日経225に無い銘柄もここに入れる）
WATCH = ["8035", "6758", "7203", "9984", "6861", "8306", "9983", "4063", "6098", "4502",
         "7974", "8001", "6857", "285A", "9831", "4704", "2326"]
# 日経225のCSVに無い監視銘柄の名前・業種
EXTRA_NAMES = {"9831": ("ヤマダホールディングス", "小売業"), "4704": ("トレンドマイクロ", "情報・通信"),
               "2326": ("デジタルアーツ", "情報・通信")}
INDICES = {"nikkei": "^N225", "sp500": "^GSPC", "usdjpy": "JPY=X", "us10y": "^TNX", "vix": "^VIX"}

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "..", "nikkei225_tickers.csv")
OUT = os.path.join(HERE, "prices.json")
JST = dt.timezone(dt.timedelta(hours=9))
CHUNK = 50                 # 一度に取る銘柄数
ENRICH_BUDGET_SEC = 25 * 60  # 決算日・指標の取得に使う最大時間
MAX_CONSECUTIVE_FAIL = 8     # 続けて失敗したら（制限の可能性）残りは諦める


def num(x):
    """数値ならfloat、NaNや空はNone"""
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def load_universe(csv_path=CSV_PATH):
    """日経225のCSV＋監視銘柄 → (名前・業種の辞書, 処理順のコード一覧。監視銘柄が先頭)"""
    uni = {}
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                code = (r.get("code") or "").strip().replace(".T", "")
                if code:
                    uni[code] = ((r.get("name") or code).strip(), (r.get("sector") or "").strip())
    except FileNotFoundError:
        print("nikkei225_tickers.csv が見つかりません。監視銘柄だけで続けます")
    for c in WATCH:
        uni.setdefault(c, EXTRA_NAMES.get(c, (c, "")))
    order = list(WATCH) + [c for c in uni if c not in WATCH]
    return uni, order


def to_arrays(df, dates):
    """DataFrame → 日付ごとの数値配列（欠損日はNone）。ファイルを小さくするため配列で持つ"""
    by = {idx.strftime("%Y-%m-%d"): row for idx, row in df.iterrows()}
    o, h, l, c, v = [], [], [], [], []
    for d in dates:
        row = by.get(d)
        vals = [num(row.get(k)) for k in ("Open", "High", "Low", "Close")] if row is not None else [None] * 4
        if None in vals:
            for arr in (o, h, l, c, v):
                arr.append(None)
            continue
        o.append(round(vals[0], 2)); h.append(round(vals[1], 2)); l.append(round(vals[2], 2)); c.append(round(vals[3], 2))
        v.append(int(num(row.get("Volume")) or 0))
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def valid_days(arrs):
    return sum(1 for x in arrs["c"] if x is not None)


def pick_earnings(cal, today):
    """calendar から、今日以降でいちばん近い決算日(YYYY-MM-DD)を返す"""
    dates = []
    if isinstance(cal, dict):
        dates = cal.get("Earnings Date") or []
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    ok = []
    for d in dates:
        try:
            s = d.strftime("%Y-%m-%d")
        except AttributeError:
            s = str(d)[:10]
        if s >= today:
            ok.append(s)
    return min(ok) if ok else None


def build_meta(info, cal, today):
    info = info or {}
    roe, dy = num(info.get("returnOnEquity")), num(info.get("trailingAnnualDividendYield"))
    return {
        "earnings": pick_earnings(cal, today),
        "per": num(info.get("trailingPE")),
        "pbr": num(info.get("priceToBook")),
        "roe": roe * 100 if roe is not None else None,
        "dy": dy * 100 if dy is not None else None,
    }


def index_value(df):
    c = [num(x) for x in df["Close"].tolist() if num(x) is not None]
    if not c:
        return None
    return {"value": c[-1], "change": ((c[-1] - c[-2]) / c[-2] * 100) if len(c) >= 2 and c[-2] else 0.0}


def download_frames(yf, syms):
    """銘柄ごとのDataFrameを返す。一度に取りすぎないよう分けて取得"""
    frames = {}
    for i in range(0, len(syms), CHUNK):
        chunk = syms[i:i + CHUNK]
        try:
            d = yf.download(chunk, period="9mo", interval="1d", group_by="ticker", auto_adjust=False, threads=False, progress=False)
        except Exception as e:
            print("まとめ取得に失敗", chunk[:2], "…", e)
            continue
        for s in chunk:
            try:
                df = (d[s] if len(chunk) > 1 else d).dropna(how="all")
                if len(df):
                    frames[s] = df
            except Exception:
                pass
        time.sleep(2)
    return frames


def main():
    import yfinance as yf

    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    uni, order = load_universe()
    syms = [c + ".T" for c in order]
    frames = download_frames(yf, syms + list(INDICES.values()))

    # 日付の一覧（日本株が1銘柄でもある日）
    days = sorted({idx.strftime("%Y-%m-%d") for s in syms if s in frames for idx in frames[s].index})
    stocks = {}
    for code in order:
        df = frames.get(code + ".T")
        if df is None:
            continue
        arrs = to_arrays(df, days)
        if valid_days(arrs) >= 80:
            stocks[code] = {"n": uni[code][0], "s": uni[code][1], **arrs}
    print("株価を取得できた銘柄:", len(stocks), "/", len(order))

    # 決算日・指標（時間がかかるので、監視銘柄から先に。制限されたら諦める）
    start, fails = time.time(), 0
    for code in [c for c in order if c in stocks]:
        if time.time() - start > ENRICH_BUDGET_SEC or fails >= MAX_CONSECUTIVE_FAIL:
            print("決算日・指標の取得を打ち切りました（時間切れ、または連続失敗）")
            break
        t = yf.Ticker(code + ".T")
        try:
            info = t.info
            cal = t.calendar
            stocks[code].update(build_meta(info, cal, today))
            fails = 0
        except Exception as e:
            fails += 1
            print("指標失敗", code, str(e)[:60])
        time.sleep(0.6)

    indices = {}
    for key, sym in INDICES.items():
        try:
            v = index_value(frames[sym]) if sym in frames else None
            if v:
                indices[key] = v
        except Exception as e:
            print("指数失敗", sym, e)

    if len(stocks) < len(order) / 2:
        print("取得できた銘柄が少なすぎるため、前回のファイルを残して終了します")
        sys.exit(1)

    out = {"version": 2, "generatedAt": dt.datetime.now(JST).isoformat(timespec="seconds"),
           "dates": days, "stocks": stocks, "indices": indices}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("書き出し:", OUT, len(stocks), "銘柄", round(os.path.getsize(OUT) / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
