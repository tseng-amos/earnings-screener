"use strict";
/* 美股突破篩選簿：前端只讀 data/screen.json（由 Python 每日產生），所有選股判斷都在後端。
   版面：左側清單、右側詳情（窄螢幕改成底部抽屜）。使用者設定與人工確認只存在本機瀏覽器。 */
(() => {
  // ───────── 工具 ─────────
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));
  const pct = (v, d = 1, sign = false) => (v == null ? "—" : `${sign && v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
  const usd = (v) => (v == null ? "—" : "$" + Math.round(v).toLocaleString("en-US"));
  const md = (iso) => (iso ? iso.slice(5).replace("-", "/") : "—");
  const tv = (sym) => `https://www.tradingview.com/symbols/${encodeURIComponent(String(sym).replace("-", "."))}/`;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  };

  // ───────── 狀態 ─────────
  const DEFAULTS = { account: 100000, riskPct: 0.5, maxPosPct: 0, theme: "auto", color: "cn" };
  const S = {
    cfg: null, data: null, tab: "pead", sel: {}, composing: false,
    ui: LS.get("pb.ui", {}),
    set: { ...DEFAULTS, ...LS.get("pb.settings", {}) },
    notes: LS.get("pb.notes", {}),
  };
  const ui = (lane) => (S.ui[lane] ||= { stage: null, sort: null, dir: -1, q: "" });
  const weak = () => S.data?.regime?.status === "weak";
  const narrow = () => matchMedia("(max-width: 1020px)").matches;
  const saveUi = () => LS.set("pb.ui", S.ui);

  function applyLook() {
    const r = document.documentElement;
    if (S.set.theme === "auto") r.removeAttribute("data-theme"); else r.setAttribute("data-theme", S.set.theme);
    r.setAttribute("data-color", S.set.color);
  }

  // ───────── 階段與顯示 ─────────
  const STAGE_LABEL = { breakout: "突破中", consolidating: "待突破", setup: "待突破", watch: "觀察", extended: "突破已逾期", removed: "失效" };
  const HOLD_LABEL = { hold: "續抱", partial: "該分批", sell: "該賣出", watch: "留意", nodata: "無資料" };
  const RISK_LABEL = { ideal: "≤1×ADR，理想", acceptable: "≤1.5×ADR，可接受", too_wide: ">1.5×ADR，不建議" };
  const LANES = {
    pead: {
      stages: ["breakout", "consolidating", "watch", "extended", "removed"], items: () => S.data.pead,
      id: (c) => `${c.symbol}|${c.reaction_date}`, noteKey: (c) => `pead:${c.symbol}:${c.reaction_date}`,
      hints: {
        breakout: "價格已越過整理區高點。影片：越過就買、止損放突破前最後幾天最低點；日線看不到盤中，所以標出「收盤是否站上」。",
        consolidating: "五項整理條件合格，等價格越過買點。影片：整理中不要先買，開盤前設好價格提醒。",
        watch: "財報後大漲且爆量，但整理還不夠久或有條件未過。逐檔展開看哪一項未通過。",
        extended: "突破已經一段時間。影片的規則是不追，等下一次整理。",
        removed: "收盤跌破財報大漲當天的最低點，影片規則：移出觀察名單。",
      },
    },
    breakout: {
      stages: ["breakout", "setup"], items: () => S.data.breakout,
      id: (c) => c.symbol, noteKey: (c) => `breakout:${c.symbol}`,
      hints: {
        breakout: "動能前段的標的剛越過橫盤上沿（最近幾日內第一次）。買點與止損固定在突破當天，不會隨後續K線漂移。",
        setup: "形態條件合格，還沒越過橫盤上沿。影片：不要提前買，盯著買點，開盤前設價格提醒。",
      },
    },
  };

  function priority(lane, c) {
    let p = lane === "pead" ? { breakout: 100, consolidating: 80, watch: 30, extended: 20, removed: 0 }[c.stage] : { breakout: 100, setup: 80 }[c.stage];
    if (lane === "pead") p += (c.ep_score || 0) * 2 + (c.close_confirmed ? 4 : 0);
    else p += (c.momentum_rank || 0) / 20 - (c.stage === "setup" ? Math.max(0, c.dist_to_pivot_pct || 0) / 2 : 0);
    if (c.stage === "breakout" && weak()) p -= 45; // 大盤弱勢：突破排到待突破之後
    return p;
  }

  const daysTo = (iso) => (iso ? Math.round((new Date(iso + "T00:00:00Z") - new Date(S.data.asof + "T00:00:00Z")) / 864e5) : null);
  const changeTag = (c) => (c.change === "new" ? '<span class="tag new">新</span>' : c.change === "stage" ? `<span class="tag adv" title="昨天：${esc(STAGE_LABEL[c.prev_stage])}">升級</span>` : "");
  function stageCell(c) {
    let extra = "";
    if (c.stage === "breakout") extra = `第${(c.days_since_breakout ?? 0) + 1}日·${c.close_confirmed === false ? "收盤未站上" : "收盤站上"}`;
    if (c.stage === "breakout" && weak()) extra += "·大盤弱";
    return `<span class="stage s-${c.stage}">${STAGE_LABEL[c.stage]}${extra ? ` <small>${extra}</small>` : ""}</span>`;
  }
  const riskCell = (c) => (c.risk_pct == null ? "—" : `<span class="${c.risk_grade === "too_wide" ? "neg" : ""}">${num(c.risk_pct, 1)}%</span><small class="muted"> /${num(c.adr_pct, 1)}</small>`);
  const distCell = (c) => (c.dist_to_pivot_pct == null ? "—" : c.dist_to_pivot_pct <= 0 ? "已越過" : `+${num(c.dist_to_pivot_pct, 1)}%`);
  const symCell = (c) => `<span class="sym">${esc(c.symbol)}</span>${changeTag(c)}<span class="nm">${esc(c.name || "")}</span>`;

  const COLS = {
    pead: [
      { k: "symbol", label: "代號", render: symCell, val: (c) => c.symbol, dir: 1 },
      { k: "stage", label: "階段", render: stageCell, val: (c) => priority("pead", c) },
      { k: "reaction_date", label: "反應日", cls: "lo", render: (c) => `${md(c.reaction_date)} <small class="muted">${esc(c.earnings_time || "")}</small>`, val: (c) => c.reaction_date },
      { k: "gain_pct", label: "漲幅", num: 1, render: (c) => `<span class="up">${pct(c.gain_pct, 1, true)}</span>`, val: (c) => c.gain_pct },
      { k: "volume_ratio", label: "量", num: 1, cls: "lo", render: (c) => `${num(c.volume_ratio, 1)}×`, val: (c) => c.volume_ratio },
      { k: "ep", label: "EP", num: 1, render: (c) => `${c.ep_score ?? "—"}/5${c.ep_unknown ? `<small class="muted"> ?${c.ep_unknown}</small>` : ""}`, val: (c) => c.ep_score },
      { k: "pivot", label: "買點", num: 1, cls: "lo", render: (c) => num(c.pivot), val: (c) => c.pivot },
      { k: "stop", label: "止損", num: 1, cls: "lo", render: (c) => num(c.stop), val: (c) => c.stop },
      { k: "risk", label: "風險／ADR", num: 1, render: riskCell, val: (c) => c.risk_pct, dir: 1 },
      { k: "dist", label: "距買點", num: 1, render: distCell, val: (c) => c.dist_to_pivot_pct, dir: 1 },
    ],
    breakout: [
      { k: "symbol", label: "代號", render: symCell, val: (c) => c.symbol, dir: 1 },
      { k: "stage", label: "階段", render: stageCell, val: (c) => priority("breakout", c) },
      { k: "momentum_rank", label: "動能", num: 1, render: (c) => num(c.momentum_rank, 1), val: (c) => c.momentum_rank },
      { k: "runup_pct", label: "前段漲幅", num: 1, cls: "lo", render: (c) => `<span class="up">${pct(c.runup_pct, 0, true)}</span>`, val: (c) => c.runup_pct },
      { k: "cons_days", label: "整理日", num: 1, cls: "lo", render: (c) => c.cons_days, val: (c) => c.cons_days },
      { k: "adr_pct", label: "ADR", num: 1, cls: "lo", render: (c) => pct(c.adr_pct, 1), val: (c) => c.adr_pct },
      { k: "pivot", label: "買點", num: 1, render: (c) => num(c.pivot), val: (c) => c.pivot },
      { k: "stop", label: "止損", num: 1, cls: "lo", render: (c) => num(c.stop), val: (c) => c.stop },
      { k: "risk", label: "風險", num: 1, render: riskCell, val: (c) => c.risk_pct, dir: 1 },
      { k: "dist", label: "距買點", num: 1, render: distCell, val: (c) => c.dist_to_pivot_pct, dir: 1 },
      { k: "earn", label: "距財報", num: 1, cls: "lo", render: (c) => (c.next_earnings ? `${daysTo(c.next_earnings)}天` : '<span class="muted">未知</span>'), val: (c) => daysTo(c.next_earnings) },
    ],
    hold: [
      { k: "symbol", label: "代號", render: (c) => `<span class="sym">${esc(c.symbol)}</span><span class="nm">${esc(c.note || "")}</span>`, val: (c) => c.symbol, dir: 1 },
      { k: "stage", label: "狀態", render: (c) => `<span class="stage s-${c.status}">${HOLD_LABEL[c.status]}</span>`, val: (c) => ({ sell: 4, partial: 3, watch: 2, hold: 1, nodata: 0 }[c.status]) },
      { k: "held_days", label: "持有", num: 1, render: (c) => (c.held_days == null ? "—" : `${c.held_days}天`), val: (c) => c.held_days },
      { k: "pnl_pct", label: "損益", num: 1, render: (c) => (c.pnl_pct == null ? "—" : `<span class="${c.pnl_pct >= 0 ? "up" : "down"}">${pct(c.pnl_pct, 1, true)}</span>`), val: (c) => c.pnl_pct },
      { k: "last_close", label: "收盤", num: 1, cls: "lo", render: (c) => num(c.last_close), val: (c) => c.last_close },
      { k: "stop", label: "止損", num: 1, cls: "lo", render: (c) => num(c.stop), val: (c) => c.stop },
      { k: "ma", label: "跟蹤均線", num: 1, cls: "lo", render: (c) => (c.exit_ma ? `${c.exit_ma}日 ${num(c.ma_value)}` : "—"), val: (c) => c.ma_value },
      { k: "earn", label: "距財報", num: 1, cls: "lo", render: (c) => (c.days_to_earnings == null ? '<span class="muted">未知</span>' : `${c.days_to_earnings}天`), val: (c) => c.days_to_earnings },
    ],
  };

  // ───────── 頁首：大盤與資料狀態 ─────────
  function renderRegime() {
    const r = S.data.regime || {}, st = r.status || "unknown", h = S.data.health || {};
    const label = { strong: "大盤強", caution: "大盤要小心", weak: "大盤弱勢", unknown: "大盤未知" }[st];
    // 影片：看那斯達克 10、20 日線。三個條件逐一列出，哪一項沒過一眼就看得出來（「向上」＝比 3 個交易日前高）
    const conds = st === "unknown" ? [] : [
      [r.ma10_above_ma20, "10 日線在 20 日線之上", "10 日線低於 20 日線"],
      [r.ma10_up, "10 日線向上", "10 日線未向上（走平或下彎）"],
      [r.ma20_up, "20 日線向上", "20 日線未向上（走平或下彎）"],
    ];
    const fails = conds.filter((c) => !c[0]).map((c) => c[2]);
    const advice = {
      strong: "三項都過：正常做。",
      caution: `${fails.join("、")}：縮小倉位，只做最好的。`,
      weak: "三項都沒過：突破容易失敗，少做小做；EP 只用小倉位試單。",
      unknown: "取不到大盤資料，請自行確認再下單。",
    }[st];
    const idx = String(r.index || "").startsWith("QQQ") ? `${r.index}（替代 ^IXIC）` : (r.index === "^IXIC" ? "那斯達克綜合" : r.index || "");
    const age = (Date.now() - new Date(S.data.asof + "T00:00:00Z")) / 864e5;
    const alerts = [];
    if (age > 5) alerts.push(`價格已 ${Math.floor(age)} 天沒更新，請先到 Actions 檢查`);
    if (h.price_success_pct != null && h.price_success_pct < 90) alerts.push(`價格成功率 ${h.price_success_pct}%，部分標的缺資料`);
    if (h.calendar_failed_days) alerts.push(`財報日曆有 ${h.calendar_failed_days} 天抓取失敗`);
    if (h.universe_source === "events_only") alerts.push("股票清單取得失敗，動能排名只含有財報的標的");
    const gen = new Date(S.data.generated_at).toLocaleString("zh-TW", { hour12: false });
    const values = r.ma10 != null ? `（10 日線 ${num(r.ma10)}、20 日線 ${num(r.ma20)}）` : "";
    $("#regime").className = `regime ${st}`;
    $("#regime").innerHTML = `<b>${label}</b>（${esc(idx)}）　${esc(advice)}
      ${conds.length ? `<span class="conds">${conds.map((c) => `<span class="cond ${c[0] ? "pass" : "fail"}">${c[0] ? c[1] : c[2]}</span>`).join("")}</span>` : ""}
      <span class="meta">價格截至 ${esc(S.data.asof)} 美東收盤${values} · 更新於 ${esc(gen)}</span>
      ${alerts.map((a) => `<span class="meta alert">⚠ ${esc(a)}</span>`).join("")}`;
  }

  // ───────── 清單（左）與詳情（右）─────────
  function buildLane(lane) {
    $(`#tab-${lane}`).innerHTML = `
      <p class="lede" id="lede-${lane}"></p>
      <div class="toolbar">
        <div class="stages" id="stages-${lane}"></div>
        <input type="search" id="q-${lane}" placeholder="搜尋代號或公司" aria-label="搜尋">
        <span class="spacer"></span>
        <button class="btn" data-act="copy" data-lane="${lane}" title="貼進 TradingView 自選股的匯入欄">複製自選股</button>
        <button class="btn" data-act="csv" data-lane="${lane}" title="買點、止損與建議股數，拿去設價格提醒">下載提醒價 CSV</button>
      </div>
      <p class="lede muted" id="hint-${lane}" style="font-size:13.5px"></p>
      <div class="workspace">
        <div class="tablewrap"><table class="list" id="list-${lane}"></table></div>
        <aside class="detail" id="detail-${lane}" aria-live="polite"></aside>
      </div>`;
    $(`#q-${lane}`).value = ui(lane).q || "";
  }

  function visibleItems(lane) {
    const u = ui(lane), q = (u.q || "").trim().toLowerCase();
    let items = LANES[lane].items().filter((c) => c.stage === u.stage);
    if (q) items = items.filter((c) => `${c.symbol} ${c.name || ""}`.toLowerCase().includes(q));
    const col = COLS[lane].find((x) => x.k === u.sort) || COLS[lane][1];
    const dir = u.sort ? u.dir : -1;
    return items.sort((a, b) => {
      const x = col.val(a), y = col.val(b);
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
  }

  function renderLane(lane) {
    const L = LANES[lane], u = ui(lane), all = L.items();
    if (!L.stages.includes(u.stage)) u.stage = L.stages[0];
    const count = (s) => all.filter((c) => c.stage === s).length;
    $(`#stages-${lane}`).innerHTML = L.stages.map((s) =>
      `<button class="s-${s}" style="--c:var(--${s === "breakout" ? "hot" : s === "consolidating" || s === "setup" ? "amber" : "muted"})" data-stage="${s}" aria-pressed="${u.stage === s}">${STAGE_LABEL[s]}<span class="n">${count(s)}</span></button>`).join("");
    $(`#hint-${lane}`).textContent = L.hints[u.stage] || "";
    renderLede(lane);

    const rows = visibleItems(lane);
    if (!rows.some((c) => L.id(c) === S.sel[lane])) S.sel[lane] = rows[0] ? L.id(rows[0]) : null;
    const cols = COLS[lane];
    $(`#list-${lane}`).innerHTML = `<thead><tr>${cols.map((c) => `<th data-k="${c.k}" class="${c.num ? "num" : ""} ${c.cls || ""}" aria-sort="${u.sort === c.k ? (u.dir > 0 ? "ascending" : "descending") : "none"}">${c.label}${u.sort === c.k ? (u.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((c) => `<tr class="row s-${c.stage} ${L.id(c) === S.sel[lane] ? "sel" : ""} ${(c.stage === "breakout" && weak()) || c.stage === "removed" || c.stage === "extended" ? "dim" : ""}" tabindex="0" data-id="${esc(L.id(c))}">${cols.map((x) => `<td class="${x.num ? "num" : ""} ${x.cls || ""}">${x.render(c)}</td>`).join("")}</tr>`).join("") ||
        `<tr class="empty"><td colspan="${cols.length}">這個階段目前沒有標的。</td></tr>`}</tbody>`;
    renderDetail(lane);
  }

  function renderLede(lane) {
    const L = LANES[lane], all = L.items(), D = S.data, sum = D.summary || {};
    const n = (s) => all.filter((c) => c.stage === s).length;
    const lead = lane === "pead"
      ? `財報後大漲且爆量的標的，等整理收斂、向上突破才買。目前 <b>突破中 ${n("breakout")}</b> 檔、<b>待突破 ${n("consolidating")}</b> 檔、觀察 ${n("watch")} 檔。`
      : `全市場 1／3／6 個月漲幅排在最前 ${S.cfg.momentum_top_pct}% 的標的，大漲後橫盤、等突破。目前 <b>剛突破 ${n("breakout")}</b> 檔、<b>待突破 ${n("setup")}</b> 檔（共掃出 ${D.breakout_total} 檔，只列動能最強的 ${all.length} 檔）。`;
    const chg = all.filter((c) => c.change === "new").length, adv = all.filter((c) => c.change === "stage").length;
    const dropped = (D.dropped || []).filter((d) => d.lane === lane);
    const diff = !sum.has_prev ? "（尚無上次資料可比較）"
      : `較上次更新：新增 ${chg} 檔、階段前進 ${adv} 檔、移出 ${dropped.length} 檔${dropped.length ? `（${dropped.slice(0, 8).map((d) => `${esc(d.symbol)}`).join("、")}${dropped.length > 8 ? "…" : ""}）` : ""}。`;
    $(`#lede-${lane}`).innerHTML = `${lead}<span class="src">${diff}</span>`;
  }

  function checksHtml(title, checks) {
    if (!checks?.length) return "";
    return `<h3>${title}</h3><ul class="checks">${checks.map((k) => `<li><span class="mk ${k.status}" title="${{ pass: "合格", warn: "提醒", fail: "不合格", unknown: "需人工確認", pending: "待觀察" }[k.status]}"></span><span>${esc(k.label)}</span><span class="d">${esc(k.detail)}</span></li>`).join("")}</ul>`;
  }

  function verdict(lane, c) {
    if (c.stage === "removed") return `收盤已在 ${md(c.removed_on)} 跌破反應日最低點 ${num(c.reaction_low)}，依影片規則移出觀察。`;
    if (c.stage === "breakout") {
      const when = c.days_since_breakout === 0 ? "今天" : `${md(c.breakout_date)}（突破後第 ${c.days_since_breakout + 1} 日）`;
      const close = c.close_confirmed === false ? "盤中越過、收盤回到買點下方，小心假突破" : "收盤站上買點";
      return `${when}越過整理區高點 ${num(c.pivot)}：${close}；最新收盤${c.holding ? "仍在" : "已低於"}買點之上。${weak() ? "大盤弱勢，影片：突破單容易失敗，少做小做。" : ""}`;
    }
    if (c.stage === "consolidating" || c.stage === "setup") return `${lane === "pead" ? "整理五項合格" : "形態條件合格"}，等價格越過 ${num(c.pivot)}（還差 ${pct(c.dist_to_pivot_pct)}）。開盤前先設好價格提醒。`;
    if (c.stage === "extended") return `突破發生在 ${md(c.breakout_date)}，已超過 ${S.cfg.breakout_fresh_bars} 日，影片規則不追。`;
    const fails = (c.checks || []).filter((k) => k.status === "fail").map((k) => k.label), pend = (c.checks || []).filter((k) => k.status === "pending").map((k) => k.label);
    return `尚未合格：${fails.join("、") || "—"}${pend.length ? `；待觀察：${pend.join("、")}` : ""}。`;
  }

  function sizingHtml(c) {
    const cfg = S.cfg, st = S.set;
    const entry = c.entry_price ?? c.pivot, maxPos = st.maxPosPct || Sizing.defaultMaxPosPct(st.account, cfg);
    const p = Sizing.position({ account: st.account, riskPct: st.riskPct, entry, stop: c.stop, maxPosPct: maxPos });
    if (!p) return "";
    const warns = [];
    if (p.capped) warns.push(`受單檔上限 ${maxPos}% 限制，實際只冒帳戶 ${num(p.lossPct, 2)}% 的風險。`);
    if (c.risk_grade === "too_wide") warns.push("止損距離超過 1.5 倍 ADR：影片建議不要買，或等整理再收斂。");
    if (p.shares === 0) warns.push("算出 0 股：止損離買點太遠或單筆風險設太低。");
    return `<h3>倉位試算（影片 B）</h3><div class="sizing">以帳戶 ${usd(st.account)}、單筆風險 ${st.riskPct}% 計，
      ${c.entry_price ? "進場價" : "以買點估算"} ${num(entry)}、止損 ${num(c.stop)}（${num(p.stopPct, 1)}%）：
      買 <b>${p.shares.toLocaleString()}</b> 股，約 ${usd(p.cost)}（佔帳戶 ${num(p.posPct, 1)}%），止損觸發約虧 ${usd(p.lossUsd)}。
      ${warns.map((w) => `<span class="warn">${esc(w)}</span>`).join("")}
      <span class="warn" style="color:var(--muted)">影片：最多同時持有 ${cfg.sizing_max_positions} 檔、不用槓桿；一次買足，不加碼攤平。</span></div>`;
  }

  const MANUAL = {
    pead: [["rev", "營收超預期"], ["guide", "指引上調／偏正面"], ["metric", "這家公司最被盯的數字過關"], ["call", "電話會逐字稿沒有雷"], ["gaap", "EPS 口徑一致（GAAP 對 GAAP）"]],
    breakout: [["event", "確認離下次財報超過 3 天"], ["theme", "同族群有其他股票一起走強"], ["alert", "已設好開盤前價格提醒"]],
  };
  function manualHtml(lane, c) {
    const key = LANES[lane].noteKey(c), n = S.notes[key] || {};
    return `<h3>人工確認與筆記（只存在這個瀏覽器）</h3><div class="manual" data-key="${esc(key)}">
      ${MANUAL[lane].map(([k, t]) => `<label><input type="checkbox" data-f="${k}" ${n[k] ? "checked" : ""}> ${t}</label>`).join("")}
      <textarea data-f="note" placeholder="筆記：想買的理由、要盯的價位…" aria-label="筆記">${esc(n.note || "")}</textarea></div>`;
  }

  function levelsHtml(lane, c) {
    const row = (a, b) => `<dt>${a}</dt><dd>${b}</dd>`;
    const common = row("買點", num(c.pivot)) + row("止損", num(c.stop)) + row("風險", c.risk_pct == null ? "—" : `${num(c.risk_pct, 1)}%`) + row("平時日波動", pct(c.adr_pct, 1));
    const own = lane === "pead"
      ? row("進場價", c.entry_price == null ? "—" : num(c.entry_price)) + row("評級", c.risk_grade ? RISK_LABEL[c.risk_grade] : "—") + row("反應日低點", num(c.reaction_low)) + row("最大回落", pct(c.max_pullback_pct, 1)) + row("整理天數", `${c.cons_days ?? 0} 日`) + row("下次財報", c.next_earnings ? `${md(c.next_earnings)}（${daysTo(c.next_earnings)}天）` : "未知")
      : row("進場價", c.entry_price == null ? "—" : num(c.entry_price)) + row("評級", c.risk_grade ? RISK_LABEL[c.risk_grade] : "—") + row("動能 1／3／6月", `${num(c.rank_1m, 0)}／${num(c.rank_3m, 0)}／${num(c.rank_6m, 0)}`) + row("前段漲幅", pct(c.runup_pct, 0)) + row("整理天數", `${c.cons_days} 日`) + row("下次財報", c.next_earnings ? `${md(c.next_earnings)}（${daysTo(c.next_earnings)}天）` : "未知");
    return `<h3>價位</h3><dl class="levels">${common}${own}</dl>`;
  }

  function chartHtml(c, lane) {
    if (!c.chart) return `<p class="muted" style="font-size:13px">這檔沒有附圖（每條線只附前 ${S.cfg.max_chart_items} 檔），請用上方連結看圖。</p>`;
    const levels = [{ y: c.pivot, label: "買點", cls: "pivot" }, { y: c.stop, label: "止損", cls: "stop" }];
    if (lane === "pead") levels.push({ y: c.reaction_low, label: "反應日低", cls: "low" });
    const marks = lane === "pead" ? [{ date: c.reaction_date, label: "財報反應日" }] : [{ date: c.peak_date, label: "前高" }];
    if (c.breakout_date) marks.push({ date: c.breakout_date, label: "突破" });
    return `<div class="chartbox">${Chart2.candleSvg(c.chart, { levels, marks, label: `${c.symbol} 近期日線` })}</div>
      <div class="legend"><span><i style="color:var(--accent)"></i>50 日線</span><span><i style="color:var(--hot)"></i>買點</span><span><i class="stop-i"></i>止損</span><span>量能在下方</span></div>`;
  }

  function renderDetail(lane) {
    const L = LANES[lane], el = $(`#detail-${lane}`), c = L.items().find((x) => L.id(x) === S.sel[lane]);
    if (!c) { el.innerHTML = '<p class="muted">左邊選一檔看詳情。</p>'; return; }
    const keys = S.cfg.key_metrics?.[c.symbol];
    const keyHtml = lane === "pead" && keys ? `<h3>這家公司最被盯的數字（影片 A）</h3><p class="keymetrics">${keys.map(esc).join("、")}</p>` : "";
    el.style.setProperty("--c", `var(--${c.stage === "breakout" ? "hot" : c.stage === "consolidating" || c.stage === "setup" ? "amber" : "muted"})`);
    el.innerHTML = `<button class="btn sheet-close" data-act="close">關閉</button>
      <h2>${esc(c.symbol)} ${changeTag(c)}</h2>
      <div class="sub">${esc(c.name || "")} · <a href="${tv(c.symbol)}" target="_blank" rel="noopener">TradingView 圖表 ↗</a>${lane === "pead" ? ` · 財報 ${md(c.earnings_date)} ${esc(c.earnings_time || "")}` : ""}</div>
      <p class="verdict">${verdict(lane, c)}</p>
      ${chartHtml(c, lane)}${levelsHtml(lane, c)}${sizingHtml(c)}
      ${lane === "pead" ? checksHtml("整理條件（影片 A）", c.checks) + checksHtml("EP 品質（影片 B）", c.ep_checks) : checksHtml("形態條件（影片 B）", c.checks)}
      ${keyHtml}${manualHtml(lane, c)}`;
  }

  // ───────── 持倉 ─────────
  function ghEditUrl() {
    const m = location.hostname.match(/^(.+)\.github\.io$/), repo = location.pathname.split("/")[1];
    return m && repo ? `https://github.com/${m[1]}/${repo}/edit/main/data/holdings.csv` : null;
  }
  function buildHold() {
    const url = ghEditUrl();
    $("#tab-hold").innerHTML = `
      <p class="lede">持倉不存在這個網頁裡：把買進的資料寫在 repo 的 <code>data/holdings.csv</code>，每次更新時一起算賣出提示。
        ${url ? `<a href="${url}" target="_blank" rel="noopener">直接在 GitHub 編輯 ↗</a>` : ""}
        <span class="src">欄位：symbol, entry_date, entry_price, shares, stop, partial_done（已分批填 Y）, exit_ma（跟蹤均線 10／20／50）, note。例：<code>TSLA,2026-10-12,431.5,40,420,N,10,突破單</code></span></p>
      <div class="workspace"><div class="tablewrap"><table class="list" id="list-hold"></table></div><aside class="detail" id="detail-hold" aria-live="polite"></aside></div>`;
  }
  function renderHold() {
    const items = S.data.holdings || [], u = ui("hold");
    const col = COLS.hold.find((x) => x.k === u.sort) || COLS.hold[1], dir = u.sort ? u.dir : -1;
    const rows = [...items].sort((a, b) => { const x = col.val(a), y = col.val(b); if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
    if (!rows.some((c) => c.symbol === S.sel.hold)) S.sel.hold = rows[0]?.symbol ?? null;
    $("#list-hold").innerHTML = `<thead><tr>${COLS.hold.map((c) => `<th data-k="${c.k}" class="${c.num ? "num" : ""} ${c.cls || ""}">${c.label}${u.sort === c.k ? (u.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((c) => `<tr class="row ${c.symbol === S.sel.hold ? "sel" : ""} s-${c.status}" tabindex="0" data-id="${esc(c.symbol)}">${COLS.hold.map((x) => `<td class="${x.num ? "num" : ""} ${x.cls || ""}">${x.render(c)}</td>`).join("")}</tr>`).join("") || '<tr class="empty"><td colspan="8">還沒有持倉。編輯 <code>data/holdings.csv</code> 加一列即可。</td></tr>'}</tbody>`;
    const c = items.find((x) => x.symbol === S.sel.hold), el = $("#detail-hold");
    const flagged = items.filter((x) => x.status === "sell" || x.status === "partial").length;
    $("#holdFlag").textContent = flagged ? ` ${flagged}` : "";
    if (!c) { el.innerHTML = '<p class="muted">選一檔看賣出提示。</p>'; return; }
    el.style.setProperty("--c", `var(--${{ sell: "bad", partial: "amber", hold: "ok" }[c.status] || "muted"})`);
    const levels = [{ y: c.entry_price, label: "買入", cls: "entry" }, { y: c.stop, label: "止損", cls: "stop" }, { y: c.ma_value, label: `${c.exit_ma}日線`, cls: "low" }];
    el.innerHTML = c.status === "nodata" ? `<h2>${esc(c.symbol)}</h2><p class="verdict">沒有取得這檔的價格資料，請確認代號（BRK.B 要寫成 BRK-B）。</p>` : `
      <button class="btn sheet-close" data-act="close">關閉</button><h2>${esc(c.symbol)}</h2>
      <div class="sub">買入 ${md(c.entry_date)} @ ${num(c.entry_price)} · 持有 ${c.held_days} 天 · <a href="${tv(c.symbol)}" target="_blank" rel="noopener">TradingView ↗</a></div>
      <p class="verdict">${c.actions.length ? "依影片的出場規則，今天要處理：" : "目前沒有觸發任何賣出規則，續抱。"}</p>
      ${c.actions.length ? `<ul class="actions">${c.actions.map((a) => `<li class="${a.level}">${esc(a.text)}</li>`).join("")}</ul>` : ""}
      ${c.chart ? `<div class="chartbox">${Chart2.candleSvg(c.chart, { levels, marks: [{ date: c.entry_date, label: "買入" }], label: `${c.symbol} 持倉日線` })}</div>` : ""}
      <h3>目前</h3><dl class="levels"><dt>最新收盤</dt><dd>${num(c.last_close)}</dd><dt>損益</dt><dd class="${c.pnl_pct >= 0 ? "up" : "down"}">${pct(c.pnl_pct, 1, true)}</dd>
      <dt>止損</dt><dd>${num(c.stop)}</dd><dt>${c.exit_ma} 日線</dt><dd>${num(c.ma_value)}</dd><dt>分批</dt><dd>${c.partial_done ? "已完成" : "未做"}</dd><dt>下次財報</dt><dd>${c.days_to_earnings == null ? "未知" : `${c.days_to_earnings} 天後`}</dd></dl>
      <p class="muted" style="font-size:13px;margin-top:14px">影片規則：只看收盤，盤中跌破不算；突破後 3～5 天賣約一半、止損移到買入價；剩餘跟均線，第一次收盤跌破就全賣；財報前剩餘倉位獲利不到 ${S.cfg.hold_min_profit_to_hold_earnings_pct}% 先賣。</p>`;
  }

  // ───────── 規則 ─────────
  function renderRules() {
    const c = S.cfg, t = (rows) => `<table><tr><th>規則</th><th>影片的說法</th><th>本站怎麼判斷</th></tr>${rows.map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td></tr>`).join("")}</table>`;
    $("#tab-rules").innerHTML = `<div class="prose">
      <h2>兩支影片，三條線</h2>
      <p><b>影片 A（財報季與 PEAD）</b>講財報後漂移：大漲爆量的財報股先觀察，等整理收斂再向上突破才買。<b>影片 B（交易系統完整版）</b>講兩種單：<b>突破</b>（動能最強的股票大漲後橫盤、突破上沿）與 <b>EP</b>（財報或消息帶動的跳空），以及倉位、出場與大盤濾網。本站每天收盤後掃描，隔天開盤前讀；影片的盤中動作（開盤區間高點、盤前漲幅榜）日線做不到，需自己盯盤。</p>
      <h2>財報後 PEAD（影片 A）</h2>
      ${t([
        ["① 選股", "漲 ≥10%、量 ≥50 日均量 2 倍；營收與 EPS 都超預期，最好上調指引", `漲幅 ≥${c.min_gain_pct}%、量 ≥${c.min_volume_ratio} 倍（自動）；EPS 用日曆數字、營收需設 <code>FINNHUB_API_KEY</code>，指引須人工確認`],
        ["　剔除", "任何一天收盤跌破財報大漲當天的最低點", "自動，歸入「失效」"],
        ["② 整理", "回落 ≤25%；沒有放量大陰線；收盤不破 50 日線；至少一週；振幅與量越來越小", `回落 ≤${c.pullback_max_pct}%；跌 ≥${c.dist_drop_pct}% 且量 ≥${c.dist_volume_ratio} 倍算大陰線（暫定）；整理 ≥${c.min_consolidation_days} 日；近三日與前段比較`],
        ["③ 進場", "價格漲過整理區高點就買；止損放突破前最後幾天最低點；距離不超過平時一天的波動", `日線以最高價越過判定，並標出收盤是否站上；止損取突破前 ${c.stop_lookback_bars} 日最低；風險 ≤${c.risk_adr_mult}×ADR 理想、≤${c.risk_adr_max_mult}×ADR 可接受`],
        ["配對", "財報後的大漲才算", "盤前：財報日當天；盤後：下一交易日（用 Nasdaq 的時間欄位）"],
      ])}
      <h2>EP（影片 B）</h2>
      ${t([
        ["跳空", "跳空 ≥10%，開盤前十幾分鐘爆量；只跳 3% 且收跌不算", `開盤較前收 ≥${c.min_gain_pct}%（自動）；開盤區間高點需盤中看`],
        ["EPS", "比預期高出一大截", `超預期 ≥${c.ep_min_surprise_pct}%（暫定）；超過 ${c.eps_oneoff_warn_pct}% 提醒核對口徑（影片 A：谷歌 GAAP 含帳面收益）`],
        ["盈利年增", "三位數最好，兩位數中等以上也行", `年增 ≥${c.ep_eps_growth_min_pct}% 合格、≥${c.ep_eps_growth_triple_pct}% 標「三位數」（用日曆的去年同期 EPS）`],
        ["之前沒怎麼漲", "前三到六個月最好橫著", `前 ${c.ep_prior_bars} 日漲幅 ≤${c.ep_prior_run_pct}%（暫定）`],
      ])}
      <h2>動能突破（影片 B）</h2>
      ${t([
        ["選股", "1／3／6 個月漲幅各排名，只留最前 1～2%", `三個百分位取最高者 ≥${100 - c.momentum_top_pct}%；股票池＝Nasdaq 清單中價格 ≥$${c.universe_min_price}、日成交額 ≥$${(c.universe_min_dollar_volume_usd / 1e6).toFixed(0)}M 者，加上有財報的標的`],
        ["流動性", "ADR ≥5%（小帳戶）；日成交額至少帳戶的 50～100 倍", `ADR ≥${c.min_adr_pct}%；20 日均成交額 ≥$${(c.min_dollar_volume_usd / 1e6).toFixed(0)}M`],
        ["前段大漲", "過去 1～3 個月有一波 ≥30%", `${c.bo_lookback_bars} 日內高點前 ${c.bo_max_runup_bars} 日漲 ≥${c.bo_min_runup_pct}%`],
        ["橫盤", "兩週到兩個月；低點墊高、振幅收窄；快的貼 10／20 日線、慢的貼 50 日線", `${c.bo_min_cons_days}～${c.bo_max_cons_days} 日；低點分三段不降；顯示貼著哪條均線；另提示高點是否壓在同一帶（≤${c.bo_resistance_band_pct}%）`],
        ["進場", "價格越過橫盤上沿就買，不提前；止損放當天最低點，不超過 ADR", `最近 ${c.breakout_fresh_bars} 日內第一次越過者標「突破」並固定買點與止損；止損距離 ≤${c.risk_adr_mult}×ADR`],
        ["避開", "離財報只剩兩三天不買", `距財報 ≤${c.bo_earnings_block_days} 天擋下；日曆查不到時標「未知」，須人工確認`],
      ])}
      <h2>倉位與出場</h2>
      ${t([
        ["單筆風險", "帳戶的 0.25%～1.5%（小帳戶可高一些）", "在「設定」填，預設 0.5%；股數＝可虧金額 ÷（買價－止損）"],
        ["單檔上限", "10～20%；10 萬美元以下小帳戶至多 25%；最多同時 4 檔；不用槓桿", `預設 ${c.sizing_max_position_pct}%（小帳戶 ${c.sizing_small_max_position_pct}%）；僅提醒最多 ${c.sizing_max_positions} 檔`],
        ["分批", "突破後 3～5 天賣一部分，剩餘止損移到買入價", `在「持倉」頁，持有 ${c.hold_partial_days[0]}～${c.hold_partial_days[1]} 天提示賣約一半（影片字幕寫「1～1.5%」，疑似轉錄錯誤，依影片範例採一半）`],
        ["跟蹤", "剩餘倉位跟 10／20／50 日線，第一次收盤跌破就全賣；盤中不算", "持倉頁依 exit_ma 判斷（預設 20 日）"],
        ["財報前", "剩餘倉位獲利不到 20% 就先賣", `距財報 ≤${c.hold_earnings_days} 天且獲利 <${c.hold_min_profit_to_hold_earnings_pct}% → 該賣出`],
      ])}
      <h2>大盤濾網</h2>
      <p>看那斯達克：<b>10 日線在 20 日線之上且兩線都向上</b>＝正常做；有一條走平或拐頭＝小心、縮小倉位；<b>10 日線在 20 日線下且兩線向下</b>＝突破容易失敗，少做小做（影片提到 2022～2023 年大多靠 EP）。「向上」是指今天的均線比 3 個交易日前高。頁首會逐項列出三個條件，哪一項沒過就直接標出來。本站在弱勢時把「突破」排到「待突破」之後並加註警示。</p>
      <h2>本站刻意不做</h2>
      <p class="src">盤中 EP 判斷（需要分鐘線與盤前資料）、做空（影片也不建議）、通知、回測。營收與指引沒有穩定的免費來源（營收可選配 Finnhub），所以用「人工確認」勾選。<b>標「暫定」的數字影片沒有給，可在 <code>config.json</code> 調整。</b></p>
      <details><summary>全部參數與出處</summary><table>${Object.entries(c).filter(([k]) => !k.startsWith("_") && k !== "key_metrics").map(([k, v]) => `<tr><td><code>${k}</code></td><td class="tbl-n">${esc(JSON.stringify(v))}</td></tr>`).join("")}</table>
      <p class="src">${Object.entries(c._來源 || {}).map(([k, v]) => `<b>${esc(k)}</b>：${esc(v)}`).join("<br>")}</p></details></div>`;
  }

  // ───────── 設定與資料 ─────────
  function renderSettings() {
    const s = S.set, h = S.data.health || {}, src = h.price_sources || {};
    const row = (a, b, cls = "") => `<tr><td>${a}</td><td class="${cls}">${b}</td></tr>`;
    $("#tab-settings").innerHTML = `<div class="settings">
      <section><h2>帳戶與倉位</h2>
        <label class="field">帳戶金額（美元）<input type="number" id="set-account" min="1000" step="1000" value="${s.account}"></label>
        <label class="field">單筆風險（%）<input type="number" id="set-risk" min="0.05" max="3" step="0.05" value="${s.riskPct}"></label>
        <label class="field">單檔上限（%，0＝依影片自動）<input type="number" id="set-maxpos" min="0" max="50" step="1" value="${s.maxPosPct}"></label>
        <p class="hint">影片：單筆 0.25%～1.5%；單檔 10～20%（小於 10 萬美元至多 25%）；最多同時 4 檔。這些數字只用來試算，不會送到任何地方。</p></section>
      <section><h2>外觀</h2>
        <label class="field">主題<select id="set-theme"><option value="auto">跟隨系統</option><option value="light">淺色</option><option value="dark">深色</option></select></label>
        <label class="field">漲跌顏色<select id="set-color"><option value="cn">紅漲綠跌</option><option value="us">綠漲紅跌</option></select></label>
        <p class="hint">階段用方塊與文字區分（突破＝朱紅、待突破＝琥珀），不用漲跌色，避免混淆。</p></section>
      <section><h2>備份人工確認與筆記</h2>
        <p class="hint" style="margin-top:0">勾選與筆記只存在這個瀏覽器；換裝置或清除資料前請先匯出。匯入會與現有紀錄合併，同一檔以較新者為準。</p>
        <button class="btn" id="exp">匯出 JSON</button> <label class="btn">匯入 JSON<input type="file" id="imp" accept="application/json" hidden></label><p class="hint" id="bk-msg"></p></section>
      <section><h2>資料狀態</h2><table class="kv">
        ${row("價格截止日", esc(S.data.asof))}${row("產生時間", esc(new Date(S.data.generated_at).toLocaleString("zh-TW", { hour12: false })))}
        ${row("股票池", `${S.data.universe_size.toLocaleString()} 檔（${h.universe_source === "nasdaq" ? "Nasdaq 清單＋財報標的" : "僅財報標的"}）`, h.universe_source === "nasdaq" ? "" : "warn")}
        ${row("價格成功率", `${h.price_success_pct ?? "—"}%（${h.price_ok ?? "—"}／${h.targets ?? "—"}）`, (h.price_success_pct ?? 100) < 90 ? "warn" : "")}
        ${row("價格來源", `Yahoo ${src.yahoo ?? 0}・快取 ${src.cache ?? 0}・Stooq ${src.stooq ?? 0}・Nasdaq ${src.nasdaq ?? 0}`)}
        ${row("盤中未完成 K 線已丟棄", `${h.partial_bar_dropped ?? 0} 檔`)}${row("日曆抓取失敗", `${h.calendar_failed_days ?? 0} 天`, h.calendar_failed_days ? "bad" : "")}
        ${row("營收預期（Finnhub）", h.finnhub_filled ? `補到 ${h.finnhub_filled} 筆` : "未啟用（設 FINNHUB_API_KEY 才有）")}
        ${row("大盤", `${esc(S.data.regime?.index || "—")}・${esc(S.data.regime?.date || "")}`)}
      </table></section></div>`;
    $("#set-theme").value = s.theme; $("#set-color").value = s.color;
  }

  // ───────── 事件 ─────────
  function saveSettings() { LS.set("pb.settings", S.set); }
  const download = (name, text, type) => {
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type })), download: name });
    document.body.append(a); a.click(); a.remove();
  };
  function exportLane(lane, act, btn) {
    const rows = visibleItems(lane);
    if (!rows.length) return;
    if (act === "copy") {
      const text = rows.map((c) => c.symbol.replace("-", ".")).join(",");
      (navigator.clipboard?.writeText(text) || Promise.reject()).catch(() => { const t = Object.assign(document.createElement("textarea"), { value: text }); document.body.append(t); t.select(); document.execCommand("copy"); t.remove(); });
      const old = btn.textContent; btn.textContent = `已複製 ${rows.length} 檔`; setTimeout(() => (btn.textContent = old), 1600);
    } else {
      const st = S.set, maxPos = st.maxPosPct || Sizing.defaultMaxPosPct(st.account, S.cfg);
      const lines = ["symbol,stage,alert_price,stop,suggested_shares,note"].concat(rows.map((c) => {
        const p = Sizing.position({ account: st.account, riskPct: st.riskPct, entry: c.entry_price ?? c.pivot, stop: c.stop, maxPosPct: maxPos });
        return [c.symbol, STAGE_LABEL[c.stage], c.pivot ?? "", c.stop ?? "", p ? p.shares : "", `"${(S.notes[LANES[lane].noteKey(c)]?.note || "").replace(/"/g, '""')}"`].join(",");
      }));
      download(`alerts-${lane}-${S.data.asof}.csv`, "\ufeff" + lines.join("\n"), "text/csv");
    }
  }

  function selectRow(lane, id) {
    S.sel[lane] = id;
    lane === "hold" ? renderHold() : renderLane(lane);
    if (narrow()) { $(`#detail-${lane}`).classList.add("open"); $("#scrim").classList.add("open"); }
  }
  const closeSheet = () => { $$(".detail.open").forEach((d) => d.classList.remove("open")); $("#scrim").classList.remove("open"); };

  function setTab(name) {
    S.tab = name;
    $$("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
    closeSheet();
  }

  function bind() {
    $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) setTab(b.dataset.tab); });
    $("#tabs").addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      const tabs = $$("#tabs button"), i = tabs.findIndex((b) => b.getAttribute("aria-selected") === "true");
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]; next.focus(); setTab(next.dataset.tab);
    });
    $("#scrim").addEventListener("click", closeSheet);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

    for (const lane of ["pead", "breakout", "hold"]) {
      const root = $(`#tab-${lane}`);
      root.addEventListener("click", (e) => {
        const stage = e.target.closest("[data-stage]"), th = e.target.closest("th[data-k]"), row = e.target.closest("tr.row"), act = e.target.closest("[data-act]");
        if (act?.dataset.act === "close") return closeSheet();
        if (act) return exportLane(lane, act.dataset.act, act);
        if (stage) { ui(lane).stage = stage.dataset.stage; S.sel[lane] = null; saveUi(); return renderLane(lane); }
        if (th) {
          const u = ui(lane), col = COLS[lane].find((c) => c.k === th.dataset.k);
          u.dir = u.sort === col.k ? -u.dir : (col.dir ?? -1); u.sort = col.k; saveUi();
          return lane === "hold" ? renderHold() : renderLane(lane);
        }
        if (row) selectRow(lane, row.dataset.id);
      });
      root.addEventListener("keydown", (e) => {
        const row = e.target.closest("tr.row");
        if (!row) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); return selectRow(lane, row.dataset.id); }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const sib = e.key === "ArrowDown" ? row.nextElementSibling : row.previousElementSibling;
          if (sib?.classList.contains("row")) { S.sel[lane] = sib.dataset.id; lane === "hold" ? renderHold() : renderLane(lane); $(`#tab-${lane} tr.row.sel`)?.focus(); }
        }
      });
      // 人工確認與筆記
      root.addEventListener("input", (e) => {
        const f = e.target.dataset?.f, box = e.target.closest(".manual");
        if (box && f) {
          const n = (S.notes[box.dataset.key] ||= {});
          n[f] = e.target.type === "checkbox" ? e.target.checked : e.target.value; n.t = Date.now();
          if (!LS.set("pb.notes", S.notes) && !S.warned) { S.warned = true; alert("瀏覽器無法儲存（可能是無痕模式），筆記不會保留，請先匯出備份。"); }
        }
      });
    }
    // 搜尋：只更新清單區，不重建輸入框；輸入法組字中不重繪
    for (const lane of ["pead", "breakout"]) {
      const q = $(`#q-${lane}`);
      q.addEventListener("compositionstart", () => (S.composing = true));
      q.addEventListener("compositionend", () => { S.composing = false; q.dispatchEvent(new Event("input")); });
      q.addEventListener("input", () => { if (S.composing) return; ui(lane).q = q.value; saveUi(); renderLane(lane); });
    }

    $("#tab-settings").addEventListener("input", (e) => {
      const id = e.target.id, v = e.target.value;
      if (id === "set-account") S.set.account = Math.max(Number(v) || 0, 0);
      else if (id === "set-risk") S.set.riskPct = Math.max(Number(v) || 0, 0);
      else if (id === "set-maxpos") S.set.maxPosPct = Math.max(Number(v) || 0, 0);
      else if (id === "set-theme") S.set.theme = v;
      else if (id === "set-color") S.set.color = v;
      else return;
      saveSettings(); applyLook(); ["pead", "breakout"].forEach(renderDetail);
    });
    $("#tab-settings").addEventListener("click", (e) => {
      if (e.target.id !== "exp") return;
      download(`pead-notes-${S.data.asof}.json`, JSON.stringify(S.notes, null, 1), "application/json");
    });
    $("#tab-settings").addEventListener("change", (e) => {
      if (e.target.id !== "imp" || !e.target.files[0]) return;
      const rd = new FileReader();
      rd.onload = () => {
        try {
          const data = JSON.parse(rd.result);
          if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("格式不對");
          let merged = 0;
          for (const [k, v] of Object.entries(data)) if (v && typeof v === "object" && (v.t || 0) >= (S.notes[k]?.t || 0)) { S.notes[k] = v; merged++; }
          LS.set("pb.notes", S.notes); ["pead", "breakout"].forEach(renderDetail);
          $("#bk-msg").textContent = `已匯入：採用 ${merged} 筆，其餘保留較新的現有紀錄。`;
        } catch { $("#bk-msg").textContent = "檔案格式無法讀取。"; }
        e.target.value = "";
      };
      rd.readAsText(e.target.files[0]);
    });
  }

  // ───────── 啟動 ─────────
  async function init() {
    applyLook();
    try {
      const get = async (p) => { const r = await fetch(p, { cache: "no-cache" }); if (!r.ok) throw new Error(`${p} (${r.status})`); return r.json(); };
      [S.cfg, S.data] = await Promise.all([get("config.json"), get("data/screen.json")]);
      if (!S.data.generated_at) throw new Error("data/screen.json 還沒有資料");
    } catch (e) {
      $("#regime").className = "regime weak";
      $("#regime").innerHTML = `<b>資料載入失敗</b>：${esc(e.message)}<span class="meta">請到 GitHub 的 Actions 手動執行一次「更新資料並部署 Pages」；本機開啟請用 <code>python -m http.server</code>，不要直接點開檔案。</span>`;
      $("#main").insertAdjacentHTML("afterbegin", '<div class="notice">目前沒有可顯示的資料。</div>');
      return;
    }
    renderRegime(); buildLane("pead"); buildLane("breakout"); buildHold(); bind();
    for (const lane of ["pead", "breakout"]) {   // 開啟時才自動挑第一個有標的的階段；之後使用者點哪個就顯示哪個（包含空的）
      const u = ui(lane), items = LANES[lane].items();
      if (!items.some((c) => c.stage === u.stage)) u.stage = LANES[lane].stages.find((st) => items.some((c) => c.stage === st)) || LANES[lane].stages[0];
    }
    renderLane("pead"); renderLane("breakout"); renderHold(); renderRules(); renderSettings();
    const first = ["pead", "breakout"].find((l) => LANES[l].items().some((c) => c.stage === "breakout"));
    if (first && first !== "pead" && !LANES.pead.items().some((c) => c.stage === "breakout")) setTab(first);
  }
  init();
})();
