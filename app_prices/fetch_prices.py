"""
株価アプリ用：Yahoo!ファイナンス(yfinance)から株価・決算日・指標を集めて JSON にします。
GitHub Actions で毎営業日に自動実行します（家のPCはYahoo!に止められることがあるため）。
出力: app_prices/prices.json
"""
import datetime as dt
import json
import os
import sys
import time

# 監視する銘柄コード（アプリの銘柄と同じにしてください。増やすときはここに追加）
CODES = ["8035", "6758", "7203", "9984", "6861", "8306", "9983", "4063", "6098", "4502",
         "7974", "8001", "6857", "285A", "9831", "4704", "2326"]
INDICES = {"nikkei": "^N225", "sp500": "^GSPC", "usdjpy": "JPY=X", "us10y": "^TNX", "vix": "^VIX"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.json")
JST = dt.timezone(dt.timedelta(hours=9))


def num(x):
    """数値ならfloat、NaNや空はNone"""
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def to_candles(df):
    """DataFrame(Open/High/Low/Close/Volume, 日付index) → ローソク足のリスト（欠損行は除く）"""
    out = {}
    for idx, row in df.iterrows():
        o, h, l, c = (num(row.get(k)) for k in ("Open", "High", "Low", "Close"))
        if None in (o, h, l, c):
            continue
        day = idx.strftime("%Y-%m-%d")
        out[day] = {"time": day, "open": o, "high": h, "low": l, "close": c, "volume": int(num(row.get("Volume")) or 0)}
    return [out[k] for k in sorted(out)]


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


def build_stock(df, info, cal, today):
    info = info or {}
    roe, dy = num(info.get("returnOnEquity")), num(info.get("trailingAnnualDividendYield"))
    return {
        "candles": to_candles(df),
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


def main():
    import yfinance as yf

    today = dt.datetime.now(JST).strftime("%Y-%m-%d")
    syms = [c + ".T" for c in CODES]
    data = yf.download(syms + list(INDICES.values()), period="1y", interval="1d", group_by="ticker",
                       auto_adjust=False, threads=False, progress=False)

    def frame(sym):
        try:
            df = data[sym].dropna(how="all")
            if len(df):
                return df
        except Exception:
            pass
        time.sleep(1)
        return yf.Ticker(sym).history(period="1y", interval="1d", auto_adjust=False)

    stocks = {}
    for code in CODES:
        sym = code + ".T"
        try:
            df = frame(sym)
            t = yf.Ticker(sym)
            try:
                info = t.info
            except Exception as e:
                print("info失敗", sym, e)
                info = {}
            try:
                cal = t.calendar
            except Exception as e:
                print("calendar失敗", sym, e)
                cal = {}
            s = build_stock(df, info, cal, today)
            if len(s["candles"]) >= 80:
                stocks[code] = s
                print("OK ", sym, len(s["candles"]), "本 決算:", s["earnings"])
            else:
                print("少なすぎ", sym, len(s["candles"]))
        except Exception as e:
            print("失敗", sym, e)
        time.sleep(1)  # 相手に負担をかけない

    indices = {}
    for key, sym in INDICES.items():
        try:
            v = index_value(frame(sym))
            if v:
                indices[key] = v
        except Exception as e:
            print("指数失敗", sym, e)

    if len(stocks) < len(CODES) / 2:
        print("取得できた銘柄が少なすぎるため、前回のファイルを残して終了します")
        sys.exit(1)

    out = {"generatedAt": dt.datetime.now(JST).isoformat(timespec="seconds"), "stocks": stocks, "indices": indices}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("書き出し:", OUT, len(stocks), "銘柄")


if __name__ == "__main__":
    main()
