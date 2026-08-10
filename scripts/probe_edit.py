#!/usr/bin/env python3
"""探测编辑页(推广人群/定向人群抽屉)的 DOM 结构，用于回填选择器。"""
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


def main():
    settings = load_json("settings.json")
    sel = load_json("selectors.json")
    nav = sel["nav"]
    at = sel["audience_targeting"]

    with Browser(settings) as b:
        b.goto(settings["portal_url"])
        time.sleep(2)
        b.click(nav["promotion_center"], "推广中心")
        time.sleep(1.5)
        b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
        time.sleep(1.5)
        b.click(nav["smart_display"], "智选展位")
        time.sleep(3)
        b.wait_for(at["promotion_list_page_indicator"], state="visible", timeout_ms=15000)
        list_frame = _wait_for_table(b, at["promotion_row"])

        # 点击第一个推广 ID
        first_id = list_frame.locator(at["promotion_row"]).first.locator(at["promotion_id_link"]).first.inner_text().strip()
        print(f"点击推广: {first_id}")

        # 用原生 click 触发 JS 路由跳转
        try:
            list_frame.locator(at["promotion_row"]).first.locator(at["promotion_id_link"]).first.evaluate("el => el.click()")
            print("[OK] 已触发原生 click")
        except Exception as e:
            print(f"原生 click 失败: {e}")
        time.sleep(3)

        # 搜索所有 frame 中是否出现“推广人群”
        print("\n=== 搜索所有 frame 中的 '推广人群' ===")
        found = False
        for pg in b.ctx.pages:
            for fr in pg.frames:
                try:
                    n = fr.locator("text=推广人群").count()
                    if n > 0:
                        print(f"  frame {fr.url[:80]} 含 '推广人群' x{n}")
                        found = True
                except Exception:
                    pass
        if not found:
            print("任何 frame 都未出现 '推广人群'")

        edit_frame = _wait_for_edit_frame(b, timeout_s=5.0)
        if not edit_frame:
            print("未检测到 cpm-edit frame")
            return
        print(f"编辑页 frame: {edit_frame.url}")

        # 诊断：列出所有页面与 frame
        print(f"\n当前页面数: {len(b.ctx.pages)}")
        for pi, pg in enumerate(b.ctx.pages):
            print(f"  page[{pi}] url={pg.url[:120]}")
            for fi, fr in enumerate(pg.frames):
                if fi < 6:
                    print(f"    frame[{fi}] {fr.url[:100]}")

        edit_frame = _wait_for_edit_frame(b, timeout_s=8.0)
        if not edit_frame:
            print("未进入编辑页(cpm-edit)")
            return
        print(f"编辑页 frame: {edit_frame.url}")

        # 查找包含“推广人群”的区域
        print("\n=== 查找推广人群区域 ===")
        crowd_candidates = edit_frame.locator("text=推广人群").all()
        print(f"含'推广人群'文本的元素数: {len(crowd_candidates)}")
        for i, c in enumerate(crowd_candidates[:5]):
            try:
                print(f"\n-- 候选 {i} HTML --")
                print(c.evaluate("el => el.outerHTML")[:1000])
            except Exception as e:
                print(f"  读取失败: {e}")

        # 点击“推广人群”所在 item 的 修改 按钮
        print("\n=== 点击推广人群修改按钮 ===")
        crowd_item = edit_frame.locator("div.cpm-edit-item:has-text('推广人群')").first
        try:
            edit_btn = crowd_item.locator("button.edit-btn").first
            print(f"找到按钮 HTML: {edit_btn.evaluate('el => el.outerHTML')[:200]}")
            edit_btn.evaluate("el => el.click()")
            print("已点击修改按钮(原生)")
        except Exception as e:
            print(f"点击失败: {e}")
        time.sleep(2)

        # 查找抽屉
        print("\n=== 查找人群抽屉 ===")
        drawer_cands = [
            "div.merchant-drawer__container",
            "div.cpm-edit-drawer",
            "div.merchant-drawer",
        ]
        drawer = None
        for d in drawer_cands:
            if edit_frame.locator(d).count() > 0:
                drawer = edit_frame.locator(d).first
                print(f"找到抽屉容器: {d}")
                break
        if drawer:
            try:
                print(drawer.evaluate("el => el.outerHTML")[:3000])
            except Exception as e:
                print(f"抽屉读取失败: {e}")

        # 跨所有 frame 搜索“定向人群”等关键词，定位抽屉所在 frame
        print("\n=== 跨 frame 搜索定向人群/自定义人群标签 ===")
        time.sleep(2)
        target_frame = None
        for pg in b.ctx.pages:
            for fr in pg.frames:
                for kw in ["定向人群", "智选人群", "自定义人群标签", "保存设置"]:
                    try:
                        n = fr.locator(f"text={kw}").count()
                        if n > 0:
                            print(f"  frame {fr.url[:70]} 含 '{kw}' x{n}")
                            target_frame = fr
                    except Exception:
                        pass

        if target_frame:
            print("\n=== 定向人群 元素及其祖先链 ===")
            try:
                chain = target_frame.locator("text=定向人群").first.evaluate("""el => {
                    const out = [];
                    let cur = el;
                    for (let i = 0; i < 10 && cur; i++) {
                        out.push(cur.tagName + '.' + (cur.className || '').toString().split(' ').slice(0,3).join('.'));
                        cur = cur.parentElement;
                    }
                    return out.join('\\n');
                }""")
                print(chain)
            except Exception as e:
                print(f"读取失败: {e}")

            print("\n=== 查找 tab 元素 (role=tab / class含tab) ===")
            try:
                tabs = target_frame.locator("[role=tab], div[class*=tab], li[class*=tab]").all()
                print(f"tab 元素数: {len(tabs)}")
                for t in tabs:
                    try:
                        print(f"  {t.evaluate('el => el.tagName + \".\" + (el.className||\"\") + \" | \" + (el.innerText||\"\").slice(0,40)')}")
                    except Exception:
                        pass
            except Exception as e:
                print(f"tab 查找失败: {e}")

            # 点击“自定义人群标签” tab
            print("\n=== 点击自定义人群标签 tab ===")
            try:
                tab = target_frame.locator("text=自定义人群标签").first
                if tab.count() > 0:
                    tab.evaluate("el => el.click()")
                    print("已点击")
                else:
                    print("未找到 自定义人群标签 tab，尝试其他文本")
                    for alt in ["自定义人群", "人群标签"]:
                        a = target_frame.locator(f"text={alt}").first
                        if a.count() > 0:
                            a.evaluate("el => el.click()")
                            print(f"已点击备选: {alt}")
                            break
            except Exception as e:
                print(f"点击 tab 失败: {e}")
            time.sleep(2)

            print("\n=== 复选框(input) 与附近文本 ===")
            inputs = target_frame.locator("input[type=checkbox]").all()
            print(f"复选框数量: {len(inputs)}")
            for inp in inputs[:40]:
                try:
                    label = inp.evaluate("""el => {
                        const lbl = el.closest('label') || el.parentElement;
                        const txt = lbl ? lbl.innerText : (el.getAttribute('aria-label') || '');
                        return txt.replace(/\\s+/g, ' ').trim().slice(0, 60);
                    }""")
                    print(f"  checkbox -> {label!r}")
                except Exception as e:
                    print(f"  (err {e})")

            print("\n=== 年龄/兴趣标签文本扫描 ===")
            for kw in ["14-24岁", "25-29岁", "30-34岁", "男", "女", "轰趴", "密室", "团建拓展", "新奇体验"]:
                n = target_frame.locator(f"text={kw}").count()
                print(f"  '{kw}': {n}")


if __name__ == "__main__":
    main()
