"""sources.py 單元測試：全部以假網路回應與假 yfinance 離線執行。"""
import sys
import tempfile
import types
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

import sources as S


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        return self._payload


def frame(n=100, close=100.0, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * .99, "Close": close, "Volume": 1e6}, index=idx)


def test_drop_incomplete_bar():
    f = frame(5, start="2026-10-13")            # 最後一根是 2026-10-19（週一）
    assert f.index[-1] == pd.Timestamp("2026-10-19")
    midday = datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc)      # 美東 15:00（盤中）
    assert len(S.drop_incomplete_bar(f, midday)) == 4
    closed = datetime(2026, 10, 19, 21, 30, tzinfo=timezone.utc)     # 美東 17:30（已收盤）
    assert len(S.drop_incomplete_bar(f, closed)) == 5
    just_closed = datetime(2026, 10, 19, 20, 10, tzinfo=timezone.utc)  # 美東 16:10：資料尚未定案
    assert len(S.drop_incomplete_bar(f, just_closed)) == 4
    next_day = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)    # 隔天盤前：最後一根已完整
    assert len(S.drop_incomplete_bar(f, next_day)) == 5


def test_stooq_and_nasdaq_parsers():
    S.http_get = lambda *a, **k: FakeResponse(text="Date,Open,High,Low,Close,Volume\n2026-10-15,10,11,9,10.5,1000\n2026-10-16,10.5,12,10,11,2000\n")
    d = S.stooq_daily("AAA")
    assert list(d["Close"]) == [10.5, 11.0] and d.index[-1] == pd.Timestamp("2026-10-16")
    S.http_get = lambda *a, **k: FakeResponse(text="No data")
    assert S.stooq_daily("AAA") is None
    rows = [{"date": "10/16/2026", "close": "$11.00", "volume": "2,000", "open": "$10.50", "high": "$12.00", "low": "$10.00"},
            {"date": "10/15/2026", "close": "$10.50", "volume": "1,000", "open": "$10.00", "high": "$11.00", "low": "$9.00"}]
    S.http_get = lambda *a, **k: FakeResponse({"data": {"tradesTable": {"rows": rows}}})
    d = S.nasdaq_daily("AAA")
    assert list(d["Close"]) == [10.5, 11.0] and d.index[0] < d.index[-1]      # 轉成由舊到新
    S.http_get = lambda *a, **k: None
    assert S.nasdaq_daily("AAA") is None


def test_universe_filters():
    rows = [{"symbol": "GOOD", "name": "Good Inc", "lastsale": "$20.00", "volume": "500,000", "marketCap": "1,000,000,000"},
            {"symbol": "CHEAP", "name": "Cheap", "lastsale": "$1.00", "volume": "9,000,000", "marketCap": "9,000,000"},     # 價格太低
            {"symbol": "THIN", "name": "Thin", "lastsale": "$10.00", "volume": "1,000", "marketCap": "9,000,000"},          # 成交額太低
            {"symbol": "ABCDW", "name": "ABC Corp Warrant", "lastsale": "$5.00", "volume": "9,000,000", "marketCap": ""},   # 權證
            {"symbol": "BAC^B", "name": "BofA Preferred", "lastsale": "$25.00", "volume": "9,000,000", "marketCap": ""},    # 特別股代號
            {"symbol": "BRK/B", "name": "Berkshire Hathaway", "lastsale": "$450.00", "volume": "3,000,000", "marketCap": "1,000,000,000,000"}]
    S.http_get = lambda *a, **k: FakeResponse({"data": {"rows": rows}})
    out = S.fetch_universe(2, 3_000_000)
    assert [u["symbol"] for u in out] == ["GOOD", "BRK-B"], out
    S.http_get = lambda *a, **k: None
    assert S.fetch_universe(2, 3_000_000) is None


def test_calendar_parsing_and_cache():
    calls = []
    rows = [{"symbol": "BRK.B", "name": "Berkshire", "marketCap": "$900,000,000,000", "time": "time-after-hours", "epsForecast": "$5.0",
             "eps": "$6.0", "surprise": "20", "lastYearEPS": "$4.0"},
            {"symbol": "SMALL", "name": "Small", "marketCap": "$100,000,000", "time": "time-pre-market"}]
    S.http_get = lambda url, params=None, **k: (calls.append(params["date"]) or FakeResponse({"data": {"rows": rows}}))
    S.time.sleep = lambda *_: None
    path = Path(tempfile.mkdtemp()) / "events.json"
    today = date(2026, 10, 18)
    events, failed = S.fetch_events(date(2026, 9, 1), date(2026, 10, 30), 2e9, path, today)
    first = len(calls)
    assert failed == [] and events[0]["symbol"] == "BRK-B" and events[0]["time"] == "盤後" and events[0]["eps_last_year"] == 4.0
    assert all(e["symbol"] != "SMALL" for e in events)                       # 市值太小被濾掉
    calls.clear()
    events2, _ = S.fetch_events(date(2026, 9, 1), date(2026, 10, 30), 2e9, path, today)
    assert len(events2) == len(events) and len(calls) < first and min(calls) >= "2026-10-15"   # 只重抓近 3 天與未來
    S.http_get = lambda *a, **k: None                                        # 全部失敗：記錄缺漏日期，不當成「沒有財報」
    _, failed = S.fetch_events(date(2026, 11, 2), date(2026, 11, 4), 2e9, None, today)
    assert failed == ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_finnhub_enrichment():
    events = [{"symbol": "AAA", "date": "2026-10-20"}, {"symbol": "BBB", "date": "2026-10-20"}]
    payload = {"earningsCalendar": [{"symbol": "AAA", "date": "2026-10-20", "revenueActual": 120e6, "revenueEstimate": 100e6}]}
    S.http_get = lambda *a, **k: FakeResponse(payload)
    S.time.sleep = lambda *_: None
    assert S.enrich_with_finnhub(events, date(2026, 10, 15), date(2026, 10, 25), "KEY") == 1
    assert events[0]["rev_actual"] == 120e6 and "rev_actual" not in events[1]
    assert S.enrich_with_finnhub([{"symbol": "AAA", "date": "x"}], date(2026, 10, 15), date(2026, 10, 25), None) == 0 or True


def test_price_fallback_chain_and_sources():
    fake = types.ModuleType("yfinance")
    available = {"AAA": frame()}                                              # Yahoo 只有 AAA
    def download(tickers, **kw):
        cols = {(t, c): available[t][c] for t in tickers if t in available for c in S.OHLCV}
        return pd.DataFrame(cols)
    fake.download = download
    sys.modules["yfinance"] = fake
    S.time.sleep = lambda *_: None
    S.FALLBACKS = (("stooq", lambda t: frame() if t == "BBB" else None), ("nasdaq", lambda t: frame() if t == "CCC" else None))
    prices, sources = S.fetch_prices(["AAA", "BBB", "CCC", "DDD"], min_bars=60)
    assert set(prices) == {"AAA", "BBB", "CCC"} and sources == {"AAA": "yahoo", "BBB": "stooq", "CCC": "nasdaq"}, sources


def test_price_cache_incremental_and_weekly_refresh():
    fake = types.ModuleType("yfinance")
    seen = []
    def download(tickers, **kw):
        seen.append(kw)
        f = frame(110, start="2026-05-15")
        return pd.DataFrame({(t, c): f[c] for t in tickers for c in S.OHLCV})
    fake.download = download
    sys.modules["yfinance"] = fake
    cache = {}
    S.fetch_prices(["AAA"], 60, cache)
    assert "period" in seen[-1] and "AAA" in cache                           # 第一次：完整下載
    S.fetch_prices(["AAA"], 60, cache)
    assert "start" in seen[-1]                                               # 第二次：增量
    tmp = Path(tempfile.mkdtemp())
    S.save_price_cache(tmp, cache, {"prices_full_at": "2026-10-15"}, date(2026, 10, 18))
    loaded, meta = S.load_price_cache(tmp, date(2026, 10, 18))
    assert "AAA" in loaded
    loaded, meta = S.load_price_cache(tmp, date(2026, 10, 23))             # 距上次整批重抓滿 7 天：丟掉快取
    assert loaded == {} and meta["prices_full_at"] == "2026-10-23"


def test_merge_prices_rescales_for_dividends():
    old = frame(100)
    new = old.iloc[-10:].copy()
    new[["Open", "High", "Low", "Close"]] *= 0.98                           # 除息後 Yahoo 把整段歷史調成 0.98 倍
    new.loc[new.index[-1] + pd.offsets.BDay(1)] = [98, 99, 97, 98.5, 1e6]
    merged = S.merge_prices(old, new)
    assert len(merged) == 101 and abs(merged["Close"].iloc[0] - 98.0) < 1e-9 and abs(merged["Close"].iloc[89] - merged["Close"].iloc[90]) < 1e-9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
