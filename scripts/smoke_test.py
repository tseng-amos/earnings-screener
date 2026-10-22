"""冒煙測試：用無頭瀏覽器開已建好的網站，確認「能載入、能切換、沒有爛掉的字」。
單元測試只測 Python；前端壞掉（資料欄位改名、JS 錯誤、整頁空白）時它們仍是綠燈，這支在部署前把這類問題擋下。

用法：python scripts/smoke_test.py --site site
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

BAD_WORDS = ("undefined", "NaN", "[object", "null")


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # 不要把每個請求都印出來
        pass


def serve(directory: str) -> tuple[http.server.ThreadingHTTPServer, int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="site")
    site = Path(parser.parse_args().site)
    data = json.loads((site / "data" / "screen.json").read_text(encoding="utf-8"))
    server, port = serve(str(site))
    failures: list[str] = []
    check = lambda ok, msg: ok or failures.append(msg)  # noqa: E731

    with sync_playwright() as p:
        browser = p.chromium.launch()
        errors: list[str] = []
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_selector("#tab-pead table.list")

        regime = page.inner_text("#regime")
        check("讀取資料中" not in regime and "載入失敗" not in regime, f"頁首狀態不正常：{regime[:60]}")
        check(data["asof"] in regime, "頁首沒有顯示價格截止日")

        # 各分頁都能開，且畫面上沒有 undefined / NaN 之類的壞字
        for tab in ("pead", "breakout", "hold", "rules", "settings"):
            page.click(f'#tabs button[data-tab="{tab}"]')
            check(page.is_visible(f"#tab-{tab}"), f"分頁 {tab} 沒有顯示")
            text = page.inner_text(f"#tab-{tab}")
            for word in BAD_WORDS:
                check(word not in text, f"分頁 {tab} 出現「{word}」")

        # 兩條線：階段按鈕上的數字要等於實際列數；點列後詳情要更新、有圖、有倉位試算
        for lane in ("pead", "breakout"):
            page.click(f'#tabs button[data-tab="{lane}"]')
            for i in range(len(page.query_selector_all(f"#stages-{lane} button"))):
                button = page.query_selector(f"#stages-{lane} button >> nth={i}")   # 點擊會重繪按鈕，每次重新取得
                expected = int(button.query_selector(".n").inner_text())
                label = button.inner_text().strip()
                button.click()
                rows = len(page.query_selector_all(f"#list-{lane} tbody tr.row"))
                check(rows == expected, f"{lane}「{label}」按鈕數字 {expected} ≠ 列數 {rows}")
                if rows:
                    page.click(f"#list-{lane} tbody tr.row >> nth=0")
                    detail = page.inner_text(f"#detail-{lane}")
                    check(len(detail) > 80, f"{lane} 詳情是空的")
                    for word in BAD_WORDS:
                        check(word not in detail, f"{lane} 詳情出現「{word}」")

        # 財報季選單：每一季都能切換、載入歷史、不出現壞字，再切回當季
        page.click('#tabs button[data-tab="pead"]')
        if page.query_selector("#season-pead"):
            values = page.eval_on_selector_all("#season-pead option", "e => e.map(o => o.value)")
            current = data["seasons"]["current"]
            for value in values:
                page.select_option("#season-pead", value)
                page.wait_for_function("() => !document.querySelector('#lede-pead').innerText.includes('讀取歷史紀錄中')")
                text = page.inner_text("#tab-pead")
                check("載入失敗" not in text, f"財報季 {value} 載入失敗")
                for word in BAD_WORDS:
                    check(word not in text, f"財報季 {value} 出現「{word}」")
                expected = next((s["count"] for s in data["seasons"]["list"] if s["id"] == value), 0)
                shown = sum(int(b.query_selector(".n").inner_text()) for b in page.query_selector_all("#stages-pead button")) if value != current else None
                check(shown is None or shown == expected, f"財報季 {value}：階段按鈕合計 {shown} ≠ 摘要 {expected}")
            page.select_option("#season-pead", current)

        # 倉位試算要隨「設定」改變
        page.click('#tabs button[data-tab="settings"]')
        page.fill("#set-account", "250000")
        page.dispatch_event("#set-account", "input")
        first_lane = next((l for l in ("pead", "breakout") if data[l]), None)
        if first_lane:
            page.click(f'#tabs button[data-tab="{first_lane}"]')
            sizing = page.query_selector(f"#detail-{first_lane} .sizing")
            check(sizing is None or "$250,000" in sizing.inner_text(), "改帳戶金額後倉位試算沒有更新")

        # 窄螢幕：點一列要開抽屜、Esc 要關閉
        mobile = browser.new_page(viewport={"width": 390, "height": 800})
        mobile.on("pageerror", lambda e: errors.append(str(e)))
        mobile.goto(f"http://127.0.0.1:{port}/index.html")
        mobile.wait_for_selector("#tab-pead table.list")
        lane = next((l for l in ("pead", "breakout") if data[l]), None)
        if lane:
            mobile.click(f'#tabs button[data-tab="{lane}"]')
            if mobile.query_selector(f"#list-{lane} tbody tr.row"):
                mobile.click(f"#list-{lane} tbody tr.row >> nth=0")
                check(mobile.eval_on_selector(f"#detail-{lane}", "e => e.classList.contains('open')"), "手機點列後抽屜沒有打開")
                mobile.keyboard.press("Escape")
                check(not mobile.eval_on_selector(f"#detail-{lane}", "e => e.classList.contains('open')"), "Esc 沒有關閉抽屜")
        check(mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "手機版出現橫向捲動")

        check(not errors, f"瀏覽器錯誤：{errors[:3]}")
        browser.close()
    server.shutdown()
    if failures:
        print("冒煙測試失敗：")
        for f in failures:
            print("  ✗", f)
        return 1
    print("冒煙測試通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
