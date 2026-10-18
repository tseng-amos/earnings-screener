"""財報後選股邏輯（Qullamaggie 跳空 / PEAD 整理突破）。

純函數、不含網路存取，方便單元測試。
輸入：單一標的日線 DataFrame（Open/High/Low/Close/Volume，日期遞增）。
"""
from __future__ import annotations

import pandas as pd


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """計算漲幅、量能倍數、50 日均線、日內振幅。"""
    d = df[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    d["ret"] = d["Close"].pct_change() * 100
    # 量能基準：前 50 日平均量（不含當天）
    d["vol_ratio"] = d["Volume"] / d["Volume"].shift(1).rolling(50).mean()
    d["sma50"] = d["Close"].rolling(50).mean()
    d["range_pct"] = (d["High"] - d["Low"]) / d["Close"] * 100
    return d


def find_gap_day(d: pd.DataFrame, cfg: dict) -> int | None:
    """步驟一：回看期內最近一個「漲 >= 10% 且量 >= 2 倍均量」的交易日，回傳位置。"""
    start = max(len(d) - cfg["lookback_trading_days"], 1)
    hit = (d["ret"] >= cfg["min_gain_pct"]) & (d["vol_ratio"] >= cfg["min_volume_ratio"])
    positions = [i for i in range(start, len(d)) if bool(hit.iloc[i])]
    return positions[-1] if positions else None


def match_event(reaction_date: pd.Timestamp, event_dates: list[pd.Timestamp], window: int):
    """確認跳空是否由財報帶起：財報日在反應日當天或前 window 天內。"""
    for e in sorted(event_dates, reverse=True):
        if 0 <= (reaction_date - e).days <= window:
            return e
    return None


def _check(status: str, label: str, detail: str) -> dict:
    return {"status": status, "label": label, "detail": detail}


def analyze(d: pd.DataFrame, pos: int, cfg: dict) -> dict:
    """步驟二、三：對指定反應日評估整理條件與突破狀態。"""
    n = len(d)
    r = d.iloc[pos]
    out = {
        "reaction_date": d.index[pos].strftime("%Y-%m-%d"),
        "gain_pct": round(float(r["ret"]), 1),
        "volume_ratio": round(float(r["vol_ratio"]), 1),
        "reaction_low": round(float(r["Low"]), 2),
        "last_date": d.index[-1].strftime("%Y-%m-%d"),
        "last_close": round(float(d["Close"].iloc[-1]), 2),
    }

    # 剔除規則：收盤跌破財報大漲日的最低點
    post = d.iloc[pos + 1:]
    broken = post[post["Close"] < r["Low"]]
    if not broken.empty:
        out.update(stage="removed", removed_on=broken.index[0].strftime("%Y-%m-%d"), checks=[])
        return out

    # 突破偵測：整理滿 min_days 根後，第一根「收盤」站上整理區高點（盤中碰到不算）
    min_days = cfg["min_consolidation_days"]
    k = None
    for j in range(pos + 1 + min_days, n):
        if d["Close"].iloc[j] > d["High"].iloc[pos + 1:j].max():
            k = j
            break
    cons_end = k if k is not None else n
    cons = d.iloc[pos + 1:cons_end]
    n_c = len(cons)

    checks = []

    # 條件一：自財報後高點回落不超過 25%
    if n_c == 0:
        checks.append(_check("pending", "回落幅度", "財報隔日後尚無整理資料"))
        max_dd = None
    else:
        seg = d.iloc[pos:cons_end]
        run_high = seg["High"].cummax()
        # 自反應日最高點起算；反應日當天自身的盤中區間不計入回落
        max_dd = float(((run_high - seg["Low"]) / run_high * 100).iloc[1:].max())
        ok = max_dd <= cfg["pullback_max_pct"]
        checks.append(_check("pass" if ok else "fail", "回落幅度",
                             f"最大回落 {max_dd:.1f}%（上限 {cfg['pullback_max_pct']}%）"))

    # 條件二：整理期間不可有放量大陰線（1 次黃燈、2 次以上不合格）
    dist = cons[(cons["ret"] <= -cfg["dist_drop_pct"]) & (cons["vol_ratio"] >= cfg["dist_volume_ratio"])]
    st = "pass" if len(dist) == 0 else ("warn" if len(dist) == 1 else "fail")
    checks.append(_check(st, "無放量大陰線", f"{len(dist)} 次（跌≥{cfg['dist_drop_pct']}% 且量≥{cfg['dist_volume_ratio']}倍）"))

    # 條件三：收盤不跌破 50 日均線
    below = cons[cons["Close"] < cons["sma50"]]
    checks.append(_check("pass" if below.empty else "fail", "站穩50日線",
                         "整理期收盤均在 50 日線上" if below.empty else f"{len(below)} 日收盤低於 50 日線"))

    # 條件四：至少整理一個星期
    checks.append(_check("pass" if n_c >= min_days else "pending", "整理天數", f"{n_c} / {min_days} 日"))

    # 條件五：近 3 日振幅與量能收斂
    if n_c >= 4:
        last3, prev = cons.tail(3), cons.iloc[:-3]
        rng3, rng_prev = last3["range_pct"].mean(), prev["range_pct"].mean()
        vol3 = last3["vol_ratio"].mean()
        ok = rng3 < rng_prev and vol3 < 1.0
        checks.append(_check("pass" if ok else "fail", "振幅量能收斂",
                             f"近3日振幅 {rng3:.1f}%（前段 {rng_prev:.1f}%）、量 {vol3:.2f} 倍均量"))
    else:
        checks.append(_check("pending", "振幅量能收斂", "資料不足（需整理 4 日以上）"))

    # 進出場價位：整理區高點為買點，近 N 根最低點為止損
    pivot = stop = risk_pct = None
    adr = None
    if n_c > 0:
        pivot = float(cons["High"].max())
        stop_from = (k if k is not None else n) - cfg["stop_lookback_bars"]
        stop = float(d["Low"].iloc[max(stop_from, pos + 1):(k if k is not None else n)].min())
        risk_pct = (pivot - stop) / pivot * 100
        # 平時波動：財報日之前 20 日的平均日內振幅
        adr = float(d["range_pct"].iloc[max(pos - 20, 0):pos].mean())

    # 階段判定
    has_fail = any(c["status"] == "fail" for c in checks)
    has_pending = any(c["status"] == "pending" for c in checks)
    if k is not None and not has_fail:
        stage = "breakout" if (n - 1 - k) < cfg["breakout_fresh_bars"] else "extended"
    elif k is None and not has_fail and not has_pending:
        stage = "consolidating"
    else:
        stage = "watch"

    out.update(
        stage=stage,
        checks=checks,
        cons_days=n_c,
        max_pullback_pct=None if max_dd is None else round(max_dd, 1),
        pivot=None if pivot is None else round(pivot, 2),
        stop=None if stop is None else round(stop, 2),
        risk_pct=None if risk_pct is None else round(risk_pct, 1),
        adr_pct=None if adr is None else round(adr, 1),
        risk_ok=None if risk_pct is None or adr is None else bool(risk_pct <= adr * cfg["risk_adr_mult"]),
        breakout_date=None if k is None else d.index[k].strftime("%Y-%m-%d"),
        dist_to_pivot_pct=None if pivot is None else round((pivot / float(d["Close"].iloc[-1]) - 1) * 100, 1),
    )
    return out


def market_regime(close: pd.Series) -> dict:
    """大盤濾網：10 日線在 20 日線之上且兩線向上 = strong；兩線向下且 10 在 20 下 = weak；其餘 caution。"""
    s10, s20 = close.rolling(10).mean(), close.rolling(20).mean()
    up10 = bool(s10.iloc[-1] > s10.iloc[-4])
    up20 = bool(s20.iloc[-1] > s20.iloc[-4])
    above = bool(s10.iloc[-1] > s20.iloc[-1])
    if above and up10 and up20:
        status = "strong"
    elif not above and not up10 and not up20:
        status = "weak"
    else:
        status = "caution"
    return {"status": status, "date": close.index[-1].strftime("%Y-%m-%d"),
            "ma10_above_ma20": above, "ma10_up": up10, "ma20_up": up20}
