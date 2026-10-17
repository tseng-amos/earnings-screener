"""screen.py 單元測試：以合成資料驗證各階段判定。執行：python scripts/test_screen.py"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from screen import analyze, find_gap_day, market_regime, prepare

CFG = json.loads((Path(__file__).parent.parent / "config.json").read_text(encoding="utf-8"))


def build(post_bars, reaction_low=117):
    """前 80 日平盤（價 100、量 1M、振幅約 3%），第 81 日財報跳空 +20%、量 4.8 倍，再接 post_bars。"""
    rows = []
    for i in range(80):
        c = 100 + (i % 3) * 0.2
        rows.append((c, c * 1.015, c * 0.985, c, 1_000_000))
    rows.append((118, 122, reaction_low, 120, 4_800_000))  # 反應日
    rows += post_bars
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    return prepare(pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"]))


def calm(n, base=118, rng0=0.05):
    """整理 n 日：振幅逐日收斂、量縮。"""
    out = []
    for i in range(n):
        r = rng0 * (1 - i / (n + 2))
        c = base + (i % 2)
        out.append((c, c * (1 + r / 2), c * (1 - r / 2), c, 700_000))
    return out


def test_consolidating():
    d = build(calm(8))
    pos = find_gap_day(d, CFG)
    assert pos == 80
    r = analyze(d, pos, CFG)
    assert r["stage"] == "consolidating", r
    assert r["stop"] < r["pivot"]


def test_breakout():
    bars = calm(8) + [(120, 126, 119, 125, 2_000_000)]
    d = build(bars)
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stage"] == "breakout", r
    assert r["breakout_date"] == d.index[-1].strftime("%Y-%m-%d")


def test_removed():
    bars = calm(5) + [(110, 111, 105, 106, 900_000)]  # 收盤 106 < 反應日低點 117
    d = build(bars)
    assert analyze(d, find_gap_day(d, CFG), CFG)["stage"] == "removed"


def test_watch_too_short():
    d = build(calm(3))
    assert analyze(d, find_gap_day(d, CFG), CFG)["stage"] == "watch"


def test_distribution_fail():
    flat = [(111, 112, 110, 111, 700_000)] * 2
    bars = calm(3) + [(112, 113, 110, 111, 3_000_000)] + flat + [(107, 108, 105, 106, 3_000_000)]
    d = build(bars, reaction_low=100)
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert any(c["label"] == "無放量大陰線" and c["status"] == "fail" for c in r["checks"]), r


def test_regime():
    idx = pd.bdate_range("2026-01-01", periods=60)
    assert market_regime(pd.Series(np.linspace(100, 160, 60), index=idx))["status"] == "strong"
    assert market_regime(pd.Series(np.linspace(160, 100, 60), index=idx))["status"] == "weak"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
