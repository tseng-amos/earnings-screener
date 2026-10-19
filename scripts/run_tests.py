"""執行全部離線測試：python scripts/run_tests.py（Python 單元測試 ＋ 倉位試算的 node 測試）。"""
import importlib
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
total = 0
for name in ("test_screen", "test_sources", "test_pipeline"):
    module = importlib.import_module(name)
    for fn_name in sorted(n for n in dir(module) if n.startswith("test_")):
        getattr(module, fn_name)()
        total += 1
    print(f"{name}: 通過")
node = shutil.which("node")
if node:
    subprocess.run([node, str(HERE / "test_sizing.js")], check=True)
else:
    print("找不到 node，略過倉位試算測試")
print(f"全部通過（Python {total} 項）")
