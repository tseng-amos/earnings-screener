"""兩支影片整合的選股邏輯（純函數、不含網路存取，方便單元測試）。

  財報後線（影片 A＋B）  find_gap_day → analyze（PEAD 整理突破）＋ ep_quality（EP 跳空品質）
  動能突破線（影片 B）   momentum_ranks → analyze_breakout（強勢股回撥橫盤後突破）
  共用                   market_regime（納指 10/20 日線）

輸入：單一標的日線 DataFrame（Open/High/Low/Close/Volume，日期遞增）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """計算漲幅、量能倍數、20／50 日均線、日內振幅。"""
    d = df[["Open", "High", "Low", "Close", "Volume"]].dropna().copy()
    d["ret"] = d["Close"].pct_change() * 100
    # 量能基準：前 50 日平均量（不含當天）
    d["vol_ratio"] = d["Volume"] / d["Volume"].shift(1).rolling(50).mean()
    d["sma20"] = d["Close"].rolling(20).mean()
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


def risk_grade_of(risk_pct, adr, cfg) -> str | None:
    """止損距離對照平時波動：<=1 倍 ADR 理想（影片 A）；<=1.5 倍可接受（影片 B）；其餘太遠、先不做。"""
    if risk_pct is None or not adr:
        return None
    if risk_pct <= adr * cfg["risk_adr_mult"]:
        return "ideal"
    return "acceptable" if risk_pct <= adr * cfg["risk_adr_max_mult"] else "too_wide"


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

    # 突破偵測：整理滿 min_days 根後，第一根「最高價」越過整理區高點（影片：價格一越過就買）
    min_days = cfg["min_consolidation_days"]
    k = None
    for j in range(pos + 1 + min_days, n):
        if d["High"].iloc[j] > d["High"].iloc[pos + 1:j].max():
            k = j
            break

    # 平時波動（ADR）：財報日之前 20 日的平均日內振幅
    adr = float(d["range_pct"].iloc[max(pos - 20, 0):pos].mean()) if pos > 0 else None
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

    # 條件二之二：整理期間每天的波動比平時小（以整理期振幅中位數對照 ADR）
    if n_c == 0 or adr is None:
        checks.append(_check("pending", "波動小於平時", "尚無整理資料"))
    else:
        med = float(cons["range_pct"].median())
        checks.append(_check("pass" if med <= adr else "fail", "波動小於平時",
                             f"整理期日振幅中位數 {med:.1f}%（平時 ADR {adr:.1f}%）"))

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

    # 進出場價位：整理區高點為買點，突破前最後 N 根最低點為止損；跳空越過買點則以開盤價成交
    pivot = stop = risk_pct = entry = None
    if n_c > 0:
        pivot = float(cons["High"].max())
        stop_from = (k if k is not None else n) - cfg["stop_lookback_bars"]
        stop = float(d["Low"].iloc[max(stop_from, pos + 1):(k if k is not None else n)].min())
        if k is not None:
            entry = max(float(d["Open"].iloc[k]), pivot)
        risk_pct = ((entry or pivot) - stop) / (entry or pivot) * 100
    risk_grade = risk_grade_of(risk_pct, adr, cfg)

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
        risk_grade=risk_grade,
        entry_price=None if entry is None else round(entry, 2),
        breakout_date=None if k is None else d.index[k].strftime("%Y-%m-%d"),
        dist_to_pivot_pct=None if pivot is None else round((pivot / float(d["Close"].iloc[-1]) - 1) * 100, 1),
    )
    return out


def ep_quality(d: pd.DataFrame, pos: int, event: dict, cfg: dict) -> dict:
    """影片 B 的 EP 條件，對財報反應日打分：開盤跳空 ≥10%、EPS 超預期、之前半年沒怎麼漲。
    量能已由反應日篩選（≥2 倍）涵蓋；營收年增與指引需人工對照財報新聞稿。"""
    r = d.iloc[pos]
    gap = (float(r["Open"]) / float(d["Close"].iloc[pos - 1]) - 1) * 100
    start = max(pos - 1 - cfg["ep_prior_bars"], 0)
    prior = (float(d["Close"].iloc[pos - 1]) / float(d["Close"].iloc[start]) - 1) * 100 if pos - 1 > start else None
    actual, forecast = event.get("eps_actual"), event.get("eps_forecast")
    beat = None if actual is None or forecast is None else actual > forecast
    checks = [
        _check("pass" if gap >= cfg["min_gain_pct"] else "fail", "開盤跳空", f"開盤較前收 {gap:+.1f}%（門檻 ≥{cfg['min_gain_pct']}%）"),
        _check("unknown" if beat is None else ("pass" if beat else "fail"), "EPS 超預期",
               "日曆無預期值，請人工確認" if beat is None else f"實際 {actual} vs 預期 {forecast}"),
        _check("unknown" if prior is None else ("pass" if prior <= cfg["ep_prior_run_pct"] else "warn"), "財報前沒怎麼漲",
               "歷史資料不足" if prior is None else f"前 {cfg['ep_prior_bars']} 日漲 {prior:+.0f}%（影片：已漲過的失敗率較高）"),
    ]
    return {"gap_open_pct": round(gap, 1), "prior_run_pct": None if prior is None else round(prior, 0),
            "ep_checks": checks, "ep_score": sum(c["status"] == "pass" for c in checks)}


def momentum_ranks(frames: dict[str, pd.DataFrame], cfg: dict) -> dict[str, dict]:
    """影片 B：1／3／6 個月漲幅各自在全體標的中排百分位（100 = 最強），momentum_rank 取三者最高。"""
    ranks = {}
    for label, bars in cfg["momentum_windows"].items():
        ret = pd.Series({s: float(d["Close"].iloc[-1] / d["Close"].iloc[-1 - bars] - 1) * 100
                         for s, d in frames.items() if len(d) > bars})
        for s, pct in (ret.rank(pct=True) * 100).items():
            ranks.setdefault(s, {})[f"rank_{label}"] = round(float(pct), 1)
            ranks[s][f"ret_{label}"] = round(float(ret[s]), 1)
    for r in ranks.values():
        r["momentum_rank"] = max(v for k, v in r.items() if k.startswith("rank_"))
    return ranks


def analyze_breakout(d: pd.DataFrame, cfg: dict, rank: dict, days_to_earnings: int | None = None) -> dict:
    """影片 B 突破：強勢股（動能排名、ADR、成交額）→ 前段大漲 → 回撥橫盤 → 越過橫盤區上沿。
    以最後一根為『今天』：今天最高價越過先前橫盤高點 = breakout；尚未越過且形態合格 = setup。"""
    last = d.iloc[-1]
    adr = float(d["range_pct"].tail(20).mean())
    adv = float((d["Close"] * d["Volume"]).tail(20).mean())
    top = 100 - cfg["momentum_top_pct"]
    checks = [
        _check("pass" if rank["momentum_rank"] >= top else "fail", "動能排名",
               f"1／3／6 月百分位 {rank.get('rank_1m')}／{rank.get('rank_3m')}／{rank.get('rank_6m')}（最高需 ≥{top:g}）"),
        _check("pass" if adr >= cfg["min_adr_pct"] else "fail", "ADR", f"20 日平均日波動 {adr:.1f}%（≥{cfg['min_adr_pct']}%）"),
        _check("pass" if adv >= cfg["min_dollar_volume_usd"] else "fail", "日成交額",
               f"20 日均 {adv / 1e6:.1f}M 美元（≥{cfg['min_dollar_volume_usd'] / 1e6:g}M）"),
    ]
    out = {"last_date": d.index[-1].strftime("%Y-%m-%d"), "last_close": round(float(last["Close"]), 2),
           "adr_pct": round(adr, 1), "dollar_volume_musd": round(adv / 1e6, 1), "checks": checks, "stage": "watch", **rank}
    width = cfg["bo_lookback_bars"]
    if any(c["status"] == "fail" for c in checks) or len(d) < width + 2:
        return out

    base, win = d.iloc[:-1], d.iloc[:-1].tail(width)
    peak = int(np.argmax(win["High"].to_numpy()))
    start = win["Low"].iloc[max(peak - cfg["bo_max_runup_bars"], 0):peak + 1]  # 影片：這一波幾天到幾周就漲完
    runup = (float(win["High"].iloc[peak]) / float(start.min()) - 1) * 100
    checks.append(_check("pass" if runup >= cfg["bo_min_runup_pct"] else "fail", "前段大漲",
                         f"高點前 {cfg['bo_max_runup_bars']} 日內自低點漲 {runup:.0f}%（需 ≥{cfg['bo_min_runup_pct']}%）"))

    cons = win.iloc[peak + 1:]
    if cons.empty:
        checks.append(_check("pending", "整理天數", "高點是前一日，尚無整理"))
        return {**out, "checks": checks}
    pivot = float(cons["High"].max())
    broke = float(last["High"]) > pivot
    cons = cons if broke else pd.concat([cons, d.iloc[[-1]]])
    n_c = len(cons)

    if n_c < cfg["bo_min_cons_days"]:
        checks.append(_check("pending", "整理天數", f"{n_c} / {cfg['bo_min_cons_days']} 日"))
    else:
        long = n_c > cfg["bo_max_cons_days"]
        checks.append(_check("warn" if long else "pass", "整理天數", f"{n_c} 日（通常 {cfg['bo_min_cons_days']}～{cfg['bo_max_cons_days']} 日）"))
    if n_c >= 6:
        lows = [float(c.min()) for c in np.array_split(cons["Low"].to_numpy(), 3)]
        rngs = [float(c.mean()) for c in np.array_split(cons["range_pct"].to_numpy(), 3)]
        checks.append(_check("pass" if lows[0] <= lows[1] <= lows[2] else "fail", "低點墊高",
                             "／".join(f"{x:.2f}" for x in lows)))
        checks.append(_check("pass" if rngs[2] < rngs[0] else "fail", "波動收窄", f"振幅 {rngs[0]:.1f}% → {rngs[2]:.1f}%"))
    else:
        checks += [_check("pending", "低點墊高", "整理不足 6 日"), _check("pending", "波動收窄", "整理不足 6 日")]
    above = bool((cons["Close"] >= cons["sma50"]).all())
    rising = bool(d["sma20"].iloc[-1] > d["sma20"].iloc[-6] or d["sma50"].iloc[-1] > d["sma50"].iloc[-6])
    checks.append(_check("pass" if above and rising else "fail", "貼著向上均線",
                         f"收盤{'均' if above else '有跌破'}在 50 日線上；20／50 日線{'向上' if rising else '未向上'}"))
    if days_to_earnings is not None:
        near = days_to_earnings <= cfg["bo_earnings_block_days"]
        checks.append(_check("fail" if near else "pass", "距下次財報", f"{days_to_earnings} 天（≤{cfg['bo_earnings_block_days']} 天不買）"))

    # 買點＝橫盤區最高點；突破當天止損＝當天最低點（影片 B），尚未突破則以近 N 日最低點估算
    entry = max(float(last["Open"]), pivot) if broke else pivot
    stop = float(last["Low"]) if broke else float(cons["Low"].tail(cfg["stop_lookback_bars"]).min())
    risk = (entry - stop) / entry * 100
    grade = risk_grade_of(risk, adr, {**cfg, "risk_adr_max_mult": cfg["risk_adr_mult"]})  # 影片 B：止損寬度不能超過 ADR
    if broke:
        checks.append(_check("fail" if grade == "too_wide" else "pass", "止損距離", f"{risk:.1f}%（ADR {adr:.1f}%）"))

    has_fail = any(c["status"] == "fail" for c in checks)
    has_pending = any(c["status"] == "pending" for c in checks)
    stage = "watch" if has_fail or has_pending else ("breakout" if broke else "setup")
    return {**out, "stage": stage, "checks": checks, "peak_date": win.index[peak].strftime("%Y-%m-%d"),
            "runup_pct": round(runup, 0), "cons_days": n_c, "pivot": round(pivot, 2), "entry_price": round(entry, 2),
            "stop": round(stop, 2), "risk_pct": round(risk, 1), "risk_grade": grade,
            "dist_to_pivot_pct": round((pivot / float(last["Close"]) - 1) * 100, 1)}


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
