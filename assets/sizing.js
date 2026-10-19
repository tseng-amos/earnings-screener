/* 倉位試算（影片 B）：股數＝可虧金額 ÷（買價－止損），再受單檔上限限制。純函式，可在 node 測試。 */
(function (root) {
  // 影片：單檔 10～20%，10 萬美元以下的小帳戶至多 25%；不用槓桿
  function defaultMaxPosPct(account, cfg) {
    return account < cfg.sizing_small_account_usd ? cfg.sizing_small_max_position_pct : cfg.sizing_max_position_pct;
  }

  function position({ account, riskPct, entry, stop, maxPosPct }) {
    if (!(account > 0) || !(riskPct > 0) || !(entry > 0) || !(stop > 0) || stop >= entry) return null;
    const riskUsd = account * riskPct / 100;
    const byRisk = Math.floor(riskUsd / (entry - stop));
    const byCap = Math.floor(account * maxPosPct / 100 / entry);
    const shares = Math.max(Math.min(byRisk, byCap), 0);
    return {
      shares,
      cost: shares * entry,
      posPct: shares * entry / account * 100,
      lossUsd: shares * (entry - stop),
      lossPct: shares * (entry - stop) / account * 100,
      capped: byCap < byRisk,
      stopPct: (entry - stop) / entry * 100,
    };
  }

  const api = { defaultMaxPosPct, position };
  if (typeof module !== "undefined") module.exports = api; else root.Sizing = api;
})(typeof window !== "undefined" ? window : globalThis);
