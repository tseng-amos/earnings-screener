"""抓取美日財報日曆與日線，執行選股，輸出 calendar.json / screen.json。

用法：python scripts/fetch_data.py --out data
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from screen import analyze, find_gap_day, market_regime, match_event, prepare  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
JPX_PAGE = "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html"


def log(msg: str) -> None:
    print(msg, flush=True)


def to_float(text) -> float | None:
    """'$1,234.5' / '(0.12)' / 'N/A' → float 或 None。"""
    if text is None:
        return None
    s = str(text).replace("$", "").replace(",", "").replace("%", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# ---------- 美股財報日曆（Nasdaq 公開 API） ----------
def fetch_us_day(day: date) -> list[dict]:
    for attempt in range(3):
        try:
            r = requests.get(NASDAQ_URL, params={"date": day.isoformat()}, headers=UA, timeout=20)
            r.raise_for_status()
            rows = (r.json().get("data") or {}).get("rows") or []
            return [{
                "date": day.isoformat(), "market": "US",
                "symbol": row.get("symbol", "").strip(), "name": row.get("name", ""),
                "time": {"time-pre-market": "BMO", "time-after-hours": "AMC"}.get(row.get("time"), ""),
                "mcap": to_float(row.get("marketCap")),
                "eps_forecast": to_float(row.get("epsForecast")),
                "eps_actual": to_float(row.get("eps")),
                "eps_surprise_pct": to_float(row.get("surprise")),
            } for row in rows if row.get("symbol")]
        except Exception as exc:  # 網路或限流：重試
            log(f"  [US {day}] 第 {attempt + 1} 次失敗：{exc}")
            time.sleep(2 * (attempt + 1))
    return []


def fetch_us_events(start: date, end: date, min_mcap: float) -> list[dict]:
    events, day = [], start
    while day <= end:
        if day.weekday() < 5:
            events += [e for e in fetch_us_day(day) if (e["mcap"] or 0) >= min_mcap]
            time.sleep(0.4)
        day += timedelta(days=1)
    log(f"美股事件：{len(events)} 筆")
    return events


# ---------- 日本財報日曆（JPX 決算發表予定日 Excel + 手動補充） ----------
def parse_jpx_excel(content: bytes) -> list[dict]:
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
    head = next((i for i in range(min(20, len(raw)))
                 if raw.iloc[i].astype(str).str.contains("予定日").any()
                 and raw.iloc[i].astype(str).str.contains("コード").any()), None)
    if head is None:
        return []
    cols = raw.iloc[head].astype(str)
    c_date = cols[cols.str.contains("予定日")].index[0]
    c_code = cols[cols.str.contains("コード")].index[0]
    c_name = next(iter(cols[cols.str.contains("会社名|名称")].index), None)
    rows = []
    for _, r in raw.iloc[head + 1:].iterrows():
        dt = pd.to_datetime(r[c_date], errors="coerce")
        code = str(r[c_code]).strip()[:4]
        if pd.isna(dt) or not re.fullmatch(r"[0-9A-Z]{4}", code):
            continue
        rows.append({"date": dt.date().isoformat(), "code": code,
                     "name": "" if c_name is None else str(r[c_name]).strip()})
    return rows


def fetch_jp_events(universe: set[str], start: date, end: date) -> list[dict]:
    found: dict[tuple, dict] = {}
    try:
        html = requests.get(JPX_PAGE, headers=UA, timeout=30).text
        links = sorted(set(urljoin(JPX_PAGE, h) for h in re.findall(r'href="([^"]+\.xlsx?)"', html)))
        log(f"JPX 附件連結：{len(links)} 個")
        for link in links:
            try:
                for row in parse_jpx_excel(requests.get(link, headers=UA, timeout=60).content):
                    found[(row["date"], row["code"])] = row
            except Exception as exc:
                log(f"  JPX 檔案解析失敗 {link}：{exc}")
    except Exception as exc:
        log(f"JPX 頁面抓取失敗：{exc}（可改用 data/jp_earnings_manual.csv 手動補充）")

    manual = ROOT / "data" / "jp_earnings_manual.csv"
    if manual.exists():
        with manual.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("date") and row.get("code"):
                    found[(row["date"].strip(), row["code"].strip())] = {
                        "date": row["date"].strip(), "code": row["code"].strip(), "name": row.get("name", "")}

    events = [{"date": r["date"], "market": "JP", "symbol": r["code"], "name": r["name"],
               "time": "", "mcap": None, "eps_forecast": None, "eps_actual": None,
               "eps_surprise_pct": None, "in_universe": r["code"] in universe}
              for r in found.values() if start.isoformat() <= r["date"] <= end.isoformat()]
    log(f"日本事件：{len(events)} 筆")
    return events


def load_jp_universe() -> list[str]:
    lines = (ROOT / "data" / "universe_jp.txt").read_text(encoding="utf-8").splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


# ---------- 價格與選股 ----------
def download_prices(yf_tickers: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for i in range(0, len(yf_tickers), 100):
        chunk = yf_tickers[i:i + 100]
        try:
            data = yf.download(chunk, period=period, interval="1d", group_by="ticker",
                               auto_adjust=True, threads=True, progress=False)
        except Exception as exc:
            log(f"  下載失敗 {chunk[0]}…：{exc}")
            continue
        for t in chunk:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                df = df.dropna(how="all")
                if len(df) > 60:
                    result[t] = df
            except KeyError:
                continue
        log(f"  已下載 {min(i + 100, len(yf_tickers))}/{len(yf_tickers)}")
    return result


def run_screen(events: list[dict], jp_universe: list[str], cfg: dict, today: date) -> list[dict]:
    window = today - timedelta(days=int(cfg["lookback_trading_days"] * 1.5) + 7)
    past = [e for e in events if window.isoformat() <= e["date"] <= today.isoformat()]

    # 事件索引：(市場, 代號) → 財報日清單／最新事件資料
    by_key: dict[tuple, list[dict]] = {}
    for e in events:
        by_key.setdefault((e["market"], e["symbol"]), []).append(e)

    targets = {("US", e["symbol"]) for e in past if e["market"] == "US"}
    targets |= {("JP", c) for c in jp_universe}
    yf_map = {(m, s): (s.replace(".", "-") if m == "US" else f"{s}.T") for m, s in targets}

    log(f"掃描標的 {len(yf_map)} 檔，下載日線…")
    prices = download_prices(sorted(set(yf_map.values())))

    candidates = []
    for (market, symbol), yf_t in yf_map.items():
        if yf_t not in prices:
            continue
        d = prepare(prices[yf_t])
        pos = find_gap_day(d, cfg)
        if pos is None:
            continue
        evs = by_key.get((market, symbol), [])
        matched = match_event(d.index[pos], [pd.Timestamp(e["date"]) for e in evs], cfg["link_window_days"])
        if market == "US" and matched is None:
            continue  # 美股以財報日曆為準：非財報帶動的跳空不列入
        result = analyze(d, pos, cfg)
        ev = next((e for e in evs if matched is not None and e["date"] == matched.date().isoformat()), {})
        result.update(
            market=market, symbol=symbol, name=ev.get("name") or "",
            earnings_linked=matched is not None,
            earnings_date=None if matched is None else matched.date().isoformat(),
            earnings_time=ev.get("time", ""),
            eps_forecast=ev.get("eps_forecast"), eps_actual=ev.get("eps_actual"),
            eps_surprise_pct=ev.get("eps_surprise_pct"),
        )
        candidates.append(result)
    log(f"通過步驟一：{len(candidates)} 檔")
    return candidates


def fill_jp_names(candidates: list[dict]) -> None:
    """日本標的若缺公司名稱，以 yfinance 補上（失敗則留空）。"""
    for c in candidates:
        if c["market"] == "JP" and not c["name"]:
            try:
                c["name"] = yf.Ticker(f"{c['symbol']}.T").info.get("shortName", "")
            except Exception:
                c["name"] = ""


def get_regimes(cfg: dict) -> dict:
    out = {}
    for market, idx in cfg["regime_index"].items():
        try:
            df = yf.download(idx, period="6mo", interval="1d", auto_adjust=True, progress=False)
            close = df["Close"].squeeze().dropna()
            out[market] = {"index": idx, **market_regime(close)}
        except Exception as exc:
            log(f"大盤 {idx} 取得失敗：{exc}")
            out[market] = {"index": idx, "status": "unknown"}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    first_this = today.replace(day=1)
    cal_start = (first_this - timedelta(days=1)).replace(day=1)          # 上月 1 日
    cal_end = (first_this + timedelta(days=62)).replace(day=1) - timedelta(days=1)  # 下月底
    screen_start = today - timedelta(days=int(cfg["lookback_trading_days"] * 1.5) + 7)
    fetch_start = min(cal_start, screen_start)

    jp_universe = load_jp_universe()
    events = fetch_us_events(fetch_start, cal_end, cfg["us_min_mcap_usd"])
    events += fetch_jp_events(set(jp_universe), fetch_start, cal_end)
    events.sort(key=lambda e: (e["date"], e["market"], -(e["mcap"] or 0)))

    candidates = run_screen(events, jp_universe, cfg, today)
    fill_jp_names(candidates)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "calendar.json").write_text(json.dumps(
        {"generated_at": now, "events": events}, ensure_ascii=False), encoding="utf-8")
    (out / "screen.json").write_text(json.dumps(
        {"generated_at": now, "regime": get_regimes(cfg), "candidates": candidates},
        ensure_ascii=False), encoding="utf-8")
    log("完成")


if __name__ == "__main__":
    main()
