#!/usr/bin/env python3
"""探测未修改(暂无推广人群)的推广人群区：下滑后看完整结构、可点击元素、点击后打开的抽屉。"""
import sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
from browser import Browser, load_json

settings = load_json("settings.json")
sel = load_json("selectors.json")
PLAN, SHOP = "19566204", "1891367695"
LAUNCH_UNMOD = "54519845"   # 未修改示例
LAUNCH_MOD = "54519819"     # 已修改示例

def goto_edit(b, launch):
    url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={launch}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            return f
    return None

def dump_crowd(b, ef, launch):
    print(f"\n########## launch={launch} ##########")
    # 滚动到推广人群
    item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
    try:
        item.scroll_into_view_if_needed()
        time.sleep(1)
    except Exception as e:
        print("scroll fail:", str(e)[:60])
    # 所有匹配的 cpm-edit-item
    items = ef.locator("div.cpm-edit-item:has-text('推广人群')").all()
    print(f"匹配 div.cpm-edit-item 数: {len(items)}")
    for i, it in enumerate(items):
        try:
            html = it.evaluate("el => el.outerHTML")
            print(f"--- item[{i}] HTML({len(html)}) ---")
            print(html[:1800])
        except Exception as e:
            print(f"item[{i}] err: {e}")
    # 该区域内所有 button
    try:
        btns = item.locator("button").all()
        print(f"crowd item 内 button 数: {len(btns)}")
        for b_ in btns:
            print("  btn:", b_.inner_text().strip()[:30])
    except Exception as e:
        print("btns err:", str(e)[:80])
    # 尝试点击 crowd item 内容区(若有 N-direction-error 或内容)
    try:
        err = item.locator(".N-direction-error").first
        if err.count() > 0:
            print("点击 .N-direction-error ...")
            err.evaluate("el => el.click()")
            time.sleep(2.5)
            # 检查抽屉是否打开
            drawer_open = ef.locator("text=定向人群").count() + ef.locator("text=智选人群").count()
            print("点击后 定向/智选人群 计数:", drawer_open)
    except Exception as e:
        print("click err:", str(e)[:80])

with Browser(settings) as b:
    ef = goto_edit(b, LAUNCH_UNMOD)
    if ef:
        dump_crowd(b, ef, LAUNCH_UNMOD)
    ef2 = goto_edit(b, LAUNCH_MOD)
    if ef2:
        dump_crowd(b, ef2, LAUNCH_MOD)
