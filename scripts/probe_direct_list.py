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
    time.sleep(4)
    print("page url:", b.page.url[:100])
    lf = None
    for f in b.page.frames:
        rows = f.locator("tr.merchant-table__row").count() if True else 0
        try:
            rows = f.locator("tr.merchant-table__row").count()
        except Exception:
            rows = -1
        if rows > 0:
            lf = f
            print(f"frame: {f.url[:80]} rows={rows}")
    if lf:
        # evaluate 分页状态
        info = lf.evaluate("""() => {
            const lis = [...document.querySelectorAll('li')];
            const pag = lis.filter(li => /pagination/.test((li.className||'').toString()));
            return {
                pagCount: pag.length,
                pagClasses: pag.map(p => ({c:(p.className||'').toString(), t:(p.innerText||'').trim()})).slice(0, 12),
            };
        }""")
        print("pag:", info["pagCount"])
        for p in info["pagClasses"]:
            print("  ", p)
