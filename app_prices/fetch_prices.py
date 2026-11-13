import datetime as dt
import math
import os
import tempfile
import pandas as pd
import fetch_prices as fp

idx = pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"])
df = pd.DataFrame({"Open": [10, 11, float("nan"), 13], "High": [12, 12, 14, 15], "Low": [9, 10, 12, 12],
                   "Close": [11, 12, 13, 14], "Volume": [100, 200, 300, float("nan")]}, index=idx)
days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
a = fp.to_arrays(df, days)
assert a["c"] == [None, 11.0, 12.0, None, 14.0], a                 # 欠損日・データの無い日はNone
assert a["v"][1] == 100 and a["v"][4] == 0 and a["o"][0] is None
assert fp.valid_days(a) == 3

cal = {"Earnings Date": [dt.date(2026, 10, 1), dt.date(2026, 12, 5), dt.date(2026, 11, 20)]}
assert fp.pick_earnings(cal, "2026-11-13") == "2026-11-20"
assert fp.pick_earnings({}, "2026-11-13") is None and fp.pick_earnings(None, "2026-11-13") is None
m = fp.build_meta({"trailingPE": 21.5, "priceToBook": 2.2, "returnOnEquity": 0.123, "trailingAnnualDividendYield": float("nan")}, cal, "2026-11-13")
assert m["per"] == 21.5 and math.isclose(m["roe"], 12.3) and m["dy"] is None and m["earnings"] == "2026-11-20"
v = fp.index_value(pd.DataFrame({"Close": [100.0, 110.0]}))
assert v["value"] == 110.0 and math.isclose(v["change"], 10.0)

# 日経225のCSV読み込み（BOM付き・.T付き・監視銘柄の追加と並び順）
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "n.csv")
    with open(p, "w", encoding="utf-8-sig") as f:
        f.write("code,name,sector\n4151,協和キリン,医薬品\n7203.T,トヨタ自動車,自動車\n285A,キオクシア,電気機器\n")
    uni, order = fp.load_universe(p)
    assert uni["7203"] == ("トヨタ自動車", "自動車") and uni["4151"][0] == "協和キリン"
    assert uni["9831"][0] == "ヤマダホールディングス"          # CSVに無い監視銘柄も入る
    assert order[: len(fp.WATCH)] == fp.WATCH and "4151" in order  # 監視銘柄が先頭
    assert len(order) == len(set(order))
uni2, order2 = fp.load_universe("/no/such/file.csv")
assert set(order2) == set(fp.WATCH)                            # CSVが無くても動く
print("PY OK")
