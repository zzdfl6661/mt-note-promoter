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
    b.page.goto(settings["portal_url"], wait_until="domcontentloaded")
    time.sleep(4)
    print("url:", b.page.url[:90])
    # 检查登录标志：推广中心菜单
    logged = False
    for f in b.page.frames:
        try:
            if f.locator("text=推广中心").count() > 0:
                logged = True
                break
        except Exception:
            pass
    print("登录状态:", "已登录" if logged else "未登录（需要人工登录）")
    if logged:
        # 直接跳 promo-list
        b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html",
                    wait_until="domcontentloaded")
        time.sleep(5)
        for f in b.page.frames:
            try:
                print(f"  {f.url[:70]} rows={f.locator('tr.merchant-table__row').count()}")
            except Exception:
                pass
