// 美股突破篩選器前端：讀 data/screen.json 與 config.json，渲染兩條篩選線與規則頁。
"use strict";

const REGIME = {
  strong: ["good", "大盤強勢", "納指 10 日線在 20 日線上且兩線向上：放手做"],
  caution: ["warn", "大盤轉弱警戒", "有一條均線走平或拐頭：小心，突破容易失敗"],
  weak: ["bad", "大盤弱勢", "10 日線在 20 日線下且兩線向下：少做、小倉位試單（不是完全不做）"],
  unknown: ["dim", "大盤未知", "指數資料未取得"],
};
const GRADE = { ideal: ["good", "止損 ≤1×ADR"], acceptable: ["warn", "止損 ≤1.5×ADR"], too_wide: ["bad", "止損太遠・先不做"] };
const MARK = { pass: "✓", warn: "!", fail: "✗", pending: "…", unknown: "?" };

// 每條線的階段：標籤、顏色、預設是否顯示
const LANES = {
  pead: {
    stages: {
      breakout: ["突破中", "good", true], consolidating: ["整理合格・待突破", "warn", true], watch: ["觀察", "dim", true],
      extended: ["突破已逾 5 日", "dim", false], removed: ["已失效", "bad", false],
    },
    sorts: { priority: "行動優先度", gain_pct: "反應日漲幅", volume_ratio: "量能倍數", ep_score: "EP 品質", reaction_date: "反應日（新→舊）" },
  },
  breakout: {
    stages: { breakout: ["今日突破", "good", true], setup: ["整理合格・待突破", "warn", true] },
    sorts: { priority: "行動優先度", momentum_rank: "動能排名", runup_pct: "前段漲幅", dollar_volume_musd: "日成交額" },
  },
};

const state = { data: null, cfg: null, filters: {} };
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, unit = "", digits = 1) => (v == null || Number.isNaN(v) ? "—" : `${Number(v).toFixed(digits)}${unit}`);
const pill = (cls, text) => `<span class="pill ${cls}">${esc(text)}</span>`;
const kv = (label, value) => `<div class="kv"><span>${esc(label)}</span><b>${value}</b></div>`;

// ---------- 通用：排序與卡片 ----------

/** 行動優先度：先看階段，再看離買點多近（越近越前面）。 */
function priority(item, order) {
  const rank = order.indexOf(item.stage);
  return (rank < 0 ? 99 : rank) * 1000 + Math.abs(item.dist_to_pivot_pct ?? 99);
}

function checksHtml(title, checks) {
  const rows = checks.map((c) => `<li class="${c.status}"><span>${MARK[c.status] || "?"}</span><b>${esc(c.label)}</b><span class="detail">${esc(c.detail)}</span></li>`);
  return `<details><summary>${esc(title)}</summary><ul class="checks">${rows.join("")}</ul></details>`;
}

function levelsHtml(item) {
  if (item.pivot == null) return "";
  // 動能突破尚未越過時，止損只是估計（影片 B：突破當天以當天最低點為止損），不評級
  const estimate = item.stage === "setup";
  const [cls, text] = estimate ? ["dim", "突破日以當日低點為準"] : GRADE[item.risk_grade] || ["dim", "—"];
  const dist = item.stage === "breakout" || item.breakout_date ? "已越過" : `${num(item.dist_to_pivot_pct, "%")}`;
  return `<div class="levels">${kv("買點（橫盤高點）", num(item.pivot, "", 2))}${kv(estimate ? "止損（估）" : "止損", num(item.stop, "", 2))}
    ${kv("風險", `${num(item.risk_pct, "%")} <small class="muted">/ ADR ${num(item.adr_pct, "%")}</small>`)}${kv("評級", pill(cls, text))}
    ${kv("距買點", dist)}${kv("收盤", num(item.last_close, "", 2))}</div>`;
}

function cardHtml(item, lane) {
  const [label, cls] = LANES[lane].stages[item.stage] || [item.stage, "dim"];
  const tv = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(item.symbol.replace("-", "."))}`;
  const head = `<div class="card-head"><div><a class="sym" href="${tv}" target="_blank" rel="noopener">${esc(item.symbol)}</a>
    <span class="muted">${esc(item.name)}</span></div><div class="tags">`;
  if (lane === "pead") {
    const ep = item.ep_score ?? 0;
    return `<article class="card ${item.stage === "breakout" ? "hot" : ""}">${head}${pill(cls, label)}${pill(ep === 3 ? "good" : ep === 2 ? "warn" : "dim", `EP ${ep}/3`)}</div></div>
      <div class="grid">${kv("反應日", esc(item.reaction_date))}${kv("反應日漲幅", num(item.gain_pct, "%"))}${kv("開盤跳空", num(item.gap_open_pct, "%"))}
      ${kv("量能倍數", num(item.volume_ratio, "×"))}${kv("最大回落", num(item.max_pullback_pct, "%"))}${kv("整理天數", item.cons_days ?? "—")}
      ${kv("財報", `${esc(item.earnings_date)} <small class="muted">${esc(item.earnings_time)}</small>`)}${kv("EPS 驚喜", num(item.eps_surprise_pct, "%"))}</div>
      ${levelsHtml(item)}${checksHtml("整理條件（影片 A）", item.checks)}${checksHtml("EP 品質（影片 B）", item.ep_checks)}</article>`;
  }
  return `<article class="card ${item.stage === "breakout" ? "hot" : ""}">${head}${pill(cls, label)}${pill("good", `動能 ${num(item.momentum_rank, "", 0)}`)}</div></div>
    <div class="grid">${kv("前段漲幅", num(item.runup_pct, "%", 0))}${kv("整理天數", item.cons_days)}${kv("高點日", esc(item.peak_date))}
    ${kv("1／3／6 月漲幅", `${num(item.ret_1m, "", 0)}／${num(item.ret_3m, "", 0)}／${num(item.ret_6m, "", 0)}%`)}
    ${kv("ADR", num(item.adr_pct, "%"))}${kv("日成交額", num(item.dollar_volume_musd, "M", 0))}${kv("下次財報", esc(item.next_earnings || "未知"))}</div>
    ${levelsHtml(item)}${checksHtml("突破條件（影片 B）", item.checks)}</article>`;
}

// ---------- 篩選線頁面 ----------

function renderLane(lane) {
  const items = state.data[lane];
  const f = (state.filters[lane] ||= {
    stages: new Set(Object.entries(LANES[lane].stages).filter(([, v]) => v[2]).map(([k]) => k)), query: "", sort: "priority" });
  const order = Object.keys(LANES[lane].stages);
  const counts = Object.fromEntries(order.map((s) => [s, items.filter((x) => x.stage === s).length]));

  const chips = order.map((s) => `<button class="chip ${f.stages.has(s) ? "on" : ""}" data-stage="${s}">${esc(LANES[lane].stages[s][0])} ${counts[s]}</button>`).join("");
  const sorts = Object.entries(LANES[lane].sorts).map(([k, v]) => `<option value="${k}" ${f.sort === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  const intro = lane === "pead"
    ? `<h2>財報後 PEAD／EP</h2><p class="muted">財報大漲且爆量 → 整理收斂 → 突破整理高點才買；以 EP 條件（跳空、EPS、之前沒漲）評品質。</p>`
    : `<h2>動能突破</h2><p class="muted">1／3／6 個月漲幅全市場前 ${state.cfg.momentum_top_pct}% → 前段大漲後橫盤 → 突破橫盤上沿。股票池 ${state.data.universe_size} 檔${
        state.data.breakout_total > items.length ? `，符合者 ${state.data.breakout_total} 檔、僅列動能最強 ${items.length} 檔` : ""}。</p>`;

  const shown = items.filter((x) => f.stages.has(x.stage) && (`${x.symbol} ${x.name}`).toLowerCase().includes(f.query.toLowerCase()))
    .sort((a, b) => (f.sort === "priority" ? priority(a, order) - priority(b, order)
      : f.sort === "reaction_date" ? b.reaction_date.localeCompare(a.reaction_date) : (b[f.sort] ?? -1) - (a[f.sort] ?? -1)));

  $(`tab-${lane}`).innerHTML = `${intro}<div class="toolbar">${chips}<input class="grow" type="search" placeholder="搜尋代號或公司" value="${esc(f.query)}">
    <select>${sorts}</select></div>
    <div class="list">${shown.length ? shown.map((x) => cardHtml(x, lane)).join("") : `<div class="empty">${items.length ? "沒有符合目前篩選的標的" : "目前沒有符合的標的"}</div>`}</div>`;

  const root = $(`tab-${lane}`);
  root.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    f.stages.has(b.dataset.stage) ? f.stages.delete(b.dataset.stage) : f.stages.add(b.dataset.stage);
    renderLane(lane);
  }));
  root.querySelector("input").addEventListener("input", (e) => {
    f.query = e.target.value;
    const pos = e.target.selectionStart;
    renderLane(lane);
    const input = root.querySelector("input");
    input.focus();
    input.setSelectionRange(pos, pos);
  });
  root.querySelector("select").addEventListener("change", (e) => { f.sort = e.target.value; renderLane(lane); });
}

// ---------- 頁首：大盤與資料狀態 ----------

function renderHeader() {
  const r = state.data.regime || {};
  const [cls, label, hint] = REGIME[r.status] || REGIME.unknown;
  $("regime").innerHTML = `${pill(cls, label)} <span class="muted small">${esc(hint)}</span>`;
  const age = state.data.generated_at ? (Date.now() - new Date(state.data.generated_at)) / 864e5 : null;
  const errors = (state.data.fetch_errors || []).length;
  const stale = age != null && age > 4;
  $("updated").innerHTML = state.data.generated_at
    ? `${stale ? "⚠ 資料已過期 · " : ""}更新 ${esc(new Date(state.data.generated_at).toLocaleString("zh-TW", { hour12: false }))}${errors ? ` · ${errors} 天日曆抓取失敗` : ""}`
    : "尚未產生資料";
}

// ---------- 規則頁（數字取自 config.json，避免與程式脫節）----------

function renderRules() {
  const c = state.cfg;
  $("tab-rules").innerHTML = `<div class="rules">
  <h2>規則</h2><p class="muted">來源為兩支 Qullamaggie 影片字幕；影片未給數字的參數標為暫定（見 config.json）。</p>

  <h3>財報後 PEAD／EP（影片 A＋B）</h3>
  <ol>
    <li><b>選股</b>：財報反應日收盤漲 ≥${c.min_gain_pct}%、量 ≥ 前 50 日均量 ${c.min_volume_ratio} 倍，且財報日在反應日前 ${c.link_window_days} 天內。反應日後<b>收盤</b>跌破反應日最低價 → 失效。</li>
    <li><b>整理</b>（五條件）：自高點回落 ≤${c.pullback_max_pct}%；無放量大陰線（跌 ≥${c.dist_drop_pct}% 且量 ≥${c.dist_volume_ratio} 倍）且每天波動小於平時；收盤不破 50 日線；至少橫盤 ${c.min_consolidation_days} 日；近 3 日振幅與量縮小。</li>
    <li><b>買點與止損</b>：越過整理區最高點（盤中即算，跳空以開盤價）；止損＝突破前最後 ${c.stop_lookback_bars} 日最低點；風險 ≤${c.risk_adr_mult}×ADR 理想、≤${c.risk_adr_max_mult}×ADR 可接受，超過先不做。</li>
    <li><b>EP 品質</b>（影片 B）：開盤跳空 ≥${c.min_gain_pct}%、EPS 超預期、財報前 ${c.ep_prior_bars} 日漲幅 ≤${c.ep_prior_run_pct}%（沒怎麼漲）。營收年增、指引、最被盯著的數字須人工查新聞稿。</li>
  </ol>

  <h3>動能突破（影片 B）</h3>
  <ol>
    <li><b>強勢股</b>：1／3／6 個月漲幅各自排名，任一最前 ${c.momentum_top_pct}%；ADR ≥${c.min_adr_pct}%；20 日平均成交額 ≥${(c.min_dollar_volume_usd / 1e6).toFixed(0)}M 美元；超過 ${c.bo_max_signals} 檔只列動能最強者。</li>
    <li><b>形態</b>：前段高點前 ${c.bo_max_runup_bars} 日內自低點漲 ≥${c.bo_min_runup_pct}%；之後橫盤 ${c.bo_min_cons_days}～${c.bo_max_cons_days} 日、低點墊高、振幅收窄、貼著向上均線且收盤不破 50 日線。</li>
    <li><b>買點與止損</b>：越過橫盤區最高點；突破當天止損＝當天最低點，寬度不得超過 ADR。</li>
    <li><b>不買</b>：離下次財報 ≤${c.bo_earnings_block_days} 天。</li>
  </ol>

  <h3>大盤濾網（兩支影片共用）</h3>
  <ul><li>納指 10 日線在 20 日線上且兩線向上 → 放手做；任一條走平或拐頭 → 小心；10 日線在下且兩線向下 → 少做、小做。</li></ul>

  <h3>買進之後（本站不計算，供參考）</h3>
  <ul>
    <li>買進後 3～5 天先賣一半左右，止損移到成本價；剩餘跟 10 日線（快）或 20 日線（慢）、50 日線（更慢），第一次<b>收盤</b>跌破就全賣。</li>
    <li>下次財報前，剩餘倉位獲利不到 20% 先賣。</li>
    <li>倉位：單筆虧損 0.25%～1.5% 帳戶；單檔 10～20%（小帳戶 ≤25%）；最多同時 4 檔；不用槓桿；股數＝可虧金額 ÷（買價－止損）。</li>
  </ul>

  <h3>本站沒有做的</h3>
  <ul>
    <li>盤前跳空榜與開盤區間高點（需盤前與分鐘資料）：EP 的「當天買」需自行用券商軟體；本站在收盤後以日線評估，EP 跳空日隔天起轉為 PEAD 觀察。</li>
    <li>拋物線做空：影片 B 本人也不建議小帳戶做。</li>
    <li>營收與指引品質：無可靠自動化資料源。</li>
  </ul></div>`;
}

// ---------- 啟動 ----------

async function init() {
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#tabs button, .tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $(`tab-${b.dataset.tab}`).classList.add("active");
  }));
  try {
    [state.data, state.cfg] = await Promise.all([fetch("data/screen.json").then((r) => r.json()), fetch("config.json").then((r) => r.json())]);
  } catch (e) {
    $("updated").textContent = "資料讀取失敗：請透過 HTTP 伺服器開啟，且 data/screen.json 需存在";
    return;
  }
  state.data.pead ||= [];
  state.data.breakout ||= [];
  renderHeader();
  renderLane("pead");
  renderLane("breakout");
  renderRules();
}

init();
