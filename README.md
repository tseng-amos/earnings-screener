# 美股突破篩選器

把兩支 Qullamaggie 影片的選股方式整合成一個靜態網站：收盤後掃描全市場，列出「該盯哪幾檔、買點與止損在哪」。

## 兩條篩選線

- **財報後 PEAD／EP**（影片 A＋B）：財報日漲 ≥10% 且量 ≥2 倍 → 整理收斂 → 突破整理區高點；另以 EP 條件（開盤跳空、EPS 超預期、財報前沒怎麼漲）評 0～3 分。
- **動能突破**（影片 B）：1／3／6 個月漲幅全市場前 2%、ADR ≥5%、日成交額 ≥500 萬美元 → 前段大漲後橫盤 10～42 日（低點墊高、振幅收窄、貼著向上均線）→ 突破橫盤上沿；離財報 ≤3 天不列。
- 共用大盤濾網：納指 10／20 日線（強勢放手做、轉弱警戒、弱勢少做小做）。

規則細節與每個參數的影片出處見網站「規則」頁與 `config.json` 的 `_來源`（影片沒給數字的標為暫定）。

## 使用

```powershell
pip install -r requirements.txt
python scripts/fetch_data.py --out data
python -m http.server 8000
```

瀏覽 `http://localhost:8000`（需透過 HTTP 伺服器，不能直接雙擊 HTML）。GitHub Actions 於美股收盤後每日更新並部署到 Pages。

## 測試

```powershell
python scripts/test_screen.py
python scripts/test_pipeline.py
```

## 限制

- 全部以日線、收盤後評估；盤前跳空榜、開盤區間高點需券商軟體。
- 動能排名的範圍是本站抓取的標的（Nasdaq 日曆上市值 ≥20 億美元者），不是全市場每一檔。
- 營收年增、指引、電話會議要人工對照財報新聞稿。
- Nasdaq 日曆為非官方 API，可能擋雲端 IP；抓取失敗時流程中止並保留上次部署。

數據與訊號僅供研究，不構成投資建議。
