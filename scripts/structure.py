"""NFE Base 轉折路徑（逐行移植自 Pine v6 指標「NFE Base Logic Isolated」）。

做法：先把被前一根完全包住的內包 K 吸收掉，再用「種子（起點）＋候選（待確認轉折）」的狀態機，
依高低點先後、外包棒、回測、同側流動性等事件，產出一串高低交替的已確認轉折點。
本站用它找「突破前的前一個轉折低點」當止損。

與 Pine 的對應：時間（time）改用 K 棒在日線中的位置（整數）；na 改用 None；其餘變數名稱與分支順序保持一致，方便對照。
"""
from __future__ import annotations

import pandas as pd

# 候選極值遇到新 K 棒時的事件
NONE, SAME_SIDE, INSIDE, OUTSIDE, EXTEND, BREAK, RETRACE = range(7)
# 外包棒的後續走向
O_NONE, O_SWING_CONTINUE, O_REVERSE, O_EXTEND = range(4)


def better_turn(typ, value, old):
    return value > old if typ == 1 else value < old


def better_high(new_high, new_low, old_high, old_low):
    return old_high is None or new_high > old_high or (new_high == old_high and (old_low is None or new_low < old_low))


def better_low(new_low, new_high, old_low, old_high):
    return old_low is None or new_low < old_low or (new_low == old_low and (old_high is None or new_high > old_high))


def inside_k(hi, lo, ref_hi, ref_lo):
    return ref_hi is not None and ref_lo is not None and hi <= ref_hi and lo >= ref_lo


def valid_n(first_index, middle_index, confirm_index, first_time, middle_time, confirm_time):
    return middle_index >= first_index and confirm_index >= middle_index and middle_time > first_time and confirm_time > middle_time


def same_side_liquidity(ctype, hi, lo, cprice, cother):
    if ctype == 1:
        return hi == cprice and (cother is None or lo < cother)
    if ctype == -1:
        return lo == cprice and (cother is None or hi > cother)
    return False


def extends_candidate(ctype, hi, lo, cprice):
    return hi > cprice if ctype == 1 else (lo < cprice if ctype == -1 else False)


def breaks_candidate(ctype, hi, lo, cother):
    if ctype == 1:
        return cother is not None and lo < cother
    if ctype == -1:
        return cother is not None and hi > cother
    return False


def outside_candidate(ctype, hi, lo, cprice, cother):
    if ctype == 1:
        return cother is not None and hi > cprice and lo < cother
    if ctype == -1:
        return cother is not None and lo < cprice and hi > cother
    return False


def outside_resolution(ctype, hi, lo, outside_high, outside_low, seed_price, low_first):
    breaks_high = outside_high is not None and hi > outside_high
    breaks_low = outside_low is not None and lo < outside_low
    candidate_side = breaks_high if ctype == 1 else breaks_low
    reverse_side = breaks_low if ctype == 1 else breaks_high
    candidate_first = (not low_first) if ctype == 1 else low_first
    reverse_breaks_seed = (lo < seed_price) if ctype == 1 else (hi > seed_price)   # 反向側同時突破種子：結構已被改寫
    if candidate_side and reverse_side:
        return O_SWING_CONTINUE if candidate_first else (O_REVERSE if reverse_breaks_seed else O_EXTEND)
    if candidate_side:
        return O_SWING_CONTINUE
    if reverse_side:
        return O_REVERSE
    return O_NONE


def retrace_candidate(ctype, idx, cidx, hi, lo, prev_hi, prev_lo, cother, rprice, rreverse):
    if ctype == 1:
        return idx > cidx and prev_lo is not None and lo < prev_lo and (cother is None or lo > cother) and better_low(lo, hi, rprice, rreverse)
    if ctype == -1:
        return idx > cidx and prev_hi is not None and hi > prev_hi and (cother is None or hi < cother) and better_high(hi, lo, rprice, rreverse)
    return False


def confirms_retrace(ctype, hi, lo, cprice, cidx, ridx, idx, ctime, rtime, t):
    if ctype == 1:
        return hi > cprice and valid_n(cidx, ridx, idx, ctime, rtime, t)
    if ctype == -1:
        return lo < cprice and valid_n(cidx, ridx, idx, ctime, rtime, t)
    return False


def candidate_event(ctype, idx, seed_index, cidx, seed_time, ctime, t, hi, lo, prev_hi, prev_lo, cprice, cother, rprice, rreverse):
    # 判斷順序與 Pine 的 baseCandidateEvent 相同
    if outside_candidate(ctype, hi, lo, cprice, cother):
        return OUTSIDE
    if same_side_liquidity(ctype, hi, lo, cprice, cother):
        return SAME_SIDE
    if inside_k(hi, lo, prev_hi, prev_lo):
        return INSIDE
    if extends_candidate(ctype, hi, lo, cprice):
        return EXTEND
    if breaks_candidate(ctype, hi, lo, cother) and valid_n(seed_index, cidx, idx, seed_time, ctime, t):
        return BREAK
    if retrace_candidate(ctype, idx, cidx, hi, lo, prev_hi, prev_lo, cother, rprice, rreverse):
        return RETRACE
    return NONE


def normalize(times, low_firsts, highs, lows):
    """吸收內包 K：高低點完全落在前一根「保留」K 棒內的不保留。"""
    nt, nf, nh, nl = [], [], [], []
    for i in range(len(highs)):
        if not nh or highs[i] > nh[-1] or lows[i] < nl[-1]:
            nt.append(times[i]); nf.append(low_firsts[i]); nh.append(highs[i]); nl.append(lows[i])
    return nt, nf, nh, nl


def append_turn(turns, time, price, typ, reverse):
    """turns：[[時間, 價格, 類型(1 高／-1 低), 反向側價格], ...]。同類型只在更極端時更新最後一個，不同類型才新增。"""
    if time is None or price is None:
        return
    if not turns:
        turns.append([time, price, typ, reverse])
    elif turns[-1][2] == typ:
        if better_turn(typ, price, turns[-1][1]):
            turns[-1][:] = [time, price, typ, reverse]
    else:
        turns.append([time, price, typ, reverse])


class _State:
    """狀態機的全部變數（對應 Pine 函式裡的區域變數）。"""
    def __init__(self):
        self.initialized = False
        self.range_high = self.range_high_reverse = self.range_high_time = self.range_high_index = None
        self.range_low = self.range_low_reverse = self.range_low_time = self.range_low_index = None
        self.seed_type = 0
        self.seed_price = self.seed_reverse = self.seed_time = self.seed_index = None
        self.cand_type = 0
        self.cand_price = self.cand_other = self.cand_time = self.cand_index = None
        self.retrace_active = False
        self.retrace_price = self.retrace_reverse = self.retrace_time = self.retrace_index = None
        self.outside_active = False
        self.outside_high = self.outside_low = self.outside_time = self.outside_index = None

    def reset_retrace(self):
        self.retrace_active = False
        self.retrace_price = self.retrace_reverse = self.retrace_time = self.retrace_index = None

    def reset_outside(self):
        self.outside_active = False
        self.outside_high = self.outside_low = self.outside_time = self.outside_index = None

    def set_seed(self, typ, price, reverse, time, index):
        self.seed_type, self.seed_price, self.seed_reverse, self.seed_time, self.seed_index = typ, price, reverse, time, index

    def set_candidate(self, typ, price, other, time, index):
        self.cand_type, self.cand_price, self.cand_other, self.cand_time, self.cand_index = typ, price, other, time, index


def build_turns(times, low_firsts, highs, lows, append_pending=False, include_retrace=False):
    """回傳轉折點清單。append_pending=True 時，尾端尚未確認的候選極值也會輸出（Pine 內包K路徑用的同一個選項）。
    include_retrace=True 是本站在原指標上加的延伸：候選極值之後已出現、但還沒被確認的回測極值也輸出——
    例如高點後的整理低點，要等價格突破該高點時指標才會畫出來，但那正是「突破前的前一個轉折低點」。"""
    out: list[list] = []
    s = _State()
    seed_turn = lambda: append_turn(out, s.seed_time, s.seed_price, s.seed_type, s.seed_reverse)  # noqa: E731

    for i in range(len(highs)):
        t, low_first, hi, lo = times[i], low_firsts[i], highs[i], lows[i]
        prev_hi = highs[i - 1] if i > 0 else None
        prev_lo = lows[i - 1] if i > 0 else None
        inside_prev = inside_k(hi, lo, prev_hi, prev_lo)

        # ── 初始化：以首段高低點的先後順序決定種子（起點）與候選（待確認轉折）──
        if not s.initialized:
            if better_high(hi, lo, s.range_high, s.range_high_reverse):
                s.range_high, s.range_high_reverse, s.range_high_time, s.range_high_index = hi, lo, t, i
            if better_low(lo, hi, s.range_low, s.range_low_reverse):
                s.range_low, s.range_low_reverse, s.range_low_time, s.range_low_index = lo, hi, t, i
            if s.range_high_time is not None and s.range_low_time is not None:
                if s.range_high_time > s.range_low_time:
                    s.set_seed(-1, s.range_low, s.range_low_reverse, s.range_low_time, s.range_low_index)
                    s.set_candidate(1, s.range_high, s.range_high_reverse, s.range_high_time, s.range_high_index)
                    s.initialized = True
                elif s.range_low_time > s.range_high_time:
                    s.set_seed(1, s.range_high, s.range_high_reverse, s.range_high_time, s.range_high_index)
                    s.set_candidate(-1, s.range_low, s.range_low_reverse, s.range_low_time, s.range_low_index)
                    s.initialized = True

        # ── 候選為高點（種子為低點）──
        elif s.cand_type == 1:
            extends_seed = better_low(lo, hi, s.seed_price, s.seed_reverse)
            if s.cand_price is None:
                if extends_seed:
                    s.set_seed(s.seed_type, lo, hi, t, i)
                    if out:
                        seed_turn()
                elif i > s.seed_index and not inside_prev:
                    s.set_candidate(1, hi, lo, t, i)
                    s.reset_retrace()
            else:
                ev = candidate_event(1, i, s.seed_index, s.cand_index, s.seed_time, s.cand_time, t, hi, lo, prev_hi, prev_lo,
                                     s.cand_price, s.cand_other, s.retrace_price, s.retrace_reverse)
                if s.outside_active:
                    res = outside_resolution(1, hi, lo, s.outside_high, s.outside_low, s.seed_price, low_first)
                    if res == O_SWING_CONTINUE and valid_n(s.seed_index, s.cand_index, s.outside_index, s.seed_time, s.cand_time, s.outside_time) and t > s.outside_time:
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, 1, s.cand_other)
                        append_turn(out, s.outside_time, s.outside_low, -1, s.outside_high)
                        s.set_seed(-1, s.outside_low, s.outside_high, s.outside_time, s.outside_index)
                        s.set_candidate(1, hi, lo, t, i)
                        s.reset_outside(); s.reset_retrace()
                    elif res == O_REVERSE and valid_n(s.seed_index, s.outside_index, i, s.seed_time, s.outside_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.outside_time, s.outside_high, 1, s.outside_low)
                        s.set_seed(1, s.outside_high, s.outside_low, s.outside_time, s.outside_index)
                        s.set_candidate(-1, lo, hi, t, i)
                        s.reset_outside()
                    elif res == O_EXTEND:
                        # 本棒先破反向側、最後創新高：高點為最終極值，延伸候選，不提交外包棒舊高低點
                        s.set_candidate(s.cand_type, hi, lo, t, i)
                        s.reset_outside()
                elif ev == SAME_SIDE:
                    s.cand_other = lo
                    s.retrace_active, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index = True, lo, hi, t, i
                elif ev == INSIDE:
                    pass
                elif ev == OUTSIDE:
                    s.outside_active, s.outside_high, s.outside_low, s.outside_time, s.outside_index = True, hi, lo, t, i
                    s.reset_retrace()
                elif ev == EXTEND:
                    if s.retrace_active and confirms_retrace(1, hi, lo, s.cand_price, s.cand_index, s.retrace_index, i, s.cand_time, s.retrace_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, 1, s.cand_other)
                        append_turn(out, s.retrace_time, s.retrace_price, -1, s.retrace_reverse)
                        s.set_seed(-1, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index)
                    s.set_candidate(1, hi, lo, t, i)
                    s.reset_retrace()
                elif ev == BREAK:
                    if not out:
                        seed_turn()
                    append_turn(out, s.cand_time, s.cand_price, 1, s.cand_other)
                    s.set_seed(1, s.cand_price, s.cand_other, s.cand_time, s.cand_index)
                    s.set_candidate(-1, lo, hi, t, i)
                    s.reset_outside(); s.reset_retrace()
                elif extends_seed:
                    if valid_n(s.seed_index, s.cand_index, i, s.seed_time, s.cand_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, 1, s.cand_other)
                        s.set_seed(1, s.cand_price, s.cand_other, s.cand_time, s.cand_index)
                        s.set_candidate(-1, lo, hi, t, i)
                    else:
                        s.set_seed(s.seed_type, lo, hi, t, i)
                        s.cand_price = s.cand_other = s.cand_time = s.cand_index = None
                    s.reset_outside(); s.reset_retrace()
                    if out and s.cand_type == 1:
                        seed_turn()
                elif ev == RETRACE:
                    s.retrace_active, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index = True, lo, hi, t, i

        # ── 候選為低點（種子為高點）──
        else:
            extends_seed = better_high(hi, lo, s.seed_price, s.seed_reverse)
            if s.cand_price is None:
                if extends_seed:
                    s.set_seed(s.seed_type, hi, lo, t, i)
                    if out:
                        seed_turn()
                elif i > s.seed_index and not inside_prev:
                    s.set_candidate(-1, lo, hi, t, i)
                    s.reset_retrace()
            else:
                ev = candidate_event(-1, i, s.seed_index, s.cand_index, s.seed_time, s.cand_time, t, hi, lo, prev_hi, prev_lo,
                                     s.cand_price, s.cand_other, s.retrace_price, s.retrace_reverse)
                if s.outside_active:
                    res = outside_resolution(-1, hi, lo, s.outside_high, s.outside_low, s.seed_price, low_first)
                    if res == O_SWING_CONTINUE and valid_n(s.seed_index, s.cand_index, s.outside_index, s.seed_time, s.cand_time, s.outside_time) and t > s.outside_time:
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, -1, s.cand_other)
                        append_turn(out, s.outside_time, s.outside_high, 1, s.outside_low)
                        s.set_seed(1, s.outside_high, s.outside_low, s.outside_time, s.outside_index)
                        s.set_candidate(-1, lo, hi, t, i)
                        s.reset_outside(); s.reset_retrace()
                    elif res == O_REVERSE and valid_n(s.seed_index, s.outside_index, i, s.seed_time, s.outside_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.outside_time, s.outside_low, -1, s.outside_high)
                        s.set_seed(-1, s.outside_low, s.outside_high, s.outside_time, s.outside_index)
                        s.set_candidate(1, hi, lo, t, i)
                        s.reset_outside()
                    elif res == O_EXTEND:
                        s.set_candidate(s.cand_type, lo, hi, t, i)
                        s.reset_outside()
                elif ev == SAME_SIDE:
                    s.cand_other = hi
                    s.retrace_active, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index = True, hi, lo, t, i
                elif ev == INSIDE:
                    pass
                elif ev == OUTSIDE:
                    s.outside_active, s.outside_high, s.outside_low, s.outside_time, s.outside_index = True, hi, lo, t, i
                    s.reset_retrace()
                elif ev == EXTEND:
                    if s.retrace_active and confirms_retrace(-1, hi, lo, s.cand_price, s.cand_index, s.retrace_index, i, s.cand_time, s.retrace_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, -1, s.cand_other)
                        append_turn(out, s.retrace_time, s.retrace_price, 1, s.retrace_reverse)
                        s.set_seed(1, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index)
                    s.set_candidate(-1, lo, hi, t, i)
                    s.reset_retrace()
                elif ev == BREAK:
                    if not out:
                        seed_turn()
                    append_turn(out, s.cand_time, s.cand_price, -1, s.cand_other)
                    s.set_seed(-1, s.cand_price, s.cand_other, s.cand_time, s.cand_index)
                    s.set_candidate(1, hi, lo, t, i)
                    s.reset_outside(); s.reset_retrace()
                elif extends_seed:
                    if valid_n(s.seed_index, s.cand_index, i, s.seed_time, s.cand_time, t):
                        if not out:
                            seed_turn()
                        append_turn(out, s.cand_time, s.cand_price, -1, s.cand_other)
                        s.set_seed(-1, s.cand_price, s.cand_other, s.cand_time, s.cand_index)
                        s.set_candidate(1, hi, lo, t, i)
                    else:
                        s.set_seed(s.seed_type, hi, lo, t, i)
                        s.cand_price = s.cand_other = s.cand_time = s.cand_index = None
                    s.reset_outside(); s.reset_retrace()
                    if out and s.cand_type == -1:
                        seed_turn()
                elif ev == RETRACE:
                    s.retrace_active, s.retrace_price, s.retrace_reverse, s.retrace_time, s.retrace_index = True, hi, lo, t, i

    if append_pending:
        if not out and s.seed_type != 0:
            seed_turn()
        if s.cand_price is not None:
            append_turn(out, s.cand_time, s.cand_price, s.cand_type, s.cand_other)
            if include_retrace and s.retrace_active:
                append_turn(out, s.retrace_time, s.retrace_price, -s.cand_type, s.retrace_reverse)
    return out


INNER_MIN_BARS = 3   # 與 Pine 的 innerMinBars 相同：母 K 吸收的內包 K 至少幾根，才另外計算獨立路徑


def previous_swing_low(d: pd.DataFrame, end: int, min_pos: int = 0) -> tuple[float, int] | None:
    """日線 d 的前 end 根（不含第 end 根，也就是不含突破那根）裡，位置 ≥ min_pos 的最後一個「低點轉折」。

    指標有兩層路徑：
      ① 紅色基礎路徑：內包 K 吸收後的 K 棒所形成的轉折；
      ② 青色內包 K 路徑：最後一根母 K 之後被它吸收的內包 K，各自再算一次轉折。
    跳空大漲那天的大 K 棒會把整個整理期當成內包 K 吸收，整理期的轉折低點只會出現在 ②，所以兩層都要看，取時間最近的低點。
    尾端尚未確認的候選與回測低點也算（突破那一根就是它的確認）。回傳 (價格, 在 d 中的位置)；找不到回傳 None。"""
    seg = d.iloc[:end]
    if len(seg) < 3:
        return None
    times = list(range(len(seg)))
    low_firsts = (seg["Close"] >= seg["Open"]).tolist()      # 與 Pine 相同：收 ≥ 開視為先低後高
    highs, lows = seg["High"].tolist(), seg["Low"].tolist()

    nt, nf, nh, nl = normalize(times, low_firsts, highs, lows)
    found = [(t, p) for t, p, typ, _ in build_turns(nt, nf, nh, nl, True, True) if typ == -1 and t >= min_pos]

    mother = nt[-1]                                           # 最後一根保留下來的母 K；它之後的 K 棒全是被吸收的內包 K
    if len(seg) - 1 - mother >= INNER_MIN_BARS:
        it, iflag, ih, il = normalize(times[mother + 1:], low_firsts[mother + 1:], highs[mother + 1:], lows[mother + 1:])
        found += [(t, p) for t, p, typ, _ in build_turns(it, iflag, ih, il, True, True) if typ == -1 and t >= min_pos]
    if not found:
        return None
    time, price = max(found)                                  # 時間最近的低點轉折
    return float(price), int(time)
