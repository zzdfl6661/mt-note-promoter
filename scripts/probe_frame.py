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
nav = sel["nav"]; at = sel["audience_targeting"]

with Browser(settings) as b:
    b.goto(settings["portal_url"]); time.sleep(2)
    b.click(nav["promotion_center"], "推广中心"); time.sleep(1.5)
    b.click(nav["promotion_center_sub"], "推广中心(子菜单)"); time.sleep(1.5)
    b.click(nav["smart_display"], "智选展位"); time.sleep(3)
    for i, f in enumerate(b.page.frames):
        try:
            rows = f.locator(at["promotion_row"]).count()
            pags = f.locator("li.merchant-pagination__item").count()
            act = f.locator("li.merchant-pagination__item--active").count()
            print(f"[{i}] {f.url[:75]}")
            print(f"     rows={rows} pagination_items={pags} active={act}")
        except Exception as e:
            print(f"[{i}] {f.url[:75]} ERR {str(e)[:50]}")
