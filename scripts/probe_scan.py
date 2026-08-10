#!/usr/bin/env python3
"""扫描前12条推广的人群状态，找未修改示例并 dump。"""
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

def goto_edit(b, launch):
    url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={launch}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(3.5)
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            return f
    return None

def crowd_state(ef):
    item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
    if item.count() == 0:
        return None
    try:
        item.scroll_into_view_if_needed(); time.sleep(0.8)
        return item.evaluate("""el => {
            const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
            const detail = (el.querySelector('.static-target-detail-wrapper') || {innerText:''}).innerText.slice(0,80);
            return {
                radios: radios.map(r => ({v: r.value, checked: r.checked})),
                detail,
                html: el.outerHTML,
            };
        }""")
    except Exception as e:
        return {"err": str(e)[:80]}

with Browser(settings) as b:
    b.goto(settings["portal_url"]); time.sleep(2)
    b.click(sel["nav"]["promotion_center"], "推广中心"); time.sleep(1.5)
    b.click(sel["nav"]["promotion_center_sub"], "推广中心(子菜单)"); time.sleep(1.5)
    b.click(sel["nav"]["smart_display"], "智选展位"); time.sleep(3)
    lf = None
    for f in b.page.frames:
        if f.locator(at["promotion_row"]).count() > 0:
            lf = f; break
    rows = lf.locator(at["promotion_row"]).all()[:12]
    promo_list = []
    for r in rows:
        try:
            pid = r.locator(at["promotion_id_link"]).first.inner_text().strip()
            lid = r.get_attribute("data-row-key")
            promo_list.append((pid, lid))
        except Exception:
            pass
    print("collected:", promo_list)
    unmod_html = None
    for i, (pid, lid) in enumerate(promo_list):
        ef = goto_edit(b, lid)
        if not ef:
            print(f"[{i+1}] {pid} 未进入"); continue
        st = crowd_state(ef)
        if not st:
            print(f"[{i+1}] {pid} 无人群区"); continue
        radios = st.get("radios") or []
        checked_v = [r["v"] for r in radios if r["checked"]]
        kind = "定向✓" if "1" in checked_v else ("智选✓" if "2" in checked_v else "未选中?")
        print(f"[{i+1}] {pid} {kind} detail={st.get('detail','')[:50]!r}")
        if "1" not in checked_v and unmod_html is None:
            unmod_html = st.get("html", "")
    if unmod_html:
        print("\n===== 未修改推广人群 HTML =====")
        print(unmod_html[:2500])
    else:
        print("\n前12条全部已定向人群")
