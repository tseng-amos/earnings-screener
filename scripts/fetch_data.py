"""產生網站資料 data/screen.json。

用法：python scripts/fetch_data.py --out data [--cache cache] [--prev prev_screen.json]
選股邏輯在 screen.py、資料來源在 sources.py；本檔的 build_screen 是純計算（不連網），可離線測試。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402
from screen import (analyze, analyze_breakout, chart_rows, ep_quality, evaluate_holding, find_gap_day,  # noqa: E402
                    market_regime, match_event, momentum_ranks, prepare)

ROOT = Path(__file__).resolve().parent.parent
log = sources.log


# ---------- 純計算（不連網）----------

def next_earnings_of(history: list[dict], today: date) -> tuple[str | None, int | None]:
    upcoming = sorted(e["date"] for e in history if e["date"] >= today.isoformat())
    return (upcoming[0], (date.fromisoformat(upcoming[0]) - today).days) if upcoming else (None, None)


def report_time(event: dict, reaction_date: str) -> str:
    """Nasdaq 對已公布的財報不再提供盤前／盤後欄位（全變「時間未定」）。價格已經反應了，就由反應日推定：
    反應日＝財報日 → 盤前公布；反應日晚於財報日 → 盤後公布。"""
    if event.get("time") in ("盤前", "盤後"):
        return event["time"]
    return "盤前（推定）" if reaction_date == event["date"] else "盤後（推定）"


def build_screen(prices: dict[str, pd.DataFrame], events: list[dict], regime: dict, cfg: dict, today: date,
                 holdings: list[dict] | None = None) -> dict:
    """prices：{代號: 日線}；events：財報事件（含過去與未來）；回傳網站用的 screen.json 內容（尚未加上與昨日的差異）。"""
    by_symbol: dict[str, list[dict]] = {}
    for event in events:
        by_symbol.setdefault(event["symbol"], []).append(event)
    frames = {s: prepare(df) for s, df in prices.items()}
    ranks = momentum_ranks(frames, cfg)

    pead, breakout = [], []
    for symbol, d in frames.items():
        history = by_symbol.get(symbol, [])
        name = next((e["name"] for e in history if e.get("name")), "")
        next_date, days = next_earnings_of(history, today)

        # 財報後線：反應日由財報帶動（影片 A；盤前／盤後精確配對），再以 EP 條件打分（影片 B）
        pos = find_gap_day(d, cfg)
        if pos is not None:
            event = match_event(d, pos, history)
            if event is not None:
                result = analyze(d, pos, cfg)
                pead.append({**result, **ep_quality(d, pos, event, cfg), "symbol": symbol, "name": name,
                             "earnings_date": event["date"], "earnings_time": report_time(event, result["reaction_date"]),
                             "eps_surprise_pct": event.get("eps_surprise_pct"), "next_earnings": next_date})

        # 動能突破線（影片 B）：下次財報距離用於「離財報兩三天不買」
        if symbol in ranks:
            result = analyze_breakout(d, cfg, ranks[symbol], days)
            if result["stage"] in ("breakout", "setup"):
                breakout.append({**result, "symbol": symbol, "name": name, "next_earnings": next_date})

    breakout.sort(key=lambda x: -x["momentum_rank"])
    breakout_total = len(breakout)
    breakout = breakout[:cfg["bo_max_signals"]]

    # 迷你 K 線資料：PEAD 從反應日前 6 根起、突破線從高點前 20 根起；每條線最多 max_chart_items 檔
    def attach_chart(items: list[dict], start_of) -> None:
        for item in items[:cfg["max_chart_items"]]:
            d = frames[item["symbol"]]
            item["chart"] = chart_rows(d, start_of(d, item), cfg["chart_bars"])
    attach_chart(pead, lambda d, c: int(d.index.get_loc(pd.Timestamp(c["reaction_date"]))) - 6)
    attach_chart(breakout, lambda d, c: int(d.index.get_loc(pd.Timestamp(c["peak_date"]))) - 20)

    held = []
    for row in holdings or []:
        d = frames.get(row["symbol"])
        if d is None:
            held.append({"symbol": row["symbol"], "status": "nodata", "actions": [{"level": "watch", "text": "沒有取得這檔的價格資料"}]})
            continue
        _, days = next_earnings_of(by_symbol.get(row["symbol"], []), today)
        item = evaluate_holding(d, row, cfg, days)
        item["chart"] = chart_rows(d, max(int(d.index.searchsorted(pd.Timestamp(row["entry_date"]))) - 10, 0), cfg["chart_bars"])
        held.append(item)

    count = lambda items: {k: sum(i["stage"] == k for i in items) for k in sorted({i["stage"] for i in items})}  # noqa: E731
    return {"regime": regime, "universe_size": len(frames), "breakout_total": breakout_total, "pead": pead, "breakout": breakout,
            "holdings": held, "summary": {"pead": count(pead), "breakout": count(breakout)}}


LANES = (("pead", lambda c: f"{c['symbol']}|{c['reaction_date']}"), ("breakout", lambda c: c["symbol"]))


def annotate_changes(out: dict, prev: dict | None) -> None:
    """與上一次部署的結果比較：標出新出現、階段改變的標的，以及移出清單的標的（每天只想看「新增了什麼」）。"""
    out["dropped"] = []
    out["summary"]["has_prev"] = bool(prev)
    out["summary"]["new"] = out["summary"]["advanced"] = 0
    if not prev:
        return
    for lane, key in LANES:
        old = {key(c): c for c in prev.get(lane, [])}
        for item in out[lane]:
            before = old.get(key(item))
            if before is None:
                item["change"] = "new"
                out["summary"]["new"] += 1
            elif before["stage"] != item["stage"]:
                item.update(change="stage", prev_stage=before["stage"])
                out["summary"]["advanced"] += 1
        now = {key(c) for c in out[lane]}
        out["dropped"] += [{"lane": lane, "symbol": c["symbol"], "prev_stage": c["stage"]} for k, c in old.items() if k not in now]


def load_holdings(path: Path) -> list[dict]:
    """data/holdings.csv：symbol,entry_date,entry_price,shares,stop,partial_done,exit_ma,note（無檔或空檔回傳空清單）。"""
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("symbol") or "").strip().upper().replace(".", "-")
            try:
                if symbol and row.get("entry_date") and float(row["entry_price"]) > 0:
                    rows.append({**{k: (v or "").strip() for k, v in row.items() if k}, "symbol": symbol})
            except (TypeError, ValueError):
                log(f"持倉檔略過格式錯誤的一列：{row}")
    return rows


# ---------- 主程式 ----------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data"))
    parser.add_argument("--cache", default=None, help="快取資料夾；省略則不使用快取")
    parser.add_argument("--prev", default=None, help="上一次部署的 screen.json（用來標示新增與階段改變）")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache) if args.cache else None
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    today = now.date()
    month_start = today.replace(day=1)
    calendar_end = (month_start + timedelta(days=62)).replace(day=1) - timedelta(days=1)
    calendar_start = (month_start - timedelta(days=75)).replace(day=1)   # 要涵蓋整個股票池的一個財報週期

    events, failed_days = sources.fetch_events(calendar_start, calendar_end, cfg["us_min_mcap_usd"],
                                               cache_dir / "events.json" if cache_dir else None, today)
    # 資料源整體失效時直接讓工作流程失敗，保留上一次成功部署的網站，而不是發布空資料
    if not events:
        log("錯誤：沒有取得任何財報事件，中止（保留上次部署）")
        sys.exit(1)
    finnhub_filled = sources.enrich_with_finnhub(events, calendar_start, today)
    events.sort(key=lambda x: (x["date"], -(x.get("mcap") or 0)))

    holdings = load_holdings(ROOT / "data" / "holdings.csv")
    universe = sources.fetch_universe(cfg["universe_min_price"], cfg["universe_min_dollar_volume_usd"])
    if universe is None:
        log("警告：Nasdaq 股票清單取得失敗，動能排名的股票池退回為「有財報的標的」")
    targets = sorted({e["symbol"] for e in events} | {u["symbol"] for u in universe or []} | {h["symbol"] for h in holdings})

    cache, meta = sources.load_price_cache(cache_dir, today)
    prices, price_sources = sources.fetch_prices(targets, cfg["min_bars"], cache)
    trimmed = 0
    for symbol in list(prices):
        full = prices[symbol]
        prices[symbol] = sources.drop_incomplete_bar(full, now)
        trimmed += len(prices[symbol]) < len(full)
        if len(prices[symbol]) < cfg["min_bars"]:
            del prices[symbol]
    success = len(prices) / max(len(targets), 1) * 100
    log(f"價格：{len(prices)}/{len(targets)} 檔成功（{success:.0f}%），盤中未完成的最後一根已丟棄 {trimmed} 檔")
    if not prices or success < cfg["min_price_success_pct"]:
        log(f"錯誤：價格資料成功率 {success:.0f}% 低於 {cfg['min_price_success_pct']}%，中止（保留上次部署）")
        sys.exit(1)
    if cache_dir:
        sources.save_price_cache(cache_dir, cache, meta, today)

    series, regime_index = sources.fetch_regime_series(cfg["regime_index"])
    regime = {"index": regime_index, **market_regime(series)} if series is not None else {"index": regime_index, "status": "unknown"}

    result = build_screen(prices, events, regime, cfg, today, holdings)
    prev = None
    if args.prev and Path(args.prev).exists():
        try:
            prev = json.loads(Path(args.prev).read_text(encoding="utf-8"))
        except ValueError:
            prev = None
    annotate_changes(result, prev)
    counts = {name: sum(v == name for v in price_sources.values()) for name in ("yahoo", "cache", "stooq", "nasdaq")}
    result.update(
        generated_at=now.isoformat(timespec="seconds"),
        asof=max(f.index[-1] for f in prices.values()).strftime("%Y-%m-%d"),     # 價格資料實際截止日
        fetch_errors=failed_days,
        health={"universe_source": "nasdaq" if universe else "events_only", "targets": len(targets), "price_ok": len(prices),
                "price_sources": counts, "partial_bar_dropped": trimmed, "finnhub_filled": finnhub_filled,
                "calendar_failed_days": len(failed_days), "price_success_pct": round(success, 1)})
    (out_dir / "screen.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    log(f"完成：股票池 {result['universe_size']} 檔、財報後 {len(result['pead'])} 檔、動能突破 {len(result['breakout'])}/{result['breakout_total']} 檔、"
        f"持倉 {len(holdings)} 檔、價格截至 {result['asof']}")


if __name__ == "__main__":
    main()
