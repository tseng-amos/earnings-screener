"""資料來源：所有網路存取集中在這裡，其餘模組只吃 DataFrame／dict，方便用假資料離線測試。

  財報日曆   Nasdaq（主）；設了環境變數 FINNHUB_API_KEY 時，另以 Finnhub 補營收實際／預期
  股票池     Nasdaq 股票清單（依價格與成交額預篩），再與有財報的標的合併
  價格日線   Yahoo Finance（批次，主）→ Stooq（逐檔）→ Nasdaq 歷史報價（逐檔）；每檔記錄實際來源
  大盤       ^IXIC（Yahoo）→ QQQ（Stooq／Nasdaq）作為替代
"""
from __future__ import annotations

import io
import json
import os
import pickle
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
NASDAQ = "https://api.nasdaq.com/api"
PRICE_FULL_REFRESH_DAYS = 7  # 每週整批重抓一次，修正除權息造成的歷史價格調整


def log(message: str) -> None:
    print(message, flush=True)


def to_float(value) -> float | None:
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def http_get(url: str, params: dict | None = None, timeout: int = 25, retries: int = 3, **kwargs):
    """GET 並在失敗時退避重試；最終失敗回傳 None（呼叫端決定如何處理）。"""
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=UA, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            log(f"  GET {url.split('?')[0]} 第 {attempt + 1} 次失敗：{exc}")
            time.sleep(2 * (attempt + 1))
    return None


def load_json(path: Path | None, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path else default
    except Exception:
        return default


# ---------- 財報日曆 ----------
def fetch_calendar_day(day: date) -> list[dict] | None:
    """Nasdaq 當日財報；請求失敗回傳 None（與「當天沒有財報」的空清單區分）。"""
    response = http_get(f"{NASDAQ}/calendar/earnings", {"date": day.isoformat()})
    if response is None:
        return None
    try:
        rows = (response.json().get("data") or {}).get("rows") or []
    except ValueError:
        return None
    return [{
        "date": day.isoformat(), "symbol": row["symbol"].strip().replace(".", "-").replace("/", "-"), "name": row.get("name", ""),
        "time": {"time-pre-market": "盤前", "time-after-hours": "盤後"}.get(row.get("time"), "時間未定"),
        "mcap": to_float(row.get("marketCap")), "eps_forecast": to_float(row.get("epsForecast")),
        "eps_actual": to_float(row.get("eps")), "eps_surprise_pct": to_float(row.get("surprise")),
        "eps_last_year": to_float(row.get("lastYearEPS")),
    } for row in rows if row.get("symbol")]


def fetch_events(start: date, end: date, minimum_mcap: float, cache_path: Path | None = None,
                 today: date | None = None) -> tuple[list[dict], list[str]]:
    """回傳 (事件清單, 抓取失敗的日期)。3 天前的歷史日曆讀快取，近期與未來日期每次重抓。"""
    today = today or datetime.now(timezone.utc).date()
    cache = load_json(cache_path, {})
    if cache.get("_min_mcap") != minimum_mcap:
        cache = {"_min_mcap": minimum_mcap}
    out, failed, day = [], [], start
    while day <= end:
        if day.weekday() < 5:
            key = day.isoformat()
            rows = cache.get(key) if day < today - timedelta(days=3) else None
            if rows is None:
                fetched = fetch_calendar_day(day)
                time.sleep(.35)
                if fetched is None:
                    failed.append(key)
                    day += timedelta(days=1)
                    continue
                rows = [event for event in fetched if (event["mcap"] or 0) >= minimum_mcap]
                cache[key] = rows
            out.extend(rows)
        day += timedelta(days=1)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return out, failed


def enrich_with_finnhub(events: list[dict], start: date, end: date, api_key: str | None = None) -> int:
    """選配：以 Finnhub 補營收實際／預期（影片：營收與 EPS 都要超預期）。沒有 key 或失敗就略過，回傳補到的筆數。"""
    api_key = api_key or os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return 0
    extra: dict[tuple[str, str], dict] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=13), end)
        response = http_get("https://finnhub.io/api/v1/calendar/earnings",
                            {"from": cursor.isoformat(), "to": chunk_end.isoformat(), "token": api_key})
        if response is not None:
            try:
                for row in response.json().get("earningsCalendar") or []:
                    extra[(row["symbol"].replace(".", "-"), row["date"])] = row
            except (ValueError, KeyError):
                pass
        cursor = chunk_end + timedelta(days=1)
        time.sleep(1.1)  # 免費方案每分鐘 60 次
    filled = 0
    for event in events:
        row = extra.get((event["symbol"], event["date"]))
        if row and row.get("revenueActual") is not None:
            event["rev_actual"], event["rev_forecast"] = row.get("revenueActual"), row.get("revenueEstimate")
            filled += 1
    return filled


# ---------- 股票池 ----------
SYMBOL_OK = re.compile(r"^[A-Z]{1,5}(-[A-Z])?$")
NOT_COMMON = re.compile(r"warrant|\bunit\b|\bunits\b|\bright\b|\brights\b|preferred|notes? due|depositary", re.I)


def fetch_universe(min_price: float, min_dollar_volume: float) -> list[dict] | None:
    """Nasdaq 股票清單（含 NYSE、Nasdaq、AMEX 普通股），以當日價格與成交額預篩，近似影片的「掃全市場」。失敗回傳 None。"""
    response = http_get(f"{NASDAQ}/screener/stocks", {"tableonly": "true", "limit": "25", "offset": "0", "download": "true"}, timeout=60)
    if response is None:
        return None
    try:
        rows = (response.json().get("data") or {}).get("rows") or (response.json().get("data") or {}).get("table", {}).get("rows") or []
    except ValueError:
        return None
    out = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().replace("/", "-").replace(".", "-")
        price, volume = to_float(row.get("lastsale")), to_float(row.get("volume"))
        if not SYMBOL_OK.match(symbol) or NOT_COMMON.search(str(row.get("name", ""))):
            continue
        if price is None or volume is None or price < min_price or price * volume < min_dollar_volume:
            continue
        out.append({"symbol": symbol, "name": row.get("name", ""), "price": price, "mcap": to_float(row.get("marketCap"))})
    return out or None


# ---------- 價格 ----------
def drop_incomplete_bar(frame: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """美股盤中（或剛收盤、資料尚未定案）時，最後一根日線是未完成的：量偏低、高低點未定，會讓量比與突破判斷失真。丟掉它。"""
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    if frame.empty or frame.index[-1].date() != now.date():
        return frame
    finished = now.weekday() < 5 and (now.hour, now.minute) >= (16, 20)   # 收盤 16:00，留 20 分鐘讓資料源定案
    return frame if finished else frame.iloc[:-1]


def clean_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    try:
        frame = frame[OHLCV].apply(pd.to_numeric, errors="coerce").dropna(how="all")
    except KeyError:
        return None
    frame = frame.dropna(subset=["Close"])
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return None if frame.empty else frame


def yahoo_batch(group: list[str], **kwargs) -> dict[str, pd.DataFrame]:
    """Yahoo 批次下載；整批失敗重試一次，單一標的缺資料不影響其他。"""
    import yfinance as yf
    data = None
    for attempt in range(2):
        try:
            data = yf.download(group, interval="1d", group_by="ticker", auto_adjust=True, threads=True, progress=False, **kwargs)
            break
        except Exception as exc:
            log(f"Yahoo 下載失敗 {group[0]}…（第 {attempt + 1} 次）：{exc}")
            time.sleep(3)
    frames: dict[str, pd.DataFrame] = {}
    if data is None or data.empty:
        return frames
    for ticker in group:
        try:
            frame = clean_frame(data[ticker] if isinstance(data.columns, pd.MultiIndex) else data)
        except KeyError:
            continue
        if frame is not None:
            frames[ticker] = frame
    return frames


def stooq_daily(symbol: str) -> pd.DataFrame | None:
    """Stooq 日線 CSV（免金鑰，逐檔）。"""
    response = http_get("https://stooq.com/q/d/l/", {"s": f"{symbol.lower()}.us", "i": "d"}, retries=2)
    if response is None or not response.text.lstrip().lower().startswith("date"):
        return None
    try:
        frame = pd.read_csv(io.StringIO(response.text), parse_dates=["Date"], index_col="Date")
    except Exception:
        return None
    return clean_frame(frame)


def nasdaq_daily(symbol: str, days: int = 420, asset: str = "stocks") -> pd.DataFrame | None:
    """Nasdaq 歷史報價（逐檔；已拆股調整、未含股息調整）。"""
    today = datetime.now(timezone.utc).date()
    response = http_get(f"{NASDAQ}/quote/{symbol}/historical",
                        {"assetclass": asset, "fromdate": (today - timedelta(days=days)).isoformat(), "todate": today.isoformat(), "limit": "9999"}, retries=2)
    if response is None:
        return None
    try:
        rows = ((response.json().get("data") or {}).get("tradesTable") or {}).get("rows") or []
    except ValueError:
        return None
    if not rows:
        return None
    frame = pd.DataFrame([{"Date": pd.to_datetime(r["date"], format="%m/%d/%Y"), "Open": to_float(r["open"]), "High": to_float(r["high"]),
                           "Low": to_float(r["low"]), "Close": to_float(r["close"]), "Volume": to_float(r["volume"])} for r in rows]).set_index("Date")
    return clean_frame(frame)


FALLBACKS = (("stooq", stooq_daily), ("nasdaq", nasdaq_daily))


def merge_prices(old: pd.DataFrame | None, new: pd.DataFrame | None) -> pd.DataFrame | None:
    """把新抓的幾天接到快取後面；以重疊日的收盤價比例重新縮放舊資料，吸收除權息／拆股造成的調整。"""
    if new is None or new.empty:
        return old
    if old is None or old.empty:
        return new
    overlap = old.index.intersection(new.index)
    if overlap.empty:
        return new if len(new) > 60 else old
    try:
        ratio = float(new.loc[overlap[-1], "Close"] / old.loc[overlap[-1], "Close"])
    except Exception:
        return new
    if not ratio or ratio != ratio:
        return new
    head = old[old.index < new.index[0]].copy()
    head[["Open", "High", "Low", "Close"]] *= ratio
    return pd.concat([head, new]).tail(520)


def fetch_prices(tickers: list[str], min_bars: int, cache: dict | None = None, period: str = "1y",
                 max_fallback: int = 400) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """回傳 (價格, 每檔來源)。Yahoo 批次為主；沒抓到或太短的標的逐檔改用 Stooq、再改用 Nasdaq。
    cache（ticker → DataFrame）會被就地更新；有快取的標的只補抓最近幾天。"""
    cache = {} if cache is None else cache
    fresh: dict[str, pd.DataFrame] = {}
    full = [t for t in tickers if t not in cache]
    incremental = [t for t in tickers if t in cache]
    for i in range(0, len(full), 100):
        fresh.update(yahoo_batch(full[i:i + 100], period=period))
        log(f"Yahoo（完整）{min(i + 100, len(full))}/{len(full)}")
    if incremental:
        start = (min(cache[t].index[-1] for t in incremental) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        for i in range(0, len(incremental), 100):
            fresh.update(yahoo_batch(incremental[i:i + 100], start=start))
            log(f"Yahoo（增量）{min(i + 100, len(incremental))}/{len(incremental)}")
    prices: dict[str, pd.DataFrame] = {}
    sources: dict[str, str] = {}
    for ticker in tickers:
        merged = merge_prices(cache.get(ticker), fresh.get(ticker))
        if merged is not None and len(merged) >= min_bars:
            prices[ticker] = cache[ticker] = merged
            sources[ticker] = "yahoo" if ticker in fresh else "cache"
    missing = [t for t in tickers if t not in prices]
    if missing:
        log(f"Yahoo 缺 {len(missing)} 檔，改用備援來源（最多 {max_fallback} 檔）")
    for ticker in missing[:max_fallback]:
        for name, fetch in FALLBACKS:
            frame = fetch(ticker)
            time.sleep(.3)
            if frame is not None and len(frame) >= min_bars:
                prices[ticker], sources[ticker] = frame, name
                break
    return prices, sources


def fetch_regime_series(index_symbol: str = "^IXIC") -> tuple[pd.Series | None, str]:
    """大盤收盤序列：Yahoo 的 ^IXIC；失敗時以 QQQ（Stooq、Nasdaq）近似。回傳 (序列, 實際使用的代號)。"""
    try:
        import yfinance as yf
        raw = yf.download(index_symbol, period="6mo", interval="1d", auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.droplevel(1, axis=1)
        frame = clean_frame(raw)
        if frame is not None and len(frame) > 25:
            return frame["Close"], index_symbol
    except Exception as exc:
        log(f"{index_symbol} 下載失敗：{exc}")
    for name, fetch in (("QQQ（Stooq）", lambda: stooq_daily("QQQ")), ("QQQ（Nasdaq）", lambda: nasdaq_daily("QQQ", 200, "etf"))):
        frame = fetch()
        if frame is not None and len(frame) > 25:
            return frame["Close"], name
    return None, index_symbol


def save_price_cache(cache_dir: Path, cache: dict, meta: dict, today: date) -> None:
    cutoff = pd.Timestamp(today) - pd.Timedelta(days=45)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "prices.pkl").write_bytes(pickle.dumps({t: f for t, f in cache.items() if f.index[-1] >= cutoff}))
    (cache_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def load_price_cache(cache_dir: Path | None, today: date) -> tuple[dict, dict]:
    """每週整批重抓一次（回傳空快取），其餘日子讀取既有快取。"""
    meta = load_json(cache_dir / "meta.json" if cache_dir else None, {})
    if cache_dir and meta.get("prices_full_at") and (today - date.fromisoformat(meta["prices_full_at"])).days < PRICE_FULL_REFRESH_DAYS:
        try:
            return pickle.loads((cache_dir / "prices.pkl").read_bytes()), meta
        except Exception:
            pass
    meta["prices_full_at"] = today.isoformat()
    return {}, meta
