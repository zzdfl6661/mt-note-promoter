#!/usr/bin/env python3
"""探测推广人群区域当前值（定向人群 vs 智选人群）的 DOM 差异。"""
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

with Browser(settings) as b:
    # 进入推广列表取前几个 launchId
    b.goto(settings["portal_url"]); time.sleep(2)
    b.click(sel["nav"]["promotion_center"], "推广中心"); time.sleep(1.5)
    b.click(sel["nav"]["promotion_center_sub"], "推广中心(子菜单)"); time.sleep(1.5)
    b.click(sel["nav"]["smart_display"], "智选展位"); time.sleep(3)
    lf = None
    for f in b.page.frames:
        if f.locator(at["promotion_row"]).count() > 0:
            lf = f; break
    rows = lf.locator(at["promotion_row"]).all()[:4]
    launches = [(r.locator(at["promotion_id_link"]).first.inner_text().strip(), r.get_attribute("data-row-key")) for r in rows]
    print("promotions:", launches)

    for pid, lid in launches:
        url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={lid}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
        b.page.goto(url, wait_until="domcontentloaded")
        time.sleep(3.5)
        ef = None
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                ef = f; break
        if not ef:
            print(f"\n[{pid}] 未进入编辑页"); continue
        # 推广人群 item 完整 HTML
        item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
        try:
            print(f"\n===== {pid} (launchId={lid}) =====")
            # 定向人群 radio 是否 checked（JS 判定）
            state = item.evaluate("""el => {
                const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
                return {
                    radios: radios.map(r => ({v: r.value, checked: r.checked})),
                    detail: (el.querySelector('.static-target-detail-wrapper') || el.querySelector('.N-direction-diy-detail') || {innerText:''}).innerText.slice(0, 300),
                };
            }""")
            print(f"radios: {state['radios']}")
            print(f"detail: {state['detail'].replace(chr(10), ' | ')}")
            html = item.evaluate("el => el.outerHTML")
            print(html[:2200])
        except Exception as e:
            print(f"\n[{pid}] 读取失败: {e}")
