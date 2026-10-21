"""structure.py（NFE Base 轉折路徑移植）測試。
注意：這裡驗證的是「移植沒有抄錯、行為符合預期」，不是與 TradingView 逐點比對；
正式使用前請在 TradingView 上對幾檔實際標的核對轉折點。"""
import random

import pandas as pd

from structure import build_turns, normalize, previous_swing_low


def frame_from_closes(closes, spread=0.5):
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame({"Open": opens, "High": [max(o, c) + spread for o, c in zip(opens, closes)],
                         "Low": [min(o, c) - spread for o, c in zip(opens, closes)], "Close": closes, "Volume": 1e6}, index=idx)


def turns_of(d, pending=False):
    t = list(range(len(d)))
    nt, nf, nh, nl = normalize(t, (d["Close"] >= d["Open"]).tolist(), d["High"].tolist(), d["Low"].tolist())
    return build_turns(nt, nf, nh, nl, pending)


def test_zigzag_turns_alternate_and_hit_extremes():
    closes = [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 11, 12, 13, 14, 15, 16, 15, 14, 13, 12.5, 13, 14]
    d = frame_from_closes([float(c) for c in closes])
    turns = turns_of(d, pending=True)
    types = [t[2] for t in turns]
    assert all(a != b for a, b in zip(types, types[1:])), types                   # 高低交替
    prices = {t[0]: t[1] for t in turns}
    assert prices[5] == d["High"].iloc[5] and prices[10] == d["Low"].iloc[10] and prices[16] == d["High"].iloc[16], prices


def test_previous_swing_low_is_the_last_pullback_low():
    # 低 → 高 → 回測低（9.5 附近）→ 準備突破：突破前的前一個轉折低點就是回測低點
    closes = [float(c) for c in [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 11, 12, 13, 14, 14.5]]
    d = frame_from_closes(closes)
    price, pos = previous_swing_low(d, len(d))
    assert pos == 10 and price == d["Low"].iloc[10], (price, pos)
    # 限制只看某位置之後的轉折（例如反應日之後）：10 之前的低點不能被採用
    assert previous_swing_low(d, len(d), min_pos=11) is None


def test_inside_bars_are_absorbed():
    d = frame_from_closes([10.0, 12.0, 11.0, 11.2, 11.1, 13.0], spread=0.1)
    t = list(range(len(d)))
    nt, *_ = normalize(t, [True] * 6, d["High"].tolist(), d["Low"].tolist())
    assert len(nt) < len(d)                                                        # 被前一根完全包住的 K 棒不保留


def test_mirror_symmetry_catches_one_sided_typos():
    """價格上下翻轉（高低互換、先後順序對調）後，轉折點應完全對稱。高點分支與低點分支若有一邊抄錯，這裡會失敗。"""
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randint(8, 70)
        price, hi, lo, lf = 100.0, [], [], []
        for _ in range(n):
            o = price
            price = max(o + rng.uniform(-3, 3), 1)
            h, l = max(o, price) + rng.uniform(0, 2), min(o, price) - rng.uniform(0, 2)
            hi.append(round(h, 2)); lo.append(round(l, 2)); lf.append(rng.random() < .5)
        t = list(range(n))
        for pending, retrace in ((False, False), (True, False), (True, True)):
            a = build_turns(*normalize(t, lf, hi, lo), pending, retrace)
            b = build_turns(*normalize(t, [not f for f in lf], [-x for x in lo], [-x for x in hi]), pending, retrace)
            assert [(x[0], x[1], x[2], x[3]) for x in a] == [(y[0], -y[1], -y[2], -y[3]) for y in b], (a, b)


def test_consolidation_inside_a_gap_bar_uses_the_inner_bar_path():
    """跳空大漲那天的大 K 棒（範圍 100～122）把整理期全部吸收成內包 K；整理期的轉折低點只在內包 K 的獨立路徑裡。
    內包路徑：高 119 → 低 110 → 高 121 → 低 113 → 高 122（尚未確認）。突破前最近的轉折低點是 113。"""
    pre = [(100.0, 101.5, 98.5, 100.0)] * 5
    rows = pre + [(118, 122, 100, 120), (118, 119, 112, 113), (113, 115, 110, 114), (114, 119, 113, 118),
                  (118, 121, 116, 120), (120, 121, 114, 115), (115, 118, 113, 117), (117, 121, 116, 120), (120, 122, 118, 121)]
    d = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=pd.bdate_range("2026-01-01", periods=len(rows)))
    d["Volume"] = 1e6
    assert previous_swing_low(d, len(d), min_pos=6) == (113.0, 11)
    assert previous_swing_low(d, 9, min_pos=6) == (110.0, 7)             # 只看到位置 8 為止：當時最近的低點是 110
    assert previous_swing_low(d, 11, min_pos=6) == (114.0, 10)            # 再多看兩根：低點更新為 114（尚未確認的候選低點）
    assert previous_swing_low(d, len(d), min_pos=12) is None              # 只採用更晚的轉折就沒有
    assert previous_swing_low(d, len(d), min_pos=0)[1] >= 6               # 沒限制時，最近的低點也在整理期而不是跳空前


def test_degenerate_inputs():
    d = frame_from_closes([10.0, 10.0])
    assert previous_swing_low(d, 2) is None
    assert build_turns([], [], [], [], True) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
