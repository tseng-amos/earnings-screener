"""美股專用：抓 Nasdaq 財報日曆與 Yahoo Finance 日線，產生網站資料。

用法：python scripts/fetch_data.py --out data
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from screen import analyze, find_gap_day, market_regime, match_event, prepare  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}

def log(message: str) -> None:
    print(message, flush=True)

def to_float(value) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None

def fetch_us_day(day: date) -> list[dict]:
    for attempt in range(3):
        try:
            response = requests.get(NASDAQ_URL, params={"date": day.isoformat()}, headers=UA, timeout=25)
            response.raise_for_status()
            return [{
                "date": day.isoformat(), "market": "US", "symbol": row["symbol"].strip(),
                "name": row.get("name", ""), "time": {"time-pre-market": "盤前", "time-after-hours": "盤後"}.get(row.get("time"), "時間未定"),
                "mcap": to_float(row.get("marketCap")), "eps_forecast": to_float(row.get("epsForecast")),
                "eps_actual": to_float(row.get("eps")), "eps_surprise_pct": to_float(row.get("surprise")),
            } for row in ((response.json().get("data") or {}).get("rows") or []) if row.get("symbol")]
        except Exception as exc:
            log(f"Nasdaq {day} 第 {attempt + 1} 次失敗：{exc}")
            time.sleep(2 * (attempt + 1))
    return []

def fetch_events(start: date, end: date, minimum_mcap: float) -> list[dict]:
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.extend(event for event in fetch_us_day(day) if (event["mcap"] or 0) >= minimum_mcap)
            time.sleep(.35)
        day += timedelta(days=1)
    return out

def download_prices(tickers: list[str]) -> dict[str, pd.DataFrame]:
    prices: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), 100):
        group = tickers[start:start + 100]
        try:
            data = yf.download(group, period="1y", interval="1d", group_by="ticker", auto_adjust=True, threads=True, progress=False)
            for ticker in group:
                frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
                frame = frame.dropna(how="all")
                if len(frame) > 60:
                    prices[ticker] = frame
        except Exception as exc:
            log(f"日線下載失敗 {group[0]}…：{exc}")
        log(f"已下載 {min(start + 100, len(tickers))}/{len(tickers)}")
    return prices

def get_regime() -> dict:
    try:
        frame = yf.download("^IXIC", period="6mo", interval="1d", auto_adjust=True, progress=False)
        return {"US": {"index": "^IXIC", **market_regime(frame["Close"].squeeze().dropna())}}
    except Exception as exc:
        log(f"Nasdaq 指數下載失敗：{exc}")
        return {"US": {"index": "^IXIC", "status": "unknown"}}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data"))
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    calendar_start = (month_start - timedelta(days=1)).replace(day=1)
    calendar_end = (month_start + timedelta(days=62)).replace(day=1) - timedelta(days=1)
    events = fetch_events(calendar_start, calendar_end, cfg["us_min_mcap_usd"])
    events.sort(key=lambda x: (x["date"], -(x["mcap"] or 0)))
    targets = sorted({event["symbol"].replace(".", "-") for event in events})
    prices = download_prices(targets)
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event["symbol"].replace(".", "-"), []).append(event)
    candidates = []
    for symbol, frame in prices.items():
        prepared = prepare(frame)
        pos = find_gap_day(prepared, cfg)
        if pos is None:
            continue
        event_dates = [pd.Timestamp(event["date"]) for event in by_symbol.get(symbol, [])]
        matched = match_event(prepared.index[pos], event_dates, cfg["link_window_days"])
        if matched is None:
            continue
        result = analyze(prepared, pos, cfg)
        event = next((x for x in by_symbol[symbol] if x["date"] == matched.date().isoformat()), {})
        result.update(market="US", symbol=symbol, name=event.get("name", ""), earnings_linked=True,
                      earnings_date=matched.date().isoformat(), earnings_time=event.get("time", ""),
                      eps_forecast=event.get("eps_forecast"), eps_actual=event.get("eps_actual"),
                      eps_surprise_pct=event.get("eps_surprise_pct"))
        candidates.append(result)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "calendar.json").write_text(json.dumps({"generated_at": now, "events": events}, ensure_ascii=False), encoding="utf-8")
    (out / "screen.json").write_text(json.dumps({"generated_at": now, "regime": get_regime(), "candidates": candidates}, ensure_ascii=False), encoding="utf-8")
    log(f"完成：{len(events)} 個財報事件、{len(candidates)} 個候選標的")

if __name__ == "__main__":
    main()
