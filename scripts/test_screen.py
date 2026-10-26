"""screen.py 單元測試：以合成資料驗證各階段判定。執行：python scripts/test_screen.py"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from screen import (analyze, analyze_breakout, chart_rows, ep_quality, evaluate_holding, find_gap_day, market_regime,
                    match_event, momentum_ranks, prepare, season_of, find_gap_days, outcome_since_breakout, chart_window, simulate_exit, simulate_split, backtest_record, signal_at_breakout)

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
    """影片 B 突破形態：120 日平盤 → 20 日從 100 漲到 200 → 平頂橫盤（高點壓在同一帶、低點墊高、振幅收窄、價格在 50 日線上）→ 今天。"""
    rows = [(100, 102, 98, 100, 1_000_000)] * 120
    rows += [(100 + (i + 1) * 5 - 2, (100 + (i + 1) * 5) * 1.04, (100 + (i + 1) * 5) * 0.96, 100 + (i + 1) * 5, 2_000_000)
             for i in range(20)]
    for i in range(cons_days):
        c = 178 + (3 if i % 2 else -3)   # 影片 B 特斯拉：高點一直壓在同一帶，低點墊高、振幅收窄
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
    assert r["stage"] == "breakout" and r["stop_3d"] == round(pivot - 4, 2), r   # 影片 B：突破當天最低點（保留為可選）
    r3 = analyze_breakout(d, {**CFG, "stop_method": "3d"}, RANK)
    assert r3["stop"] == r["stop_3d"] and r3["stop_method"] == "3d"


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
    # 影片做法（止損＝突破當天最低點，上限 1 倍 ADR）：止損太遠 → 不合格
    r = analyze_breakout(d, {**CFG, "stop_method": "3d", "bo_stop_max_adr_mult": 1.0}, RANK)
    assert r["risk_grade"] == "too_wide" and r["stage"] == "watch", r
    # 預設做法（前一個轉折低點）：突破當天盤中下殺不影響止損位置，仍是合格的突破
    assert analyze_breakout(d, CFG, RANK)["stage"] == "breakout"


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


def extend(d, bars):
    """在已 prepare 的日線後面接新的 K 線（開、高、低、收、量），回傳重新 prepare 的結果。"""
    raw = d[["Open", "High", "Low", "Close", "Volume"]]
    idx = pd.bdate_range(raw.index[-1] + pd.offsets.BDay(1), periods=len(bars))
    return prepare(pd.concat([raw, pd.DataFrame(bars, index=idx, columns=raw.columns)]))


def test_breakout_memory_keeps_original_pivot_and_stop():
    # 突破後隔天沒創新高：不可退回「待突破」，買點與止損要維持突破當天的值
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p + 2, 2_000_000))
    first = analyze_breakout(d, CFG, RANK)
    d2 = extend(d, [(pivot + 2, pivot + 2.5, pivot, pivot + 1, 1_000_000)])
    again = analyze_breakout(d2, CFG, RANK)
    assert again["stage"] == "breakout" and again["days_since_breakout"] == 1, again
    assert again["pivot"] == first["pivot"] and again["stop"] == first["stop"], (first, again)


def test_breakout_memory_expires():
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p + 2, 2_000_000))
    d2 = extend(d, [(pivot + 2, pivot + 2.5, pivot, pivot + 1, 1_000_000)] * CFG["breakout_fresh_bars"])
    assert analyze_breakout(d2, CFG, RANK)["stage"] != "breakout"


def test_breakout_close_confirmed_flag():
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p - 2, 2_000_000))   # 盤中越過、收盤回到買點下方
    r = analyze_breakout(d, CFG, RANK)
    assert r["stage"] == "breakout" and r["close_confirmed"] is False and r["holding"] is False, r
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p + 2, 2_000_000))
    assert analyze_breakout(d, CFG, RANK)["close_confirmed"] is True


def test_breakout_unknown_earnings_date_is_flagged_not_passed():
    d, _ = build_bo()
    r = analyze_breakout(d, CFG, RANK, None)
    assert any(c["label"] == "距下次財報" and c["status"] == "unknown" for c in r["checks"])


def test_pead_close_confirmed_flag():
    d = build(calm(8) + [(119, 126, 118, 119, 1_000_000)])        # 盤中越過、收盤回到區間內
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stage"] == "breakout" and r["close_confirmed"] is False and r["holding"] is False
    d = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["close_confirmed"] is True and r["days_since_breakout"] == 0


def test_match_event_uses_report_time():
    d = build(calm(3))
    pos = 80                                               # 反應日
    day = d.index[pos]
    prev = d.index[pos - 1]
    assert match_event(d, pos, [{"date": prev.strftime("%Y-%m-%d"), "time": "盤後"}]) is not None   # 前一天盤後 → 次日反應
    assert match_event(d, pos, [{"date": day.strftime("%Y-%m-%d"), "time": "盤前"}]) is not None    # 當天盤前
    assert match_event(d, pos, [{"date": prev.strftime("%Y-%m-%d"), "time": "盤前"}]) is None        # 前一天盤前：反應日應是前一天
    assert match_event(d, pos, [{"date": day.strftime("%Y-%m-%d"), "time": "盤後"}]) is None         # 當天盤後：反應日應是隔天
    assert match_event(d, pos, [{"date": prev.strftime("%Y-%m-%d"), "time": "時間未定"}]) is not None
    weekend = (d.index[pos - 5]).strftime("%Y-%m-%d")
    assert match_event(d, pos, [{"date": weekend, "time": "盤後"}]) is None                         # 太久以前


def test_ep_quality_growth_revenue_and_oneoff():
    d = build([])
    pos = find_gap_day(d, CFG)
    q = ep_quality(d, pos, {"eps_actual": 2.5, "eps_forecast": 2.0, "eps_last_year": 1.0, "rev_actual": 120e6, "rev_forecast": 100e6}, CFG)
    by = {c["label"]: c for c in q["ep_checks"]}
    assert by["盈利年增"]["status"] == "pass" and "三位數" in by["盈利年增"]["detail"]
    assert by["營收超預期"]["status"] == "pass" and q["ep_score"] == 5 and q["ep_unknown"] == 0
    q = ep_quality(d, pos, {"eps_actual": 9.11, "eps_forecast": 2.87, "eps_last_year": 2.0}, CFG)   # 影片 A 谷歌：超預期 217% 含帳面收益
    assert {c["label"]: c for c in q["ep_checks"]}["EPS 超預期"]["status"] == "warn"
    q = ep_quality(d, pos, {"eps_actual": 1.1, "eps_forecast": 1.0, "eps_last_year": 1.0}, CFG)
    assert {c["label"]: c for c in q["ep_checks"]}["盈利年增"]["status"] == "fail"


def test_chart_rows_shape():
    d = build(calm(8))
    rows = chart_rows(d, 74, 75)
    assert rows[0][0] == d.index[max(74, len(d) - 75)].strftime("%Y-%m-%d") and len(rows[0]) == 8 and len(rows) <= 75


def _hold(entry_date, **kw):
    return {"symbol": "X", "entry_date": entry_date, "entry_price": 100, "shares": 10, "stop": 95, "partial_done": "N", "exit_ma": 10, **kw}


def test_holding_rules():
    idx = pd.bdate_range("2026-01-01", periods=60)
    up = pd.DataFrame({"Open": np.linspace(100, 130, 60), "High": np.linspace(101, 131, 60), "Low": np.linspace(99, 129, 60),
                       "Close": np.linspace(100, 130, 60), "Volume": 1_000_000.0}, index=idx)
    d = prepare(up)
    entry = idx[-5].strftime("%Y-%m-%d")                       # 持有 5 天
    r = evaluate_holding(d, _hold(entry), CFG)
    assert r["held_days"] == 5 and r["status"] == "partial" and "賣出約一半" in r["actions"][0]["text"]
    assert evaluate_holding(d, _hold(entry, partial_done="Y", stop=100), CFG)["status"] == "hold"
    # 收盤跌破 10 日線 → 全賣
    broke = up.copy()
    broke.iloc[-1] = [120, 121, 118, 119, 2_000_000]
    r = evaluate_holding(prepare(broke), _hold(entry, partial_done="Y", stop=100), CFG)
    assert r["status"] == "sell" and "跌破 10 日線" in r["actions"][0]["text"]
    # 盤中跌破但收盤站上：只提醒
    intraday = up.copy()
    intraday.iloc[-1] = [129, 131, 120, 130.5, 1_000_000]
    assert evaluate_holding(prepare(intraday), _hold(entry, partial_done="Y", stop=100), CFG)["status"] == "watch"
    # 財報前：獲利不到 20% 先賣；超過 20% 續抱
    assert evaluate_holding(d, _hold(entry, partial_done="Y", stop=100, entry_price=125), CFG, days_to_earnings=1)["status"] == "sell"
    assert evaluate_holding(d, _hold(entry, partial_done="Y", stop=100, entry_price=100), CFG, days_to_earnings=1)["status"] == "watch"


def test_calm_check_is_tolerant_but_bounded():
    d = build(calm(8, rng0=0.09))          # 整理期振幅比財報前平時（約 3%）大
    pos = find_gap_day(d, CFG)
    def status(mult):
        r = analyze(d, pos, {**CFG, "calm_fail_adr_mult": mult})
        return next(c for c in r["checks"] if c["label"] == "波動小於平時")["status"]
    assert status(1.0) == "fail"           # 嚴格照字面：比 ADR 大就不合格
    assert status(10.0) == "warn"          # 容忍度放寬：只提醒
    assert status(CFG["calm_fail_adr_mult"]) in ("warn", "fail")


def test_stop_base_is_the_wider_option():
    d = build(calm(8))
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stop_base"] is not None and r["stop_base"] <= r["stop"]
    bo, _ = build_bo()
    b = analyze_breakout(bo, CFG, RANK)
    assert b["stop_base"] <= b["stop"]


def test_default_stop_is_previous_swing_low_after_the_peak():
    d, pivot = build_bo(lambda p: (p - 1, p + 3, p - 4, p + 2, 2_000_000))
    r = analyze_breakout(d, CFG, RANK)
    assert r["stop_method"] == "swing" and r["stop"] == r["stop_swing"], r
    assert r["stop_swing_date"] > r["peak_date"]                  # 轉折低點必須在高點之後的整理期
    assert r["stop"] < r["pivot"] and r["stop_base"] <= r["stop"]  # 比整理區最低點高、比買點低


def test_pead_swing_stop_and_fallback():
    d = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    r = analyze(d, find_gap_day(d, CFG), CFG)
    assert r["stop_3d"] is not None and r["stop_method"] in ("swing", "3d")
    if r["stop_method"] == "swing":
        assert r["stop"] == r["stop_swing"] and r["stop_swing_date"] > r["reaction_date"]
    old = analyze(d, find_gap_day(d, CFG), {**CFG, "stop_method": "3d"})
    assert old["stop"] == old["stop_3d"] == r["stop_3d"] and old["stop_method"] == "3d"


def test_season_of_groups_by_report_quarter():
    assert season_of("2026-01-15") == ("2026-S1", "2026 年 1～3 月財報季（上年度 Q4）")
    assert season_of("2026-04-30")[0] == "2026-S2" and season_of("2026-07-01")[0] == "2026-S3"
    assert season_of("2026-10-21") == ("2026-S4", "2026 年 10～12 月財報季（Q3）")
    assert season_of("2026-03-31")[0] == "2026-S1" and season_of("2026-12-31")[0] == "2026-S4"


def test_find_gap_days_finds_every_event_not_just_the_latest():
    d = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    d2 = extend(d, [(125, 126, 124, 125.5, 1_000_000)] * 5 + [(125.5, 150, 125, 148, 6_000_000)])    # 之後又一次大漲爆量
    days = find_gap_days(d2, CFG)
    assert 80 in days and len(d2) - 1 in days and find_gap_day(d2, CFG) == len(d2) - 1   # 單日版只會回報最近一次


def test_outcome_since_breakout_is_a_plain_record():
    d = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    d = extend(d, [(125, 140, 124, 130, 1_500_000), (130, 132, 126, 127, 1_000_000)])
    r = analyze(d, find_gap_day(d, CFG), CFG)
    o = outcome_since_breakout(d, r["breakout_date"], r["entry_price"])
    assert o["max_since_breakout_pct"] == round((140 / r["entry_price"] - 1) * 100, 1)
    assert o["since_breakout_pct"] == round((127 / r["entry_price"] - 1) * 100, 1)
    assert outcome_since_breakout(d, None, None) == {"max_since_breakout_pct": None, "since_breakout_pct": None}
    w = chart_window(d, 70, 90)
    assert w[0][0] == d.index[70].strftime("%Y-%m-%d") and len(w) == 20 and len(w[0]) == 8


def _bars(rows):
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"]).assign(Volume=1e6)


def test_exit_rules_stop_gap_and_entry_day():
    flat_bar = (100, 101, 99, 100)
    # 進場在第 0 根（買 100、止損 95）：之後盤中跌到 94 → 以止損價 95 出場
    d = _bars([flat_bar, (100, 101, 99, 100), (99, 100, 94, 96)])
    r = simulate_exit(d, 0, 100, 95, 20, 5)
    assert (r["reason"], r["exit_price"], r["days"]) == ("stop", 95, 2) and r["ret_pct"] == round((95 / 100 - 1 - 0.001) * 100, 2)
    assert r["r"] == round(r["ret_pct"] / 5, 2)
    # 跳空開在止損下方：只能以開盤價出場
    assert simulate_exit(_bars([flat_bar, (90, 91, 88, 89)]), 0, 100, 95, 20, 5)["exit_price"] == 90
    # 進場當天最低價就已經碰到止損：保守地算停損（日線看不出盤中先後）
    r = simulate_exit(_bars([(100, 103, 94, 102), (102, 104, 101, 103)]), 0, 100, 95, 20, 5)
    assert (r["reason"], r["days"], r["exit_price"]) == ("stop", 0, 95)


def test_exit_on_ma_is_next_open_and_20_exits_before_50():
    closes = [80 + i * 0.9 for i in range(60)] + [126, 121, 119, 118, 119, 122, 127, 131, 135]     # 長多後拉回（只破 20 日線、不碰 50 日線），再創高
    rows = [(c, c + 1, c - 1, c) for c in closes]
    d = _bars(rows)
    entry, stop = closes[50], 100.0                                                                # 止損放很遠，不會被掃到
    r20, r50, hold = (simulate_exit(d, 50, entry, stop, m, 0) for m in (20, 50, None))
    assert r20["reason"] == "ma" and r20["exit_date"] > d.index[50].strftime("%Y-%m-%d")
    i = d.index.get_loc(pd.Timestamp(r20["exit_date"]))
    assert r20["exit_price"] == round(float(d["Open"].iloc[i]), 2)                                  # 收盤跌破 20 日線 → 隔天開盤賣
    assert float(d["Close"].iloc[i - 1]) < float(d["Close"].rolling(20).mean().iloc[i - 1])
    assert r50["reason"] == "open" and hold["reason"] == "open"                                    # 拉回沒跌破 50 日線
    assert r50["exit_price"] == hold["exit_price"] == round(closes[-1], 2)                         # 未了結：以最後收盤估算
    assert simulate_exit(d, 50, entry, stop, 20, 0)["ret_pct"] != simulate_exit(d, 50, entry, stop, 50, 0)["ret_pct"]


def test_split_takes_half_at_20ma_and_half_at_50ma():
    closes = [80 + i * 0.9 for i in range(60)] + [126, 121, 119, 118, 119, 122, 127, 131, 135]     # 長多後拉回（跌破 20 日線、不碰 50 日線），再創高
    d = _bars([(c, c + 1, c - 1, c) for c in closes])
    entry, stop = closes[50], 100.0
    a, b = simulate_exit(d, 50, entry, stop, 20, 0), simulate_exit(d, 50, entry, stop, 50, 0)
    s = simulate_split(d, 50, entry, stop, 0, False)
    assert s["legs"] == [a, b] and s["reason"] == "mixed"                                           # 一批被 20 日線賣掉、一批續抱
    assert s["ret_pct"] == round((a["ret_pct"] + b["ret_pct"]) / 2, 2) and s["days"] == round((a["days"] + b["days"]) / 2, 1)
    # 第一批賣出後把止損移到成本：拉回時剩下那批被成本價掃出場，不會等到後面的新高
    be = simulate_split(d, 50, entry, stop, 0, True)
    assert be["legs"][0] == a and be["legs"][1]["reason"] == "stop" and be["ret_pct"] < s["ret_pct"]
    # 同樣的手續費：兩批各付自己那一半，等於單筆的手續費
    assert simulate_split(d, 50, entry, stop, 10, False)["ret_pct"] == round(s["ret_pct"] - 0.2, 2)
    # 兩批都被同一個止損掃掉
    crash = _bars([(100, 101, 99, 100)] * 3 + [(99, 100, 90, 92)])
    c = simulate_split(crash, 0, 100, 95, 0, True)
    assert c["reason"] == "stop" and c["ret_pct"] == -5.0 and c["r"] == -1.0


def test_signal_is_point_in_time_failed_breakouts_stay_in_the_sample():
    base = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    pos = find_gap_day(base, CFG)
    assert signal_at_breakout(base, pos, CFG) is not None
    ok = backtest_record(base, pos, CFG)
    assert ok and ok["entry"] > ok["stop"] and set(ok) >= {"split", "split_be", "ma20", "ma50", "hold", "entry_date", "risk_pct"}
    # 突破之後暴跌到反應日最低點之下：事後階段變成「失效」，但突破當下確實是訊號，必須留在回測樣本裡（否則樣本被挑過）
    crashed = extend(base, [(110, 111, 95, 96, 3_000_000)] * 3)
    assert analyze(crashed, pos, CFG)["stage"] == "removed"
    again = backtest_record(crashed, pos, CFG)
    assert again and (again["entry"], again["stop"], again["entry_date"]) == (ok["entry"], ok["stop"], ok["entry_date"])
    assert again["split"]["ret_pct"] < 0 and again["hold"]["reason"] == "stop"
    # 突破「之前」就已經跌破反應日最低點：沒有訊號
    early = build(calm(3) + [(110, 111, 95, 96, 900_000)] + calm(6) + [(120, 130, 119, 128, 2_000_000)], reaction_low=100)
    assert backtest_record(early, find_gap_day(early, CFG), CFG) is None
    # 整理天數不夠就突破：整理條件未過，沒有訊號
    short = build(calm(3) + [(120, 130, 119, 128, 2_000_000)])
    assert backtest_record(short, find_gap_day(short, CFG), CFG) is None


def test_regime():
    idx = pd.bdate_range("2026-01-01", periods=60)
    r = market_regime(pd.Series(np.linspace(100, 160, 60), index=idx))
    assert r["status"] == "strong" and r["ma10"] > r["ma20"] > 0 and r["ma10_up"] and r["ma20_up"] and r["ma10_above_ma20"]
    # 前高後回落、近 10 日反彈：10 日線向上且在 20 日線之上，但 20 日線仍未向上 → 小心，且要能指出是 20 日線
    rebound = np.concatenate([np.linspace(100, 140, 40), np.linspace(140, 110, 10), 110 + 3.0 * np.arange(1, 11)])
    r = market_regime(pd.Series(rebound, index=pd.bdate_range("2026-01-01", periods=60)))
    assert r["status"] == "caution" and r["ma10_above_ma20"] and r["ma10_up"] and not r["ma20_up"], r
    assert market_regime(pd.Series(np.linspace(160, 100, 60), index=idx))["status"] == "weak"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
