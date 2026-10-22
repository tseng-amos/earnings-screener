"""build_screen 整條管線（離線、合成資料）：財報後線與動能突破線各挑出對的標的。執行：python scripts/test_pipeline.py"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

import sys
import tempfile

import fetch_data
from fetch_data import (annotate_changes, build_history, build_screen, load_holdings, merge_history, season_index, union_history)
from test_screen import build, build_bo, calm

CFG = json.loads((Path(__file__).parent.parent / "config.json").read_text(encoding="utf-8"))


def flat(n=200):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": 50.0, "High": 51.0, "Low": 49.0, "Close": 50.0, "Volume": 1_000_000}, index=idx)


def test_build_screen_picks_each_line():
    pead = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    bo, _ = build_bo()
    prices = {"PEAD1": pead, "BO1": bo, **{f"F{i}": flat() for i in range(50)}}
    before_reaction = pead.index[79].strftime("%Y-%m-%d")
    events = [{"symbol": "PEAD1", "name": "Pead Inc", "date": before_reaction, "time": "盤後", "eps_actual": 1.2, "eps_forecast": 1.0},
              {"symbol": "BO1", "name": "Bo Inc", "date": "2026-12-31"}]
    out = build_screen(prices, events, {"status": "strong"}, CFG, date(2026, 11, 20))
    assert [c["symbol"] for c in out["pead"]] == ["PEAD1"]
    assert out["pead"][0]["stage"] == "breakout" and out["pead"][0]["ep_score"] == 3
    assert [c["symbol"] for c in out["breakout"]] == ["BO1"] and out["breakout"][0]["stage"] == "setup"
    assert out["breakout"][0]["next_earnings"] == "2026-12-31" and out["universe_size"] == 52


def test_earnings_within_three_days_blocks_breakout():
    bo, _ = build_bo()
    prices = {"BO1": bo, **{f"F{i}": flat() for i in range(50)}}
    out = build_screen(prices, [{"symbol": "BO1", "name": "", "date": "2026-11-22"}], {}, CFG, date(2026, 11, 20))
    assert out["breakout"] == []


def _screen_inputs():
    pead = build(calm(8) + [(120, 126, 119, 125, 2_000_000)])
    bo, _ = build_bo()
    prices = {"PEAD1": pead, "BO1": bo, **{f"F{i}": flat() for i in range(50)}}
    events = [{"symbol": "PEAD1", "name": "Pead Inc", "date": pead.index[79].strftime("%Y-%m-%d"), "time": "盤後", "eps_actual": 1.2, "eps_forecast": 1.0},
              {"symbol": "BO1", "name": "Bo Inc", "date": "2026-12-31"}]
    return prices, events


def test_charts_and_holdings_are_attached():
    prices, events = _screen_inputs()
    entry = prices["PEAD1"].index[-5].strftime("%Y-%m-%d")
    holdings = [{"symbol": "PEAD1", "entry_date": entry, "entry_price": "120", "stop": "115", "partial_done": "N", "exit_ma": "10"},
                {"symbol": "NOPE", "entry_date": entry, "entry_price": "10"}]
    out = build_screen(prices, events, {"status": "strong"}, CFG, date(2026, 11, 20), holdings)
    assert len(out["pead"][0]["chart"][0]) == 7 and out["pead"][0]["chart"][0][0] <= out["pead"][0]["reaction_date"]
    assert out["breakout"][0]["chart"][0][0] <= out["breakout"][0]["peak_date"]
    assert out["holdings"][0]["symbol"] == "PEAD1" and out["holdings"][0]["held_days"] == 5 and "chart" in out["holdings"][0]
    assert out["holdings"][1]["status"] == "nodata"
    assert out["summary"]["pead"] == {"breakout": 1} and out["summary"]["breakout"] == {"setup": 1}


def test_annotate_changes():
    prices, events = _screen_inputs()
    today = date(2026, 11, 20)
    prev = build_screen(prices, events, {}, CFG, today)
    annotate_changes(prev, None)
    assert prev["summary"]["has_prev"] is False and prev["dropped"] == []
    now = build_screen(prices, events, {}, CFG, today)
    annotate_changes(now, prev)
    assert now["summary"]["new"] == 0 and now["summary"]["advanced"] == 0 and now["dropped"] == []
    older = json.loads(json.dumps(prev))
    older["pead"][0]["stage"] = "consolidating"                                   # 昨天還在整理
    older["breakout"] = []                                                       # 昨天動能線沒有它
    older["pead"].append({"symbol": "GONE", "reaction_date": "2026-01-01", "stage": "watch"})
    now = build_screen(prices, events, {}, CFG, today)
    annotate_changes(now, older)
    assert now["pead"][0]["change"] == "stage" and now["pead"][0]["prev_stage"] == "consolidating"
    assert now["breakout"][0]["change"] == "new" and now["summary"]["new"] == 1 and now["summary"]["advanced"] == 1
    assert now["dropped"] == [{"lane": "pead", "symbol": "GONE", "prev_stage": "watch"}]


def test_load_holdings():
    path = Path(tempfile.mkdtemp()) / "holdings.csv"
    path.write_text("\ufeffsymbol,entry_date,entry_price,shares,stop,partial_done,exit_ma,note\n"
                    "brk.b,2026-10-01,450,10,430,N,20,測試\n,2026-10-01,10,1,9,N,10,沒代號\nXYZ,2026-10-01,abc,1,9,N,10,價格錯\n", encoding="utf-8")
    rows = load_holdings(path)
    assert [r["symbol"] for r in rows] == ["BRK-B"] and rows[0]["note"] == "測試"
    assert load_holdings(path.with_name("nope.csv")) == []


def _run_main(tmp, events, prices, argv_extra=(), universe=None):
    import sources
    out = tmp / "out"
    sources.fetch_events = lambda *a, **k: (events, [])
    sources.enrich_with_finnhub = lambda *a, **k: 0
    sources.fetch_universe = lambda *a, **k: [{"symbol": s} for s in (universe if universe is not None else prices)]
    sources.fetch_prices = lambda targets, min_bars, cache=None, **k: ({t: prices[t] for t in targets if t in prices}, {t: "yahoo" for t in targets if t in prices})
    sources.fetch_regime_series = lambda idx: (pd.Series(range(100, 160), index=pd.bdate_range("2026-06-01", periods=60)), idx)
    fetch_data.load_holdings = lambda path: []
    sys.argv = ["fetch_data", "--out", str(out), *argv_extra]
    fetch_data.main()
    return json.loads((out / "screen.json").read_text(encoding="utf-8"))


def test_main_end_to_end_and_failure_guards():
    prices, events = _screen_inputs()
    tmp = Path(tempfile.mkdtemp())
    first = _run_main(tmp, events, prices)
    assert first["asof"] == max(f.index[-1] for f in prices.values()).strftime("%Y-%m-%d") and first["health"]["price_ok"] == 52
    assert first["regime"]["status"] == "strong" and first["summary"]["has_prev"] is False
    (tmp / "prev.json").write_text(json.dumps(first), encoding="utf-8")
    second = _run_main(tmp, events, prices, ["--prev", str(tmp / "prev.json")])
    assert second["summary"]["has_prev"] is True and second["summary"]["new"] == 0
    for bad_events, bad_prices in (([], prices), (events, {})):                   # 日曆全失敗／價格全失敗：必須中止，不發布空資料
        try:
            _run_main(tmp, bad_events, bad_prices)
            raise AssertionError("應該中止")
        except SystemExit as exc:
            assert exc.code == 1
    few = {k: v for k, v in list(prices.items())[:20]}                              # 成功率不足 80%
    try:
        _run_main(tmp, events, few, universe=list(prices))
        raise AssertionError("成功率不足時應該中止")
    except SystemExit as exc:
        assert exc.code == 1


def test_report_time_is_inferred_from_reaction_day():
    rt = fetch_data.report_time
    assert rt({"date": "2026-10-01", "time": "盤前"}, "2026-10-02") == "盤前"                # 已知的不改
    assert rt({"date": "2026-10-01", "time": "時間未定"}, "2026-10-01") == "盤前（推定）"      # 當天就反應
    assert rt({"date": "2026-10-01", "time": "時間未定"}, "2026-10-02") == "盤後（推定）"      # 隔天才反應
    prices, events = _screen_inputs()
    events[0]["time"] = "時間未定"
    out = build_screen(prices, events, {"status": "strong"}, CFG, date(2026, 11, 20))
    assert out["pead"][0]["earnings_time"] == "盤後（推定）"


def _history_inputs():
    prices, events = _screen_inputs()
    return prices, events


def test_history_records_season_outcome_and_carry_over():
    prices, events = _history_inputs()
    recs = build_history(prices, events, CFG)
    assert len(recs) == 1 and recs[0]["symbol"] == "PEAD1" and recs[0]["season"].startswith("2026-S") and recs[0]["stage"] == "breakout"
    assert recs[0]["max_since_breakout_pct"] is not None and recs[0]["chart"][0][0] <= recs[0]["reaction_date"]
    today = date(2026, 11, 20)
    merged = merge_history([], recs, today)
    assert merged[0]["first_seen"] == "2026-11-20"
    # 隔天重跑：同一筆被更新，但保留第一次看到的日期；這次沒算到的舊紀錄原樣留著
    old = {**recs[0], "symbol": "OLD", "reaction_date": "2025-11-05", "season": "2025-S4", "stage": "removed", "updated": "2025-12-01", "first_seen": "2025-11-06"}
    again = merge_history(merge_history([old], recs, today), recs, date(2026, 11, 21))
    by = {r["symbol"]: r for r in again}
    assert by["PEAD1"]["first_seen"] == "2026-11-20" and by["PEAD1"]["updated"] == "2026-11-21"
    assert by["OLD"]["stage"] == "removed" and by["OLD"]["updated"] == "2025-12-01"
    # 兩份來源（上次部署／快取）取聯集，同一筆以較新的為準
    a, b = [{**old, "stage": "watch", "updated": "2026-01-01"}], [{**old, "updated": "2026-02-01"}, {**old, "symbol": "ONLYB"}]
    u = {r["symbol"]: r for r in union_history(a, b)}
    assert u["OLD"]["stage"] == "removed" and "ONLYB" in u
    idx = season_index(again)
    assert [s["id"] for s in idx] == sorted({r["season"] for r in again}, reverse=True) and idx[0]["count"] >= 1
    assert idx[0]["label"].endswith("）") and idx[0]["stages"]


def test_main_writes_history_and_keeps_old_seasons():
    prices, events = _screen_inputs()
    tmp = Path(tempfile.mkdtemp())
    old = {"symbol": "OLD", "name": "Old Co", "reaction_date": "2025-08-05", "season": "2025-S3", "earnings_date": "2025-08-04", "stage": "removed",
           "gain_pct": 12.0, "updated": "2025-09-01", "first_seen": "2025-08-06"}
    (tmp / "hist_prev.json").write_text(json.dumps({"records": [old]}), encoding="utf-8")
    first = _run_main(tmp, events, prices, ["--history-prev", str(tmp / "hist_prev.json"), "--cache", str(tmp / "cache")])
    hist = json.loads((tmp / "out" / "history.json").read_text(encoding="utf-8"))["records"]
    assert {r["symbol"] for r in hist} == {"OLD", "PEAD1"} and (tmp / "cache" / "history.json").exists()
    assert first["seasons"]["carried_over"] == 1 and {s["id"] for s in first["seasons"]["list"]} >= {"2025-S3"}
    # 上次部署的檔案遺失時，由快取接力，舊季度不會消失
    again = _run_main(tmp, events, prices, ["--cache", str(tmp / "cache")])
    assert any(s["id"] == "2025-S3" for s in again["seasons"]["list"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
