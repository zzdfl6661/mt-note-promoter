#!/usr/bin/env python3
"""探测抽屉滚动：打开已定向推广的人群抽屉→自定义人群标签→滚动→统计各标签出现情况。"""
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
PLAN, SHOP = at["edit_plan_id"], at["edit_shop_id"]
LAUNCH = "54514165"  # 刚提交的推广20260805aba

TARGETS = ["14-24岁", "25-29岁", "30-34岁", "男", "女", "轰趴", "密室", "团建拓展", "新奇体验"]

def _goto_edit(b, launch):
    url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={launch}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            return f
    return None

with Browser(settings) as b:
    ef = _goto_edit(b, LAUNCH)
    if not ef:
        print("未进入编辑页"); sys.exit(1)
    item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
    item.scroll_into_view_if_needed(); time.sleep(1)
    item.locator("button.edit-btn").first.evaluate("el => el.click()")
    time.sleep(2.5)
    # 点自定义人群标签
    for kw in ["自定义人群标签", "自定义人群", "人群标签"]:
        loc = ef.locator(f"text={kw}").first
        if loc.count() > 0:
            loc.evaluate("el => el.click()")
            print(f"点击 {kw}")
            break
    time.sleep(2)

    # 找抽屉滚动容器：包含'用户属性'文本的祖先中带 overflow 的元素
    info = ef.evaluate("""() => {
        const dir = [...document.querySelectorAll('*')].find(e => e.innerText && e.innerText.includes('自定义标签组合') || (e.innerText||'').includes('用户属性'));
        return null;
    }""")
    # 直接找滚动容器：所有 overflow-y auto/scroll 的 div
    scrolls = ef.evaluate("""() => {
        return [...document.querySelectorAll('div')]
            .filter(e => { const s = getComputedStyle(e); return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200; })
            .map(e => ({cls: (e.className||'').toString().slice(0,80), h: e.clientHeight, scrollH: e.scrollHeight}));
    }""")
    print("滚动容器候选:")
    for s in scrolls[:10]:
        print("  ", s)

    # 滚动前各标签计数
    print("\n滚动前:")
    for t in TARGETS:
        print(f"  {t}: {ef.locator(f'text={t}').count()}")

    # 尝试滚动最大的滚动容器
    ef.evaluate("""() => {
        const divs = [...document.querySelectorAll('div')].filter(e => { const s = getComputedStyle(e); return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200; });
        divs.sort((a,b) => b.scrollHeight - a.scrollHeight);
        if (divs[0]) { divs[0].scrollTop = divs[0].scrollHeight; }
        return divs.length;
    }""")
    time.sleep(1.5)
    print("\n滚动到底后:")
    for t in TARGETS:
        print(f"  {t}: {ef.locator(f'text={t}').count()}")
