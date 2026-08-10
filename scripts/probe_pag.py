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
    lf = None
    deadline = time.time() + 15
    while time.time() < deadline:
        for f in b.page.frames:
            if "promo-list" in (f.url or ""):
                lf = f; break
        if lf:
            break
        time.sleep(0.5)
    if not lf:
        print("promo-list frame 未找到"); sys.exit(1)
    # 所有含 pagination 的 class
    info = lf.evaluate("""() => {
        const uls = [...document.querySelectorAll('ul')].map(u => u.className).filter(c => /pag|more|page/i.test(c));
        const lis = [...document.querySelectorAll('li')].map(l => l.className).filter(c => /pag|page/i.test(c)).slice(0, 20);
        const pag = document.querySelector('.merchant-pagination');
        return {uls, lis, pagHtml: pag ? pag.outerHTML.slice(0, 1200) : 'NO .merchant-pagination'};
    }""")
    print("ul pagination classes:", info["uls"])
    print("li pagination classes:", info["lis"])
    print("pag html:", info["pagHtml"])
