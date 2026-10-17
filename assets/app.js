"use strict";

// ---------- 工具 ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const iso = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
const fmtMcap = (v) => (v == null ? "" : v >= 1e12 ? `${(v / 1e12).toFixed(2)}T` : v >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : `${(v / 1e6).toFixed(0)}M`);
const fmtPct = (v, sign = false) => (v == null ? "–" : `${sign && v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const tvUrl = (c) => `https://www.tradingview.com/symbols/${c.market === "JP" ? "TSE-" : ""}${encodeURIComponent(c.symbol)}/`;
const storage = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)) || {}; } catch { return {}; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 無痕模式等情況略過 */ } },
};

const STAGES = [
  ["breakout", "突破中", "整理後剛突破（近幾日），為可進場觀察標的。"],
  ["consolidating", "整理中", "五項整理條件皆合格、尚未突破；盯著買點。"],
  ["watch", "觀察", "已通過步驟一，但整理天數不足或有條件未過。"],
  ["extended", "已突破過", "突破已有一段時間，PEAD 規則不追。"],
  ["removed", "已剔除", "收盤跌破財報大漲日最低點。"],
];
const CHECK_LABEL = { pass: "合格", warn: "黃燈", fail: "不合格", pending: "待觀察" };
const REGIME_LABEL = { strong: "強（正常做）", caution: "小心", weak: "弱（少做／小做）", unknown: "未知" };

const state = { cfg: null, cal: null, scr: null, year: 0, month: 0, selDate: null, stage: "breakout", expanded: new Set(),
  byDate: {}, daySort: { key: "mcap", dir: -1 }, dayFilter: { eps: "all", minMcap: 0, onlyCand: false } };

// ---------- 載入 ----------
async function loadJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

async function init() {
  bindTabs();
  $("tab-strategy").innerHTML = window.STRATEGY_HTML;
  try {
    [state.cfg, state.cal, state.scr] = await Promise.all([
      loadJSON("config.json"), loadJSON("data/calendar.json"), loadJSON("data/screen.json"),
    ]);
  } catch (e) {
    document.querySelector("main").insertAdjacentHTML("afterbegin",
      `<div class="card">尚無資料（${esc(e.message)}）。請先執行 <code>python scripts/fetch_data.py</code>，或等待 GitHub Actions 產生資料。</div>`);
    if (!state.cfg) state.cfg = await loadJSON("config.json").catch(() => null);
    renderParams();
    return;
  }
  const now = new Date();
  state.year = now.getFullYear();
  state.month = now.getMonth();
  $("updated").textContent = `資料更新：${new Date(state.cal.generated_at).toLocaleString("zh-TW")}`;
  renderRegime();
  renderParams();
  bindCalendar();
  bindScreener();
  renderCalendar();
  renderScreener();
}

function bindTabs() {
  $("tabs").addEventListener("click", (e) => {
    const t = e.target.dataset.tab;
    if (!t) return;
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
    document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === `tab-${t}`));
  });
}

function renderRegime() {
  const names = { US: "美股（那指）", JP: "日股（日經）" };
  $("regime").innerHTML = Object.entries(state.scr.regime || {})
    .map(([m, r]) => `<span class="badge ${esc(r.status)}" title="10日線在20日線上：${r.ma10_above_ma20 ? "是" : "否"}；10日線向上：${r.ma10_up ? "是" : "否"}；20日線向上：${r.ma20_up ? "是" : "否"}">${names[m] || m}：${REGIME_LABEL[r.status] || r.status}</span>`)
    .join("");
}

// ---------- 日曆 ----------
function candidateKeys() {
  return new Set((state.scr.candidates || []).filter((c) => c.stage !== "removed").map((c) => `${c.market}:${c.symbol}`));
}

function visibleEvents() {
  const us = $("fUS").checked, jp = $("fJP").checked, jpAll = $("fJPAll").checked;
  const onlyCand = $("fCand").checked, q = $("q").value.trim().toLowerCase();
  const keys = candidateKeys();
  return state.cal.events.filter((e) => {
    if (e.market === "US" && !us) return false;
    if (e.market === "JP" && (!jp || (!jpAll && !e.in_universe))) return false;
    if (onlyCand && !keys.has(`${e.market}:${e.symbol}`)) return false;
    if (q && !`${e.symbol} ${e.name}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

function bindCalendar() {
  $("prev").onclick = () => shiftMonth(-1);
  $("next").onclick = () => shiftMonth(1);
  $("today").onclick = () => { const n = new Date(); state.year = n.getFullYear(); state.month = n.getMonth(); renderCalendar(); };
  ["fUS", "fJP", "fJPAll", "fCand", "q"].forEach((id) => $(id).addEventListener("input", renderCalendar));
  $("dayPanel").addEventListener("click", (e) => {
    const k = e.target.closest("th[data-sort]")?.dataset.sort;
    if (!k) return;
    const sort = state.daySort;
    sort.dir = sort.key === k ? -sort.dir : (k === "symbol" || k === "name" ? 1 : -1);
    sort.key = k;
    renderDayPanel();
  });
  $("dayPanel").addEventListener("input", (e) => {
    const f = state.dayFilter;
    if (e.target.id === "dEps") f.eps = e.target.value;
    else if (e.target.id === "dMcap") f.minMcap = Number(e.target.value);
    else if (e.target.id === "dCand") f.onlyCand = e.target.checked;
    else return;
    renderDayPanel();
  });
  $("calGrid").addEventListener("click", (e) => {
    const cell = e.target.closest(".cell");
    if (cell) { state.selDate = cell.dataset.date; renderCalendar(); }
  });
}

function shiftMonth(delta) {
  const d = new Date(state.year, state.month + delta, 1);
  state.year = d.getFullYear();
  state.month = d.getMonth();
  renderCalendar();
}

function renderCalendar() {
  const { year, month } = state;
  $("monthTitle").textContent = `${year} 年 ${month + 1} 月`;
  const byDate = {};
  visibleEvents().forEach((e) => (byDate[e.date] ||= []).push(e));
  const keys = candidateKeys();
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7; // 週一為首
  const days = new Date(year, month + 1, 0).getDate();
  const t = new Date();
  const todayIso = iso(t.getFullYear(), t.getMonth(), t.getDate());

  let html = "";
  for (let i = 0; i < Math.ceil((offset + days) / 7) * 7; i++) {
    const dt = new Date(year, month, 1 - offset + i);
    const dIso = iso(dt.getFullYear(), dt.getMonth(), dt.getDate());
    const list = (byDate[dIso] || []).slice().sort((a, b) => (b.mcap || 0) - (a.mcap || 0) || (b.in_universe ? 1 : 0) - (a.in_universe ? 1 : 0));
    const nUS = list.filter((e) => e.market === "US").length, nJP = list.length - nUS;
    const chips = list.slice(0, 3).map((e) =>
      `<span class="chip ${e.market === "US" ? "us" : "jp"} ${keys.has(`${e.market}:${e.symbol}`) ? "cand" : ""}">${esc(e.symbol)} ${esc(e.name).slice(0, 14)}</span>`).join("");
    const cls = ["cell", dt.getMonth() !== month ? "out" : "", dIso === todayIso ? "today" : "", dIso === state.selDate ? "sel" : ""].join(" ");
    html += `<div class="${cls}" data-date="${dIso}"><div class="d"><span>${dt.getDate()}</span><span class="n">${nUS ? `<span class="us">美${nUS}</span> ` : ""}${nJP ? `<span class="jp">日${nJP}</span>` : ""}</span></div>${chips}${list.length > 3 ? `<span class="more">+${list.length - 3} 家</span>` : ""}</div>`;
  }
  $("calGrid").innerHTML = html;
  state.byDate = byDate;
  renderDayPanel();
}

const STAGE_RANK = { breakout: 5, consolidating: 4, watch: 3, extended: 2, removed: 1 };
const DAY_COLS = [
  ["symbol", "代號"], ["name", "公司"], ["time", "時段"], ["mcap", "市值", true],
  ["eps_forecast", "EPS預期", true], ["eps_actual", "EPS實際", true], ["eps_surprise_pct", "驚喜", true], ["stage", "選股狀態"],
];

function renderDayPanel() {
  const panel = $("dayPanel");
  if (!state.selDate) { panel.classList.add("hidden"); return; }
  const cands = new Map((state.scr.candidates || []).map((c) => [`${c.market}:${c.symbol}`, c]));
  const { eps, minMcap, onlyCand } = state.dayFilter;
  const { key, dir } = state.daySort;
  const all = state.byDate[state.selDate] || [];
  const stageOf = (e) => cands.get(`${e.market}:${e.symbol}`)?.stage;
  const sortVal = (e) => (key === "stage" ? STAGE_RANK[stageOf(e)] : e[key]);

  const list = all.filter((e) => {
    if (eps === "beat" && !(e.eps_surprise_pct > 0)) return false;
    if (eps === "miss" && !(e.eps_surprise_pct < 0)) return false;
    if (eps === "none" && e.eps_surprise_pct != null) return false;
    if (minMcap && (e.mcap ?? 0) < minMcap) return false; // 日本無市值資料，設門檻時會被排除
    if (onlyCand && !stageOf(e)) return false;
    return true;
  }).sort((a, b) => {
    const va = sortVal(a), vb = sortVal(b);
    if (va == null || va === "") return 1;   // 空值一律排最後
    if (vb == null || vb === "") return -1;
    return typeof va === "string" ? dir * va.localeCompare(vb) : dir * (va - vb);
  });

  const rows = list.map((e) => {
    const stage = stageOf(e) ? (STAGES.find((s) => s[0] === stageOf(e)) || [])[1] : "";
    const sur = e.eps_surprise_pct;
    return `<tr><td><span class="tag ${e.market === "US" ? "us" : "jp"}">${e.market === "US" ? "美" : "日"}</span>${esc(e.symbol)}</td><td>${esc(e.name)}</td><td>${esc(e.time)}</td>
      <td class="num">${fmtMcap(e.mcap)}</td><td class="num">${e.eps_forecast ?? "–"}</td><td class="num">${e.eps_actual ?? "–"}</td>
      <td class="num ${sur > 0 ? "pos" : sur < 0 ? "neg" : ""}">${sur == null ? "–" : fmtPct(sur, true)}</td><td>${stage ? `<b>${esc(stage)}</b>` : ""}</td>
      <td><a href="${tvUrl(e)}" target="_blank" rel="noopener">TV</a></td></tr>`;
  }).join("");

  const th = DAY_COLS.map(([k, label, num]) =>
    `<th data-sort="${k}" class="sortable ${num ? "num" : ""}">${label}${k === key ? (dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("");
  const opt = (v, label, cur) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${label}</option>`;
  panel.classList.remove("hidden");
  panel.innerHTML = `<div class="toolbar"><b>${esc(state.selDate)}</b><span>顯示 ${list.length} / ${all.length} 家</span>
    <label>EPS <select id="dEps">${opt("all", "全部", eps)}${opt("beat", "超預期", eps)}${opt("miss", "不及預期", eps)}${opt("none", "尚無實際值", eps)}</select></label>
    <label>市值 <select id="dMcap">${opt(0, "不限", minMcap)}${opt(1e9, "≥1B", minMcap)}${opt(1e10, "≥10B", minMcap)}${opt(1e11, "≥100B", minMcap)}</select></label>
    <label><input type="checkbox" id="dCand" ${onlyCand ? "checked" : ""}> 只看選股名單</label></div>
    <div class="tablewrap"><table><tr>${th}<th></th></tr>${rows || '<tr><td colspan="9" class="muted">無符合篩選的財報</td></tr>'}</table></div>`;
}

// ---------- 選股清單 ----------
function bindScreener() {
  ["sUS", "sJP", "sLinked", "sSort"].forEach((id) => $(id).addEventListener("input", renderScreener));
  $("stageBar").addEventListener("click", (e) => {
    const s = e.target.dataset.stage;
    if (s) { state.stage = s; renderScreener(); }
  });
  $("candTable").addEventListener("click", (e) => {
    if (e.target.closest("input, a")) return;
    const row = e.target.closest("tr.row");
    if (!row) return;
    const k = row.dataset.key;
    state.expanded.has(k) ? state.expanded.delete(k) : state.expanded.add(k);
    renderScreener();
  });
  $("candTable").addEventListener("change", (e) => {
    const box = e.target.closest("input[data-manual]");
    if (!box) return;
    const all = storage.get("manual");
    (all[box.dataset.key] ||= {})[box.dataset.manual] = box.checked;
    storage.set("manual", all);
  });
}

const dotRow = (c) => (c.checks || []).map((k) => `<span class="dot ${k.status}" title="${esc(k.label)}：${CHECK_LABEL[k.status]}（${esc(k.detail)}）"></span>`).join("");

function renderScreener() {
  const all = state.scr.candidates || [];
  const us = $("sUS").checked, jp = $("sJP").checked, linked = $("sLinked").checked;
  const pool = all.filter((c) => (c.market === "US" ? us : jp) && (!linked || c.earnings_linked));
  $("stageBar").innerHTML = STAGES.map(([k, label]) =>
    `<button class="stagebtn ${k === state.stage ? "active" : ""}" data-stage="${k}">${label}（${pool.filter((c) => c.stage === k).length}）</button>`).join("");
  $("stageHint").textContent = (STAGES.find((s) => s[0] === state.stage) || [])[2] || "";

  const sortKey = $("sSort").value;
  const dir = sortKey === "risk_pct" || sortKey === "dist_to_pivot_pct" ? 1 : -1;
  const rows = pool.filter((c) => c.stage === state.stage).sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey];
    if (va == null) return 1;
    if (vb == null) return -1;
    return typeof va === "string" ? dir * va.localeCompare(vb) : dir * (va - vb);
  });

  const manual = storage.get("manual");
  const head = `<tr><th>標的</th><th>財報日</th><th>反應日</th><th class="num">漲幅</th><th class="num">量能</th><th class="num">EPS驚喜</th>
    <th>整理五項</th><th class="num">買點</th><th class="num">止損</th><th class="num">風險/平時波動</th><th class="num">距買點</th><th>人工確認</th></tr>`;
  const body = rows.map((c) => {
    const key = `${c.market}:${c.symbol}:${c.reaction_date}`;
    const m = manual[key] || {};
    const risk = c.risk_pct == null ? "–" : `<span class="${c.risk_ok ? "pos" : "neg"}">${c.risk_pct}% / ${c.adr_pct}%</span>`;
    const row = `<tr class="row" data-key="${esc(key)}">
      <td><span class="tag ${c.market === "US" ? "us" : "jp"}">${c.market === "US" ? "美" : "日"}</span><b>${esc(c.symbol)}</b> <span class="muted">${esc(c.name).slice(0, 22)}</span> <a href="${tvUrl(c)}" target="_blank" rel="noopener">TV</a></td>
      <td>${c.earnings_linked ? esc(c.earnings_date) + (c.earnings_time ? ` ${esc(c.earnings_time)}` : "") : '<span class="muted">待確認</span>'}</td>
      <td>${esc(c.reaction_date)}</td><td class="num pos">${fmtPct(c.gain_pct, true)}</td><td class="num">${c.volume_ratio}×</td>
      <td class="num ${c.eps_surprise_pct > 0 ? "pos" : c.eps_surprise_pct < 0 ? "neg" : ""}">${c.eps_surprise_pct == null ? "–" : fmtPct(c.eps_surprise_pct, true)}</td>
      <td>${c.stage === "removed" ? `<span class="muted">${esc(c.removed_on)} 跌破</span>` : dotRow(c)}</td>
      <td class="num">${c.pivot ?? "–"}</td><td class="num">${c.stop ?? "–"}</td><td class="num">${risk}</td>
      <td class="num">${c.dist_to_pivot_pct == null ? "–" : fmtPct(c.dist_to_pivot_pct)}</td>
      <td><label><input type="checkbox" data-key="${esc(key)}" data-manual="rev" ${m.rev ? "checked" : ""}>營收</label>
          <label><input type="checkbox" data-key="${esc(key)}" data-manual="guide" ${m.guide ? "checked" : ""}>指引</label></td></tr>`;
    if (!state.expanded.has(key)) return row;
    const items = (c.checks || []).map((k) => `<li><span class="dot ${k.status}"></span><b>${esc(k.label)}</b>：${CHECK_LABEL[k.status]}　<span class="muted">${esc(k.detail)}</span></li>`).join("");
    return row + `<tr class="detail"><td colspan="12"><ul class="checks">${items || "<li class='muted'>無整理資料</li>"}</ul>
      <div class="muted">財報日低點 ${c.reaction_low}　最新收盤 ${c.last_close}（${esc(c.last_date)}）　整理 ${c.cons_days ?? 0} 日${c.breakout_date ? `　突破日 ${esc(c.breakout_date)}` : ""}</div></td></tr>`;
  }).join("");
  $("candTable").innerHTML = head + (body || '<tr><td colspan="12" class="muted">目前沒有符合的標的</td></tr>');
}

// ---------- 參數頁 ----------
function renderParams() {
  const c = state.cfg;
  if (!c) return;
  const row = (label, val, src) => `<tr><td>${label}</td><td class="num"><b>${val}</b></td><td>${src}</td></tr>`;
  $("tab-params").innerHTML = `
  <div class="card"><h3>篩選參數（config.json）</h3>
    <table><tr><th>參數</th><th class="num">值</th><th>來源</th></tr>
      ${row("財報日漲幅下限", `${c.min_gain_pct}%`, "影片")}
      ${row("量能倍數（vs 前 50 日均量）", `${c.min_volume_ratio}×`, "影片")}
      ${row("財報後最大回落上限", `${c.pullback_max_pct}%`, "影片")}
      ${row("最少整理天數", `${c.min_consolidation_days} 日`, "影片（一週以上）")}
      ${row("止損參考：突破前最低點的根數", `${c.stop_lookback_bars} 根`, "影片（最後幾天）")}
      ${row("風險上限 ＝ 平時日振幅 ×", c.risk_adr_mult, "影片（不超過平時一天波動）")}
      ${row("放量大陰線：跌幅", `≥${c.dist_drop_pct}%`, "預設（影片未給數字）")}
      ${row("放量大陰線：量能倍數", `≥${c.dist_volume_ratio}×`, "預設（影片未給數字）")}
      ${row("突破視為「剛突破」的天數", `${c.breakout_fresh_bars} 日內`, "預設")}
      ${row("掃描回看交易日", `${c.lookback_trading_days} 日`, "預設（影片：財報季約 3～4 週）")}
      ${row("美股最低市值", `${(c.us_min_mcap_usd / 1e9).toFixed(0)}B USD`, "預設")}
    </table></div>
  <div class="card"><h3>限制與注意</h3><ul>
    <li><b>整理五項</b>中「放量大陰線」「振幅量能收斂」影片只給定性描述，門檻為本站暫定，請依回測調整 <code>config.json</code>。</li>
    <li>反應日以<b>收盤對前收</b>計算漲幅；美股依財報日曆確認，日本在 JPX 日程缺資料時標示「待確認」，需人工核對是否為財報帶動。</li>
    <li>日本掃描範圍為 <code>data/universe_jp.txt</code> 的清單，不是全市場；可自行增刪。日本財報日程來自 JPX 公布的 Excel，若格式變動可改用 <code>data/jp_earnings_manual.csv</code> 補充。</li>
    <li>營收超預期、上調指引、最被盯著的指標無免費穩定資料源，請在清單用勾選框自行確認（僅存於本機瀏覽器）。</li>
    <li>日期為各交易所當地日期（美東／日本）。價格資料來自 Yahoo Finance，可能有缺漏或延遲。</li>
  </ul></div>`;
}

init();
