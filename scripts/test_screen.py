"""screen.py 單元測試：以合成資料驗證各階段判定。執行：python scripts/test_screen.py"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from screen import analyze, analyze_breakout, ep_quality, find_gap_day, market_regime, momentum_ranks, prepare

CFG = json.loads((Path(__file__).parent.parent / "config.json").read_text(encoding="utf-8"))


def build(post_bars, reaction_low=117, reaction_high=122):
    """前 80 日平盤（價 100、量 1M、振幅約 3%），第 81 日財報跳空 +20%、量 4.8 倍，再接 post_bars。"""
    rows = []
    for i in range(80):
        c = 100 + (i % 3) * 0.2
        rows.append((c, c * 1.015, c * 0.985, c, 1_000_000))
    rows.append((118, reaction_high, reaction_low, 120, 4_800_000))  # 反應日
    rows += post_bars
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    return prepare(pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"]))


def calm(n, base=118, rng0=0.04):
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


def test_intraday_cross_is_breakout():
    # 影片：價格一越過整理區高點就買。盤中最高價越過、收盤回到區間內，仍算突破
    d = build(calm(8) + [(119, 126, 118, 119, 1_000_000)])
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stage"] == "breakout", r
    assert r["entry_price"] == r["pivot"]  # 開盤在區間內：以買點價成交


def test_gap_above_pivot_enters_at_open():
    # 跳空越過買點：以開盤價成交（不是買點價）
    d = build(calm(8) + [(130, 131, 128, 129, 1_000_000)])
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stage"] == "breakout" and r["entry_price"] == 130, r


def test_not_crossing_is_not_breakout():
    d = build(calm(8) + [(119, 121, 118, 119, 1_000_000)])
    assert analyze(d, find_gap_day(d, CFG), CFG)["stage"] != "breakout"


def test_wide_range_fails_quieter_than_usual():
    # 整理期每天振幅都大於財報前的平時波動：「波動小於平時」不合格
    wide = [(118, 124, 112, 118 + (i % 2), 700_000) for i in range(8)]
    d = build(wide)
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert any(c["label"] == "波動小於平時" and c["status"] == "fail" for c in r["checks"]), r


def test_risk_grade():
    from screen import risk_grade_of
    assert risk_grade_of(5.0, 5.0, CFG) == "ideal"
    assert risk_grade_of(5.7, 5.2, CFG) == "acceptable"   # 影片戴爾：風險 5.7% 比 ADR 稍大、仍可接受
    assert risk_grade_of(8.0, 5.0, CFG) == "too_wide"


def test_reaction_day_range_not_counted_in_pullback():
    # 反應日自身盤中區間很大（150→105），後續回落僅約 23%，應合格
    d = build(calm(8), reaction_low=105, reaction_high=150)
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["max_pullback_pct"] <= 25 and r["stage"] == "consolidating", r


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


def build_bo(today=None, cons_days=24):
    """影片 B 突破形態：120 日平盤 → 20 日從 100 漲到 200 → 橫盤（低點墊高、振幅收窄、價格在 50 日線上）→ 今天。"""
    rows = [(100, 102, 98, 100, 1_000_000)] * 120
    rows += [(100 + (i + 1) * 5 - 2, (100 + (i + 1) * 5) * 1.04, (100 + (i + 1) * 5) * 0.96, 100 + (i + 1) * 5, 2_000_000)
             for i in range(20)]
    for i in range(cons_days):
        c = 175 + 10 * i / cons_days + (3 if i % 2 else -3)
        rng = 0.10 * (1 - i / (cons_days * 1.6))
        rows.append((c, c * (1 + rng / 2), c * (1 - rng / 2), c, 1_000_000))
    pivot = max(r[1] for r in rows[140:])
    rows.append(today(pivot) if today else (188, pivot - 1, 184, 189, 1_000_000))
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    return prepare(pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])), pivot


RANK = {"momentum_rank": 99.0, "rank_1m": 80.0, "rank_3m": 99.0, "rank_6m": 99.0}


def test_breakout_setup_before_cross():
    d, pivot = build_bo()
    r = analyze_breakout(d, CFG, RANK)
    assert r["stage"] == "setup", r
    assert r["pivot"] == round(pivot, 2) and r["stop"] < r["pivot"] and r["dist_to_pivot_pct"] > 0


def test_breakout_cross_uses_day_low_as_stop():
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p + 2, 2_000_000))
    r = analyze_breakout(d, CFG, RANK)
    assert r["stage"] == "breakout" and r["stop"] == round(pivot - 4, 2), r


def test_breakout_needs_top_momentum_and_liquidity():
    d, _ = build_bo()
    assert analyze_breakout(d, CFG, {**RANK, "momentum_rank": 90.0})["stage"] == "watch"
    assert analyze_breakout(d, {**CFG, "min_dollar_volume_usd": 1e12}, RANK)["stage"] == "watch"


def test_breakout_blocked_near_earnings():
    d, _ = build_bo()
    r = analyze_breakout(d, CFG, RANK, days_to_earnings=2)
    assert r["stage"] == "watch" and any(c["label"] == "距下次財報" and c["status"] == "fail" for c in r["checks"])
    assert analyze_breakout(d, CFG, RANK, days_to_earnings=20)["stage"] == "setup"


def test_breakout_too_short_consolidation_is_pending():
    d, _ = build_bo(cons_days=6)
    r = analyze_breakout(d, CFG, RANK)
    assert r["stage"] == "watch" and any(c["status"] == "pending" for c in r["checks"])


def test_breakout_wide_stop_is_skipped():
    d, _ = build_bo(lambda p: (p - 1, p + 3, p - 40, p + 2, 2_000_000))   # 當天最低點離買點遠超過 ADR
    r = analyze_breakout(d, CFG, RANK)
    assert r["risk_grade"] == "too_wide" and r["stage"] == "watch", r


def test_momentum_ranks():
    idx = pd.bdate_range("2026-01-01", periods=200)
    frames = {f"S{i}": pd.DataFrame({"Close": np.linspace(100, 100 + i, 200)}, index=idx) for i in range(1, 101)}
    r = momentum_ranks(frames, CFG)
    assert r["S100"]["momentum_rank"] == 100.0 and r["S1"]["momentum_rank"] == 1.0


def test_ep_quality():
    d = build([])
    q = ep_quality(d, find_gap_day(d, CFG), {"eps_actual": 1.2, "eps_forecast": 1.0}, CFG)
    assert q["gap_open_pct"] == 17.8 and q["ep_score"] == 3, q
    q2 = ep_quality(d, find_gap_day(d, CFG), {}, CFG)
    assert q2["ep_score"] == 2 and q2["ep_checks"][1]["status"] == "unknown"


def test_regime():
    idx = pd.bdate_range("2026-01-01", periods=60)
    assert market_regime(pd.Series(np.linspace(100, 160, 60), index=idx))["status"] == "strong"
    assert market_regime(pd.Series(np.linspace(160, 100, 60), index=idx))["status"] == "weak"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
