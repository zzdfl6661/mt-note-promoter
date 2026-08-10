#!/usr/bin/env python3
"""探测定向人群抽屉结构（带入口重试）。"""
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from browser import Browser, load_json  # noqa: E402


def _wait_for_table(b, row_sel, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in b.page.frames:
            try:
                if f.locator(row_sel).count() > 0:
                    return f
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _wait_for_edit_frame(b, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                return f
        time.sleep(0.3)
    return None


def _enter_edit(b, sel, max_try=3):
    at = sel["audience_targeting"]
    nav = sel["nav"]
    for attempt in range(1, max_try + 1):
        try:
            b.goto(sel and b.settings["portal_url"])
            time.sleep(2)
            b.page.keyboard.press("Escape")
            time.sleep(0.5)
            b.click(nav["promotion_center"], "推广中心")
            time.sleep(1.5)
            b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
            time.sleep(1.5)
            b.click(nav["smart_display"], "智选展位")
            time.sleep(3)
            b.wait_for(at["promotion_list_page_indicator"], state="visible", timeout_ms=15000)
            list_frame = _wait_for_table(b, at["promotion_row"])
            if not list_frame:
                continue
            # 点击第一个推广 ID：先 force click，失败用原生 click
            link = list_frame.locator(at["promotion_row"]).first.locator(at["promotion_id_link"]).first
            try:
                link.click(force=True, timeout=5000)
            except Exception:
                link.evaluate("el => el.click()")
            time.sleep(3)
            ef = _wait_for_edit_frame(b, timeout_s=12.0)
            if ef:
                return ef
        except Exception as e:
            print(f"[入口] 第{attempt}次失败: {e}")
    return None


def main():
    settings = load_json("settings.json")
    sel = load_json("selectors.json")

    with Browser(settings) as b:
        edit_frame = _enter_edit(b, sel)
        if not edit_frame:
            print("未进入编辑页"); return
        print(f"[OK] 编辑页: {edit_frame.url[:90]}")

        # 点击推广人群 查看/修改
        crowd_item = edit_frame.locator("div.cpm-edit-item:has-text('推广人群')").first
        crowd_item.locator("button.edit-btn").first.evaluate("el => el.click()")
        time.sleep(2.5)

        # 点击定向人群 radio
        for cand in ["text=定向人群", "div:has-text('定向人群') input"]:
            try:
                loc = edit_frame.locator(cand).first
                if loc.count() > 0:
                    loc.evaluate("el => el.click()")
                    print(f"[OK] 点击定向人群: {cand}")
                    break
            except Exception as e:
                print(f"点击定向人群失败 {cand}: {e}")
        time.sleep(1.5)

        # 点击自定义人群标签（多种文本尝试）
        for txt in ["自定义人群标签", "自定义人群", "人群标签", "自定义"]:
            try:
                loc = edit_frame.locator(f"text={txt}").first
                if loc.count() > 0:
                    loc.evaluate("el => el.click()")
                    print(f"[OK] 点击 tab: {txt}")
                    break
            except Exception:
                pass
        time.sleep(2)

        print("\n=== 定向人群 元素祖先链 ===")
        chain = edit_frame.locator("text=定向人群").first.evaluate("""el => {
            const out = [];
            let cur = el;
            for (let i = 0; i < 10 && cur; i++) {
                out.push(cur.tagName + '.' + (cur.className||'').toString().split(' ').slice(0,3).join('.'));
                cur = cur.parentElement;
            }
            return out.join('\\n');
        }""")
        print(chain)

        print("\n=== 含'自定义'的文本元素 ===")
        txts = edit_frame.evaluate("""() => [...document.querySelectorAll('*')]
            .filter(e => e.children.length === 0 && e.innerText && e.innerText.includes('自定义'))
            .map(e => e.innerText.trim().slice(0,50))""")
        for t in sorted(set(txts)):
            print(f"  {t!r}")

        print("\n=== 抽屉容器 HTML（含定向人群的最小容器）===")
        html = edit_frame.evaluate("""() => {
            const dir = [...document.querySelectorAll('*')].find(e => e.children.length && e.innerText && e.innerText.includes('定向人群') && e.innerText.length < 3000);
            return dir ? dir.outerHTML.slice(0, 4000) : 'NOT FOUND';
        }""")
        print(html)


if __name__ == "__main__":
    main()
