"""build_screen 整條管線（離線、合成資料）：財報後線與動能突破線各挑出對的標的。執行：python scripts/test_pipeline.py"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

from fetch_data import build_screen
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
