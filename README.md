# 美股突破篩選簿

依 Qullamaggie 兩支影片（財報季 PEAD、交易系統完整版）的條件，每天美股收盤後自動掃描，網站在 GitHub Pages：

- **財報後 PEAD／EP**：財報後大漲爆量 → 等整理收斂 → 向上突破；同時用 EP 條件打分（跳空、EPS、盈利年增、營收）
- **動能突破**：1／3／6 個月漲幅排名最前 2%、大漲後橫盤、等突破；突破後固定買點與止損，不會隔天漂移
- **持倉**：寫在 `data/holdings.csv`，依影片的出場規則提示該分批、該賣出（只看收盤）
- 詳情有迷你 K 線、倉位試算（帳戶、單筆風險 → 股數）、人工確認勾選與筆記；清單可匯出自選股與提醒價

## 發布
1. 推到 `main`；Settings → Pages → Source 選 **GitHub Actions**。
2. Actions → 「更新資料並部署 Pages」→ Run workflow。之後每個交易日自動跑兩次（美股收盤後、台灣早上）。
3. 選配：在 Settings → Secrets 新增 `FINNHUB_API_KEY`，營收預期會自動補上（影片要求營收與 EPS 都超預期）。

## 持倉
編輯 `data/holdings.csv`（在 GitHub 網頁直接改即可，存檔後會自動重算）：

```
symbol,entry_date,entry_price,shares,stop,partial_done,exit_ma,note
TSLA,2026-10-12,431.5,40,420,N,10,突破單
```
`partial_done` 已分批填 `Y`；`exit_ma` 是跟蹤均線（10／20／50）。

## 本機
```bash
pip install -r requirements.txt -r requirements-dev.txt
python scripts/run_tests.py                 # 單元測試（全部離線）
python scripts/fetch_data.py --out data     # 產生 data/screen.json（需要網路）
python -m http.server 8000                  # 開 http://localhost:8000
python -m playwright install chromium && python scripts/smoke_test.py --site .   # 冒煙測試（網站與 data/ 要在同一資料夾）
```

## 資料來源
| 項目 | 來源 |
|---|---|
| 財報日曆、EPS、去年同期 EPS | Nasdaq（主） |
| 營收實際／預期 | Finnhub（選配，需金鑰） |
| 股票池 | Nasdaq 股票清單（價格、成交額預篩）＋有財報的標的 |
| 日線價格 | Yahoo Finance（批次）→ Stooq → Nasdaq（逐檔備援）；每檔記錄實際來源 |
| 大盤 | ^IXIC；失敗時用 QQQ 近似 |

價格與日曆有快取（`actions/cache`），每週整批重抓一次。盤中執行時，尚未收盤的最後一根 K 線會被丟棄。資料成功率低於 80% 或完全沒資料時，工作流程會失敗，網站保留上一次的內容。

## 參數與判斷
全部在 `config.json`，`_來源` 欄位註明每個數字出自哪支影片；影片沒給的數字標「暫定」。規則頁會依 `config.json` 自動顯示。

## 限制
- 日線看不到盤中：影片的 EP「開盤區間高點」、盤前漲幅榜需自己盯盤。
- 營收、指引、電話會議沒有穩定的免費來源，用「人工確認」勾選（只存在你的瀏覽器，可匯出備份）。
- Stooq、Nasdaq 備援與 Finnhub 的回應格式是依公開文件寫的，首次執行請看 Actions 日誌確認有抓到。
- 不含通知與回測。
