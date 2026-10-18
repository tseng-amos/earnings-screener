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

def fetch_us_day(day: date) -> list[dict] | None:
    """回傳當日財報清單；連續 3 次失敗回傳 None（與「當天沒有財報」的空清單區分）。"""
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
    return None

def fetch_events(start: date, end: date, minimum_mcap: float) -> tuple[list[dict], list[str]]:
    """回傳 (事件清單, 抓取失敗的日期清單)。"""
    out, failed, day = [], [], start
    while day <= end:
        if day.weekday() < 5:
            rows = fetch_us_day(day)
            if rows is None:
                failed.append(day.isoformat())
            else:
                out.extend(event for event in rows if (event["mcap"] or 0) >= minimum_mcap)
            time.sleep(.35)
        day += timedelta(days=1)
    return out, failed

def download_prices(tickers: list[str]) -> dict[str, pd.DataFrame]:
    prices: dict[str, pd.DataFrame] = {}
    for start in range(0, len(tickers), 100):
        group = tickers[start:start + 100]
        data = None
        for attempt in range(2):
            try:
                data = yf.download(group, period="1y", interval="1d", group_by="ticker", auto_adjust=True, threads=True, progress=False)
                break
            except Exception as exc:
                log(f"日線下載失敗 {group[0]}…（第 {attempt + 1} 次）：{exc}")
                time.sleep(3)
        if data is not None:
            for ticker in group:
                try:  # 單一標的缺資料不應拖累同批其他標的
                    frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
                    frame = frame.dropna(how="all")
                    if len(frame) > 60:
                        prices[ticker] = frame
                except KeyError:
                    continue
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
    # 選股要比對回看期內的財報日，日曆起點不可晚於回看期起點，否則月初會漏掉上月中以前的標的
    lookback_start = today - timedelta(days=int(cfg["lookback_trading_days"] * 1.5) + 7)
    events, failed_days = fetch_events(min(calendar_start, lookback_start), calendar_end, cfg["us_min_mcap_usd"])
    events.sort(key=lambda x: (x["date"], -(x["mcap"] or 0)))
    # 資料源整體失效時直接讓工作流程失敗，保留上一次成功部署的網站，而不是發布空資料
    if not events:
        log("錯誤：沒有取得任何財報事件，中止（保留上次部署）")
        sys.exit(1)
    targets = sorted({event["symbol"].replace(".", "-") for event in events})
    prices = download_prices(targets)
    if not prices:
        log("錯誤：沒有取得任何價格資料，中止（保留上次部署）")
        sys.exit(1)
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
    (out / "calendar.json").write_text(json.dumps({"generated_at": now, "fetch_errors": failed_days, "events": events}, ensure_ascii=False), encoding="utf-8")
    (out / "screen.json").write_text(json.dumps({"generated_at": now, "regime": get_regime(), "candidates": candidates}, ensure_ascii=False), encoding="utf-8")
    log(f"完成：{len(events)} 個財報事件、{len(candidates)} 個候選標的、{len(failed_days)} 天日曆抓取失敗")

if __name__ == "__main__":
    main()
