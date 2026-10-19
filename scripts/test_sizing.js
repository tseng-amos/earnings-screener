// 倉位試算測試（影片 B 的例子）：node scripts/test_sizing.js
const assert = require("assert");
const S = require("../assets/sizing.js");
const cfg = { sizing_small_account_usd: 100000, sizing_small_max_position_pct: 25, sizing_max_position_pct: 20 };
assert.strictEqual(S.defaultMaxPosPct(50000, cfg), 25);
assert.strictEqual(S.defaultMaxPosPct(100000, cfg), 20);
// 影片 B：10 萬美元、單筆風險 1%、買價 20、止損 19 → 1000 股、佔帳戶 20%
let p = S.position({ account: 100000, riskPct: 1, entry: 20, stop: 19, maxPosPct: 20 });
assert.deepStrictEqual([p.shares, p.cost, p.capped], [1000, 20000, false]);
assert.ok(Math.abs(p.posPct - 20) < 1e-9 && Math.abs(p.lossUsd - 1000) < 1e-9);
// 止損很近 → 受單檔上限限制
p = S.position({ account: 50000, riskPct: 1, entry: 100, stop: 99.5, maxPosPct: 25 });
assert.deepStrictEqual([p.shares, p.capped], [125, true]);
// 不合理輸入回傳 null
for (const bad of [{ account: 0, riskPct: 1, entry: 10, stop: 9, maxPosPct: 20 }, { account: 1000, riskPct: 1, entry: 10, stop: 11, maxPosPct: 20 }, { account: 1000, riskPct: 0, entry: 10, stop: 9, maxPosPct: 20 }])
  assert.strictEqual(S.position(bad), null);
console.log("sizing: 通過");
