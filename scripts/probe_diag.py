#!/usr/bin/env python3
import sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
from browser import Browser, load_json

settings = load_json("settings.json")
sel = load_json("selectors.json")
at = sel["audience_targeting"]
PLAN, SHOP = "19566204", "1891367695"

def try_edit(b, launch):
    url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={launch}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(3.5)
    ef = None
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            ef = f; break
    ok = ef is not None and ef.locator("text=推广人群").count() > 0
    print(f"  launchId={launch}: cpm-edit={'Y' if ef else 'N'} 推广人群={'Y' if ok else 'N'}")
    return ef

with Browser(settings) as b:
    # 取前几个 promotion 的 launchId
    b.goto(settings["portal_url"]); time.sleep(2)
    b.click(sel["nav"]["promotion_center"], "推广中心"); time.sleep(1.5)
    b.click(sel["nav"]["promotion_center_sub"], "推广中心(子菜单)"); time.sleep(1.5)
    b.click(sel["nav"]["smart_display"], "智选展位"); time.sleep(3)
    lf=None
    for f in b.page.frames:
        if f.locator(at["promotion_row"]).count()>0:
            lf=f; break
    launches = []
    for r in lf.locator(at["promotion_row"]).all()[:4]:
        launches.append(r.get_attribute("data-row-key"))
    print("launchIds:", launches)
    for lid in launches:
        try_edit(b, lid)
