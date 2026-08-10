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
TARGET = ["14-24岁", "25~29岁", "30~34岁", "男", "女", "轰趴", "密室", "团建拓展", "新奇体验"]
PID = sys.argv[1] if len(sys.argv) > 1 else "推广20260805884"

with Browser(settings) as b:
    nav = sel["nav"]
    # 门户导航（最稳）：portal → 推广中心 → 智选展位
    b.goto(settings["portal_url"])
    time.sleep(2.5)
    b.click(nav["promotion_center"], "推广中心")
    time.sleep(1.5)
    b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
    time.sleep(1.5)
    b.click(nav["smart_display"], "智选展位")
    time.sleep(3.5)
    lf = None
    for f in b.page.frames:
        if "promo-list" in (f.url or "") and f.locator(at["promotion_row"]).count() > 0:
            lf = f
            break
    if lf is None:
        print("列表 frame 未找到，frames:", [f.url[:60] for f in b.page.frames])
        sys.exit(1)
    lid = None
    for r in lf.locator(at["promotion_row"]).all():
        try:
            if r.locator(at["promotion_id_link"]).first.inner_text().strip() == PID:
                lid = r.get_attribute("data-row-key")
                break
        except Exception:
            pass
    print("launchId:", lid)
    url = (f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html"
           f"?planId={at['edit_plan_id']}&launchId={lid}&shopId={at['edit_shop_id']}"
           f"&brandId=0&isStatic=false&editCreative=false")
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    ef = None
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            ef = f
            break
    item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
    item.scroll_into_view_if_needed()
    time.sleep(1)
    st = item.evaluate(
        """el => {
            const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
            const r1 = radios.find(r => String(r.value) === '1');
            const d = (el.querySelector('.static-target-detail-wrapper') || {innerText: ''}).innerText || '';
            return {targeted: r1 ? r1.checked : false, detail: d};
        }"""
    )
    print("定向:", st["targeted"])
    print("标签详情:", st["detail"][:250])
    missing = [t for t in TARGET if t not in st["detail"]]
    print("缺失标签:", missing if missing else "无，完整")
