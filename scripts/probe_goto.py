#!/usr/bin/env python3
import sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
from browser import Browser, load_json

settings = load_json("settings.json")
with Browser(settings) as b:
    b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html",
                wait_until="domcontentloaded")
    for wait in [2, 4, 6]:
        time.sleep(wait)
        print(f"\n--- after {wait}s ---")
        print("page url:", b.page.url[:90])
        for f in b.page.frames[:6]:
            try:
                rows = f.locator("tr.merchant-table__row").count()
            except Exception:
                rows = -1
            print(f"  {f.url[:70]} rows={rows}")
