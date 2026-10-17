# 財報季選股

美日財報日曆 + Qullamaggie 跳空／PEAD 整理突破篩選。純靜態網頁，資料由 GitHub Actions 每日更新。

## 發布到 GitHub Pages
1. 建立 repo 並推送本專案到 `main`。
2. Settings → Pages → Source 選 **GitHub Actions**。
3. Actions →「更新資料並部署 Pages」→ Run workflow（之後每個交易日自動跑兩次）。

## 本機預覽
```bash
pip install -r requirements.txt
python scripts/test_screen.py     # 選股邏輯單元測試
python scripts/fetch_data.py      # 產生 data/calendar.json、data/screen.json
python -m http.server 8000        # 開 http://localhost:8000
```

## 結構
- `config.json`：篩選參數（前端與 Python 共用）
- `scripts/screen.py`：選股邏輯；`scripts/fetch_data.py`：抓資料並輸出 JSON
- `data/universe_jp.txt`：日本掃描清單；`data/jp_earnings_manual.csv`：日本財報日手動補充
- `assets/`：前端；`.github/workflows/`：更新與部署

## 資料來源
美股財報日曆：Nasdaq 公開 API；日本：JPX 決算發表予定日 Excel；價格：Yahoo Finance（yfinance）。
