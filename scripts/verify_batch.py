#!/usr/bin/env python3
"""批量验证指定推广的真实人群状态（定向+9标签完整与否），用于确认哪些没改。"""
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

IDS = sys.argv[1:] if len(sys.argv) > 1 else []

with Browser(settings) as b:
    # 门户导航进入列表（最稳）
    nav = sel["nav"]
    try:
        b.goto(settings["portal_url"])
        time.sleep(2.5)
        b.click(nav["promotion_center"], "推广中心")
        time.sleep(1.5)
        b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
        time.sleep(1.5)
        b.click(nav["smart_display"], "智选展位")
        time.sleep(3.5)
    except Exception as e:
        print(f"门户导航异常: {str(e)[:50]}")
    lf = None
    for f in b.page.frames:
        if "promo-list" in (f.url or "") and f.locator(at["promotion_row"]).count() > 5:
            lf = f
            break
    if lf is None:
        # 兜底：goto + reload
        b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html", wait_until="domcontentloaded")
        time.sleep(3)
        try:
            b.page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        time.sleep(4)
        for f in b.page.frames:
            if "promo-list" in (f.url or "") and f.locator(at["promotion_row"]).count() > 5:
                lf = f
                break
    if lf is None:
        print("列表未加载"); sys.exit(1)
    pid2lid = {}
    rows = lf.locator(at["promotion_row"]).all()
    for r in rows:
        try:
            pid = r.locator(at["promotion_id_link"]).first.inner_text().strip()
            lid = r.get_attribute("data-row-key")
            if pid and lid:
                pid2lid[pid] = lid
        except Exception:
            pass
    print(f"列表映射 {len(pid2lid)} 条 (rows={len(rows)})")

    for pid in IDS:
        lid = pid2lid.get(pid)
        if not lid:
            print(f"{pid} | 列表未找到")
            continue
        url = (f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html"
               f"?planId={at['edit_plan_id']}&launchId={lid}&shopId={at['edit_shop_id']}"
               f"&brandId=0&isStatic=false&editCreative=false")
        try:
            b.page.goto(url, wait_until="domcontentloaded", timeout=12000)
        except Exception:
            pass
        time.sleep(3)
        ef = None
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                ef = f
                break
        if not ef:
            print(f"{pid} | 未进入编辑页")
            continue
        item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
        try:
            item.scroll_into_view_if_needed(timeout=3000)
            time.sleep(0.5)
            st = item.evaluate("""el => {
                const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
                const r1 = radios.find(r => String(r.value) === '1');
                const d = (el.querySelector('.static-target-detail-wrapper') || {innerText: ''}).innerText || '';
                return {targeted: r1 ? r1.checked : false, detail: d};
            }""")
            missing = [t for t in TARGET if t not in st["detail"]]
            if st["targeted"] and not missing:
                print(f"{pid} | 已定向+9标签完整（已修改OK）")
            elif st["targeted"]:
                print(f"{pid} | 已定向但缺: {missing}")
            else:
                print(f"{pid} | 未定向（智选，未修改！）")
        except Exception as e:
            print(f"{pid} | 检查异常: {str(e)[:40]}")
