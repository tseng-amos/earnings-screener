/* 迷你 K 線（純 SVG，無外部套件）。rows：[日期, 開, 高, 低, 收, 量, 50 日線]。 */
(function (root) {
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function candleSvg(rows, opts = {}) {
    if (!rows || rows.length < 3) return "";
    const W = 580, H = 300, PL = 6, PR = 118, PT = 16, PB = 22, VOL = 48, GAP = 8;
    const priceH = H - PT - PB - VOL - GAP;
    const levels = (opts.levels || []).filter((l) => l.y != null);
    const lows = rows.map((r) => r[3]), highs = rows.map((r) => r[2]);
    let lo = Math.min(...lows, ...levels.map((l) => l.y)), hi = Math.max(...highs, ...levels.map((l) => l.y));
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad; hi += pad;
    const n = rows.length, step = (W - PL - PR) / n, bw = Math.max(2, step * 0.62);
    const x = (i) => PL + (i + 0.5) * step;
    const y = (v) => PT + (hi - v) / (hi - lo) * priceH;
    const maxVol = Math.max(...rows.map((r) => r[5] || 0), 1);
    const volTop = PT + priceH + GAP;
    let out = "";

    // 日期標記（財報反應日、突破日）
    for (const m of opts.marks || []) {
      const i = rows.findIndex((r) => r[0] === m.date);
      if (i < 0) continue;
      out += `<line class="ch-mark" x1="${x(i)}" x2="${x(i)}" y1="${PT}" y2="${volTop + VOL}"/>` +
             `<text class="ch-note" x="${Math.min(x(i) + 3, W - PR - 36)}" y="${PT + 8}">${esc(m.label)}</text>`;
    }
    // 成交量
    rows.forEach((r, i) => {
      const h = Math.max(1, (r[5] || 0) / maxVol * VOL);
      out += `<rect class="ch-vol ${r[4] >= r[1] ? "up" : "down"}" x="${x(i) - bw / 2}" y="${volTop + VOL - h}" width="${bw}" height="${h}"/>`;
    });
    // 50 日線
    const sma = rows.map((r, i) => (r[6] == null ? null : `${x(i).toFixed(1)},${y(r[6]).toFixed(1)}`)).filter(Boolean);
    if (sma.length > 1) out += `<polyline class="ch-sma" points="${sma.join(" ")}"/>`;
    // K 線
    rows.forEach((r, i) => {
      const up = r[4] >= r[1], top = y(Math.max(r[1], r[4])), bot = y(Math.min(r[1], r[4]));
      out += `<line class="ch-wick ${up ? "up" : "down"}" x1="${x(i)}" x2="${x(i)}" y1="${y(r[2])}" y2="${y(r[3])}"/>` +
             `<rect class="ch-body ${up ? "up" : "down"}" x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}"/>`;
    });
    // 價位線：標籤放右側邊欄，互相錯開避免重疊
    const placed = [];
    for (const l of [...levels].sort((a, b) => y(a.y) - y(b.y))) {
      let ty = y(l.y) + 3;
      for (const p of placed) if (Math.abs(ty - p) < 16) ty = p + 16;
      placed.push(ty);
      out += `<line class="ch-level ${esc(l.cls)}" x1="${PL}" x2="${W - PR}" y1="${y(l.y)}" y2="${y(l.y)}"/>` +
             `<text class="ch-label ${esc(l.cls)}" x="${W - PR + 5}" y="${ty}">${esc(l.label)} ${l.y.toFixed(2)}</text>`;
    }
    out += `<text class="ch-axis" x="${PL}" y="${H - 5}">${esc(rows[0][0].slice(5))}</text>` +
           `<text class="ch-axis" x="${W - PR}" y="${H - 5}" text-anchor="end">${esc(rows[n - 1][0].slice(5))}</text>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || "近期日線")}">${out}</svg>`;
  }

  const api = { candleSvg };
  if (typeof module !== "undefined") module.exports = api; else root.Chart2 = api;
})(typeof window !== "undefined" ? window : globalThis);
