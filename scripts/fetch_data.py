"""抓 Nasdaq 財報日曆與 Yahoo Finance 日線，產生網站資料 data/screen.json。

用法：python scripts/fetch_data.py --out data
選股邏輯在 screen.py；本檔分成「網路抓取」與純計算的 build_screen（可離線測試）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from screen import (analyze, analyze_breakout, ep_quality, find_gap_day, market_regime,  # noqa: E402
                    match_event, momentum_ranks, prepare)

ROOT = Path(__file__).resolve().parent.parent
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}


def log(message: str) -> None:
    print(message, flush=True)


# ---------- 純計算（不連網）----------

def build_screen(prices: dict[str, pd.DataFrame], events: list[dict], regime: dict, cfg: dict, today: date) -> dict:
    """prices：{代號: 日線}；events：財報事件（含過去與未來）；回傳網站用的 screen.json 內容。"""
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event["symbol"], []).append(event)
    frames = {s: prepare(df) for s, df in prices.items()}
    ranks = momentum_ranks(frames, cfg)

    pead, breakout = [], []
    for symbol, d in frames.items():
        history = by_symbol.get(symbol, [])
        name = next((e["name"] for e in history if e.get("name")), "")

        # 財報後線：反應日由財報帶動（影片 A），再以 EP 條件打分（影片 B）
        pos = find_gap_day(d, cfg)
        if pos is not None:
            matched = match_event(d.index[pos], [pd.Timestamp(e["date"]) for e in history], cfg["link_window_days"])
            if matched is not None:
                event = next(e for e in history if e["date"] == matched.date().isoformat())
                pead.append({**analyze(d, pos, cfg), **ep_quality(d, pos, event, cfg), "symbol": symbol, "name": name,
                             "earnings_date": event["date"], "earnings_time": event.get("time", ""),
                             "eps_surprise_pct": event.get("eps_surprise_pct")})

        # 動能突破線（影片 B）：下次財報距離用於「離財報兩三天不買」
        upcoming = sorted(e["date"] for e in history if e["date"] >= today.isoformat())
        days = (date.fromisoformat(upcoming[0]) - today).days if upcoming else None
        if symbol in ranks:
            result = analyze_breakout(d, cfg, ranks[symbol], days)
            if result["stage"] in ("breakout", "setup"):
                breakout.append({**result, "symbol": symbol, "name": name, "next_earnings": upcoming[0] if upcoming else None})

    breakout.sort(key=lambda x: -x["momentum_rank"])
    return {"regime": regime, "universe_size": len(frames), "breakout_total": len(breakout),
            "pead": pead, "breakout": breakout[:cfg["bo_max_signals"]]}


# ---------- 網路抓取 ----------

def to_float(value) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def fetch_us_day(day: date) -> list[dict] | None:
    """回傳當日財報清單；連續 3 次失敗回傳 None（與「當天沒有財報」的空清單區分）。"""
    import requests
    for attempt in range(3):
        try:
            response = requests.get(NASDAQ_URL, params={"date": day.isoformat()}, headers=UA, timeout=25)
            response.raise_for_status()
            rows = (response.json().get("data") or {}).get("rows") or []
            return [{
                "date": day.isoformat(), "symbol": row["symbol"].strip().replace(".", "-"), "name": row.get("name", ""),
                "time": {"time-pre-market": "盤前", "time-after-hours": "盤後"}.get(row.get("time"), "時間未定"),
                "mcap": to_float(row.get("marketCap")), "eps_forecast": to_float(row.get("epsForecast")),
                "eps_actual": to_float(row.get("eps")), "eps_surprise_pct": to_float(row.get("surprise")),
            } for row in rows if row.get("symbol")]
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
    import yfinance as yf
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
                    if len(frame) > 130:  # 6 個月動能排名需要約 127 根
                        prices[ticker] = frame
                except KeyError:
                    continue
        log(f"已下載 {min(start + 100, len(tickers))}/{len(tickers)}")
    return prices


def get_regime(index: str) -> dict:
    import yfinance as yf
    try:
        frame = yf.download(index, period="6mo", interval="1d", auto_adjust=True, progress=False)
        return {"index": index, **market_regime(frame["Close"].squeeze().dropna())}
    except Exception as exc:
        log(f"{index} 下載失敗：{exc}")
        return {"index": index, "status": "unknown"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    calendar_end = (month_start + timedelta(days=62)).replace(day=1) - timedelta(days=1)
    # 日曆起點要涵蓋整個股票池的一個財報週期（反應日比對與動能排名都靠它）
    calendar_start = (month_start - timedelta(days=75)).replace(day=1)
    events, failed_days = fetch_events(calendar_start, calendar_end, cfg["us_min_mcap_usd"])
    # 資料源整體失效時直接讓工作流程失敗，保留上一次成功部署的網站，而不是發布空資料
    if not events:
        log("錯誤：沒有取得任何財報事件，中止（保留上次部署）")
        sys.exit(1)
    prices = download_prices(sorted({event["symbol"] for event in events}))
    if not prices:
        log("錯誤：沒有取得任何價格資料，中止（保留上次部署）")
        sys.exit(1)
    result = build_screen(prices, events, get_regime(cfg["regime_index"]), cfg, today)
    result.update(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), fetch_errors=failed_days)
    (out / "screen.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    log(f"完成：股票池 {result['universe_size']} 檔、財報後 {len(result['pead'])} 檔、"
        f"動能突破 {len(result['breakout'])}/{result['breakout_total']} 檔、{len(failed_days)} 天日曆抓取失敗")


if __name__ == "__main__":
    main()
