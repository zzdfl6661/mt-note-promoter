#!/usr/bin/env python3
"""打开已定向人群推广的人群抽屉，切到自定义人群标签，dump 复选框结构。"""
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
LAUNCH = "54519679"  # 推广2026080524e (精选人群)

with Browser(settings) as b:
    url = f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html?planId={PLAN}&launchId={LAUNCH}&shopId={SHOP}&brandId=0&isStatic=false&editCreative=false"
    b.page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    ef = None
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            ef = f; break
    if not ef:
        print("未进入编辑页"); sys.exit(1)

    item = ef.locator("div.cpm-edit-item:has-text('推广人群')").first
    item.scroll_into_view_if_needed(); time.sleep(1)

    # 点击 查看/修改
    btn = item.locator("button.edit-btn").first
    btn.evaluate("el => el.click()")
    time.sleep(2.5)

    # 找抽屉内 tab：精选人群 / 自定义人群标签 / 上传人群文件
    print("=== 抽屉内 tab 候选 ===")
    for kw in ["精选人群", "自定义人群标签", "上传人群文件", "自定义人群", "人群标签"]:
        try:
            els = ef.locator(f"text={kw}").all()
            print(f"  '{kw}': {len(els)}")
            for e in els[:2]:
                print("    ", e.evaluate("el => el.tagName + '.' + (el.className||'') + ' text=' + (el.innerText||'').slice(0,30)"))
        except Exception as ex:
            print(f"  '{kw}' err {str(ex)[:50]}")

    # 点击 自定义人群标签
    for kw in ["自定义人群标签", "自定义人群", "人群标签"]:
        try:
            loc = ef.locator(f"text={kw}").first
            if loc.count() > 0:
                loc.evaluate("el => el.click()")
                print(f"[OK] 点击 {kw}")
                break
        except Exception:
            pass
    time.sleep(2)

    print("\n=== 复选框(input[type=checkbox]) ===")
    inputs = ef.locator("input[type=checkbox]").all()
    print(f"总数: {len(inputs)}")
    for inp in inputs[:40]:
        try:
            info = inp.evaluate("""el => {
                const wrap = el.closest('label') || el.parentElement;
                return {
                    checked: el.checked,
                    label: wrap ? (wrap.innerText || '').replace(/\\s+/g,' ').trim().slice(0,40) : '',
                    html: (wrap||el).outerHTML.slice(0,300),
                };
            }""")
            print(f"  checked={info['checked']} label={info['label']!r}")
        except Exception:
            pass

    print("\n=== 目标标签文本数量 ===")
    for kw in ["14-24岁", "25-29岁", "30-34岁", "男", "女", "轰趴", "密室", "团建拓展", "新奇体验"]:
        try:
            print(f"  {kw}: {ef.locator(f'text={kw}').count()}")
        except Exception:
            pass

    # dump 一个复选框的 HTML 结构样本
    print("\n=== 第一个复选框 HTML 样本 ===")
    try:
        if inputs:
            print(inputs[0].evaluate("el => { const p = el.closest('label') || el.parentElement; return p.outerHTML; }")[:600])
    except Exception:
        pass
