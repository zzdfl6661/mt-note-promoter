#!/usr/bin/env python3
"""批量修改智选展位推广人群为定向人群 + 自定义人群标签（独立脚本，复用 CDP 登录态）。

用法:
  python scripts/batch_target_audience.py [--dry-run] [--max-pages 13] [--start-page 1] [--clear-progress]

说明:
  - 直达 URL 进入每个推广的编辑页（planId/shopId 见 selectors.json audience_targeting 段）。
  - 先滚动到「推广人群」区：若定向人群已勾选 → 记入数据库跳过（含用户手动改过的）；
    否则点定向人群 → 查看/修改 → 自定义人群标签 → 勾选 9 项标签 → 保存设置 → 保存并提交。
  - 提交后页面自动回到推广列表第 1 页，脚本用 data/batch_target_audience_progress.json
    记录已处理推广 ID，重跑自动跳过（断点续跑）。
"""

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from browser import Browser, load_json, ROOT  # noqa: E402

PROGRESS_FILE = ROOT / "data" / "batch_target_audience_progress.json"

# 自定义人群标签目标（用户属性 + 用户兴趣）。注意：25~29岁/30~34岁 用波浪线 ~
TARGET_TAGS = [
    ("14-24岁", "年龄14-24"),
    ("25~29岁", "年龄25-29"),
    ("30~34岁", "年龄30-34"),
    ("男", "性别男"),
    ("女", "性别女"),
    ("轰趴", "兴趣轰趴"),
    ("密室", "兴趣密室"),
    ("团建拓展", "兴趣团建拓展"),
    ("新奇体验", "兴趣新奇体验"),
]


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {"processed_ids": [], "failed": []}


def save_progress(progress: dict):
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def _wait_for_table(b: Browser, sel: dict, timeout_s: float = 15.0):
    """等待推广列表(promo-list)表格渲染出数据行，返回所在 frame。
    必须限定 promo-list frame，避免把共享预算等页面的同名表格误认为列表。"""
    row_selector = sel["audience_targeting"]["promotion_row"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in b.page.frames:
            if "promo-list" not in (f.url or ""):
                continue
            try:
                if f.locator(row_selector).count() > 0:
                    return f
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _extract_promotions(frame, sel: dict, processed: set) -> list[tuple[str, str]]:
    """提取未处理的 (推广ID, launchId)。launchId 来自行 data-row-key。"""
    at = sel["audience_targeting"]
    rows = frame.locator(at["promotion_row"]).all()
    result = []
    for row in rows:
        try:
            link = row.locator(at["promotion_id_link"])
            if link.count() > 0:
                pid = link.first.inner_text().strip()
            else:
                m = re.search(r"推广\d+", row.inner_text())
                pid = m.group(0) if m else None
            lid = row.get_attribute("data-row-key")
            if pid and lid and pid not in processed:
                result.append((pid, lid))
        except Exception:
            continue
    return result


def _list_has_pagination(list_frame) -> bool:
    """分页器是否渲染出页码（直达 URL 有时 total=0 无页码，此时无法翻页）。"""
    try:
        return list_frame.evaluate("""() => {
            const lis = [...document.querySelectorAll('li')];
            return lis.some(li => (li.className || '').toString().includes('pagination__item'));
        }""")
    except Exception:
        return False


def _navigate_to_promotion_list(b: Browser, sel: dict, settings: dict):
    """进入推广列表：优先用当前 promo-list 页；否则 goto + 等表格完整渲染（rows>=10 且有分页），失败则门户导航。"""
    at = sel["audience_targeting"]
    row_sel = at["promotion_row"]

    def _list_ready(lf):
        try:
            n = lf.locator(row_sel).count()
            return n >= 5  # 放宽：表格行够即可，分页器渲染抖动不阻塞
        except Exception:
            return False

    frame = _wait_for_table(b, sel, timeout_s=8.0)
    if frame and _list_ready(frame):
        print("[OK] 使用当前推广列表页")
        return frame
    for attempt in range(3):
        try:
            b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html",
                        wait_until="domcontentloaded")
            time.sleep(3)
            try:
                b.page.reload(wait_until="domcontentloaded")
            except Exception:
                pass
            time.sleep(3)
            frame = _wait_for_table(b, sel, timeout_s=10.0)
            if frame and _list_ready(frame):
                print(f"[OK] 已进入推广列表 (第{attempt + 1}次)")
                return frame
            print(f"[导航] 第{attempt + 1}次列表未完整渲染")
        except Exception as e:
            print(f"[导航] 第{attempt + 1}次异常: {str(e)[:50]}")
    # 兜底：门户导航
    try:
        nav = sel["nav"]
        b.goto(settings["portal_url"])
        time.sleep(2)
        b.click(nav["promotion_center"], "推广中心")
        time.sleep(1.5)
        b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
        time.sleep(1.5)
        b.click(nav["smart_display"], "智选展位")
        time.sleep(3)
        frame = _wait_for_table(b, sel, timeout_s=10.0)
        if frame:
            print("[OK] 门户导航进入推广列表")
            return frame
    except Exception as e:
        print(f"[导航] 门户兜底失败: {str(e)[:50]}")
    raise RuntimeError("列表未加载")


def _get_active_page(list_frame) -> int | None:
    try:
        return list_frame.evaluate("""() => {
            const lis = [...document.querySelectorAll('li')];
            const a = lis.find(li => (li.className || '').toString().includes('pagination__item--active'));
            const n = a ? parseInt((a.innerText || '').trim(), 10) : NaN;
            return isNaN(n) ? null : n;
        }""")
    except Exception:
        return None


def _set_page_size(list_frame, value: str = "100条/页"):
    """设置每页条数（选项文本带空格如'100 条/页'，需去空格匹配）。"""
    try:
        list_frame.evaluate("""() => {
            const li = [...document.querySelectorAll('li')].find(l => (l.className||'').includes('page-sizer'));
            if (li) li.click(timeout=5000);
        }""")
        time.sleep(0.8)
        js = """v => {
            const opts = [...document.querySelectorAll('li')].filter(l => l.innerText.replace(/\\s+/g, '').trim() === v);
            if (opts[0]) opts[0].click(timeout=5000);
        }"""
        list_frame.evaluate(js, value)
        time.sleep(2)
    except Exception:
        pass


def _click_next(list_frame) -> bool:
    """点下一页按钮。"""
    try:
        return list_frame.evaluate("""() => {
            const a = [...document.querySelectorAll('li')].find(l => (l.className||'').includes('pagination__next'));
            if (a && !(a.className||'').includes('--disabled')) { a.click(timeout=5000); return true; }
            return false;
        }""")
    except Exception:
        return False


def _click_page_number(list_frame, n: int) -> bool:
    """JS 点击指定页码（避免页面引擎对 CSS 伪类的兼容问题）。"""
    try:
        return list_frame.evaluate("""n => {
            const items = [...document.querySelectorAll('li')].filter(li => {
                const c = (li.className || '').toString();
                return c.includes('pagination__item');
            });
            const target = items.find(li => parseInt(li.innerText.trim()) === n);
            if (target) { target.click(timeout=5000); return true; }
            return false;
        }""", n)
    except Exception:
        return False


def _goto_page(b: Browser, list_frame, sel: dict, n: int):
    """直接 JS 点击页码 li（极速：不做 jump-next 复杂循环）。"""
    if _get_active_page(list_frame) == n:
        return list_frame
    if not _click_page_number(list_frame, n):
        raise RuntimeError(f"找不到页码 {n}")
    time.sleep(1.2)
    return _wait_for_table(b, sel, timeout_s=6.0) or list_frame


def _goto_edit(b: Browser, at: dict, launch_id: str):
    """直达 URL 进入编辑页，返回 cpm-edit frame。
    当前页已是该 launchId 的 cpm-edit 则直接复用（跳过 goto，避免导航卡住）。"""
    plan = at.get("edit_plan_id", "19566204")
    shop = at.get("edit_shop_id", "1891367695")
    url = (f"https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html"
           f"?planId={plan}&launchId={launch_id}&shopId={shop}"
           f"&brandId=0&isStatic=false&editCreative=false")
    # 已在目标编辑页 → 直接复用
    for f in b.page.frames:
        if "cpm-edit" in (f.url or "") and launch_id in (f.url or ""):
            time.sleep(0.8)
            return f
    for attempt in range(3):
        try:
            time.sleep(0.4)  # 等上一条导航结束
            b.page.goto(url, wait_until="domcontentloaded", timeout=12000)
            break
        except Exception as e:
            print(f"  [导航重试{attempt + 1}] {str(e)[:60]}")
            time.sleep(1)
    deadline = time.time() + 10
    while time.time() < deadline:
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                time.sleep(0.6)  # 等表单渲染
                return f
        time.sleep(0.3)
    return None


def _crowd_item(edit_frame):
    """定位推广人群 item。多轮等待+滚动触发懒加载（页面渲染抖动时不再立即失败）。"""
    sel = "div.cpm-edit-item:has-text('推广人群')"
    for attempt in range(6):
        item = edit_frame.locator(sel).first
        if item.count() > 0:
            try:
                item.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.4)
            except Exception:
                pass
            return item
        # 滚动页面触发懒加载
        try:
            edit_frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)
            edit_frame.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        time.sleep(1.0)
    return None


def _crowd_is_targeted(item) -> bool:
    """定向人群 radio(value=1) 是否勾选。"""
    try:
        return item.evaluate("""el => {
            const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
            const r1 = radios.find(r => String(r.value) === '1');
            return r1 ? r1.checked : false;
        }""")
    except Exception:
        return False


def _click_targeted_radio(item):
    """点击定向人群 radio（用 Playwright 真实点击触发 React 事件）。"""
    try:
        radios = item.locator("input[type=radio]").all()
        for r in radios:
            try:
                if r.get_attribute("value") == "1":
                    r.click(timeout=5000)
                    time.sleep(1.2)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    # 兜底：JS click
    try:
        item.evaluate("""el => {
            const radios = [...el.querySelectorAll('input')].filter(i => i.type === 'radio');
            const r1 = radios.find(r => String(r.value) === '1');
            if (r1 && !r1.checked) { r1.click(timeout=5000); }
        }""")
        time.sleep(1.2)
        return True
    except Exception as e:
        print(f"[WARN] 点击定向人群 radio 失败: {e}")
        return False


def _open_crowd_drawer(edit_frame):
    """点击查看/修改按钮，等抽屉打开。宽松版：点击后固定等待，不再严格校验文本。"""
    try:
        item = edit_frame.locator("div.cpm-edit-item:has-text('推广人群')").first
        btn = item.locator("button.edit-btn").first
        if btn.count() == 0:
            # 可能刚点定向 radio 后按钮还在渲染，滚动再找一次
            item.scroll_into_view_if_needed(timeout=3000)
            time.sleep(1)
            btn = item.locator("button.edit-btn").first
        if btn.count() == 0:
            print("[WARN] 未找到查看/修改按钮")
            return False
        btn.scroll_into_view_if_needed(timeout=3000)
        time.sleep(0.3)
        btn.evaluate("el => { el.scrollIntoView(); el.click(); }")
        print("[OK] 点击查看/修改")
    except Exception as e:
        print(f"[WARN] 点击查看/修改失败: {e}")
        return False
    time.sleep(1.0)  # 等抽屉动画
    return True


def _click_custom_tag_tab(edit_frame):
    """点击抽屉里的「自定义人群标签」页签。找不到则滚动顶部/底部再找。"""
    def _try(kws):
        for kw in kws:
            try:
                loc = edit_frame.locator(f"text={kw}").first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed(timeout=3000)
                    time.sleep(0.3)
                    loc.evaluate("el => el.click()")
                    print(f"[OK] 点击页签: {kw}")
                    time.sleep(1.5)
                    return True
            except Exception:
                continue
        return False
    if _try(["自定义人群标签", "自定义人群", "人群标签"]):
        return True
    _scroll_drawer_top(edit_frame)
    time.sleep(0.8)
    if _try(["自定义人群标签", "自定义人群", "人群标签"]):
        return True
    _scroll_drawer_bottom(edit_frame)
    time.sleep(0.8)
    return _try(["自定义人群标签", "自定义人群", "人群标签"])


def _scroll_drawer_bottom(edit_frame):
    """把抽屉所有大滚动容器滚到底，让懒加载/折叠内容渲染出来。"""
    try:
        edit_frame.evaluate("""() => {
            const divs = [...document.querySelectorAll('div')].filter(e => {
                const s = getComputedStyle(e);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200;
            });
            divs.sort((a, b) => b.scrollHeight - a.scrollHeight);
            divs.forEach(d => { d.scrollTop = d.scrollHeight; });
        }""")
    except Exception:
        pass


def _scroll_drawer_gradual_slow(edit_frame):
    """模拟人工慢滚动：少量分段滚动并停留，触发虚拟列表渲染。
    注意：滚动太快/太频繁会导致页面状态不稳定找不到元素，控制在 5 步 × 0.4s。"""
    try:
        for _ in range(5):
            edit_frame.evaluate("""() => {
                const divs = [...document.querySelectorAll('div')].filter(e => {
                    const s = getComputedStyle(e);
                    return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200;
                });
                for (const d of divs) {
                    if (d.scrollTop + d.clientHeight >= d.scrollHeight - 5) { d.scrollTop = 0; }
                    else { d.scrollTop += Math.max(120, Math.round(d.clientHeight * 0.4)); }
                }
            }""")
            time.sleep(0.4)
    except Exception:
        pass


def _scroll_drawer_gradual(edit_frame):
    """对抽屉所有滚动容器逐步滚动（顶→底→顶），触发虚拟列表/懒加载渲染全部内容。"""
    try:
        edit_frame.evaluate("""() => {
            const divs = [...document.querySelectorAll('div')].filter(e => {
                const s = getComputedStyle(e);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200;
            });
            for (const d of divs) {
                const step = Math.max(200, Math.round(d.clientHeight * 0.5));
                for (let y = 0; y <= d.scrollHeight; y += step) {
                    d.scrollTop = y;
                }
                d.scrollTop = 0;
            }
        }""")
    except Exception:
        pass


def _scroll_drawer_top(edit_frame):
    """滚回抽屉顶部（用户属性区在顶部，虚拟列表可能卸载了上方节点）。"""
    try:
        edit_frame.evaluate("""() => {
            const divs = [...document.querySelectorAll('div')].filter(e => {
                const s = getComputedStyle(e);
                return (s.overflowY === 'auto' || s.overflowY === 'scroll') && e.clientHeight > 200;
            });
            divs.forEach(d => { d.scrollTop = 0; });
        }""")
    except Exception:
        pass


def _ensure_industry_expanded(edit_frame, industry_name: str = "休闲娱乐"):
    """点击左侧行业分类（如"休闲娱乐"）展开右侧标签。滚动抽屉让分类和右侧都渲染。"""
    _scroll_drawer_bottom(edit_frame)
    time.sleep(0.8)
    try:
        loc = edit_frame.locator(f"text={industry_name}").first
        if loc.count() > 0:
            loc.scroll_into_view_if_needed(timeout=3000)
            time.sleep(0.5)
            loc.evaluate("el => el.click()")
            print(f"[OK] 展开行业: {industry_name}")
            time.sleep(1.2)
            _scroll_drawer_bottom(edit_frame)
            time.sleep(0.5)
            return True
    except Exception as e:
        print(f"[WARN] 展开行业 {industry_name} 失败: {e}")
    return False


def _check_tag(edit_frame, text: str, label: str) -> bool:
    """勾选指定标签。用 TreeWalker 精确匹配文本节点（整段相等），从该文本向上找 checkbox 点击。
    绝不误点页面其它含该词的地方（如笔记标题里的"密室"）。找不到则滚动重试。"""
    def _click_once():
        try:
            return edit_frame.evaluate("""(text) => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                const candidates = [];
                while (walker.nextNode()) {
                    const t = walker.currentNode.textContent.replace(/\\s+/g, ' ').trim();
                    if (t === text) candidates.push(walker.currentNode.parentElement);
                }
                for (const el of candidates) {
                    let cur = el;
                    for (let i = 0; i < 8 && cur; i++) {
                        const cb = cur.querySelector && cur.querySelector('input[type=checkbox]');
                        if (cb) {
                            if (!cb.checked) { cb.click(timeout=5000); }
                            return cb.checked ? 'clicked' : 'checked';
                        }
                        cur = cur.parentElement;
                    }
                }
                return null;
            }""", text)
        except Exception:
            return None

    r = _click_once()
    if r == "clicked":
        print(f"[勾选] {label}")
        time.sleep(0.15)
        return True
    if r == "checked":
        print(f"[已勾选] {label}")
        return True
    # 未找到 → 慢滚动抽屉一次（触发虚拟列表/懒加载渲染）→ 最多 2 轮
    for _ in range(2):
        _scroll_drawer_gradual_slow(edit_frame)
        r = _click_once()
        if r == "clicked":
            print(f"[勾选] {label}")
            time.sleep(0.3)
            return True
        if r == "checked":
            print(f"[已勾选] {label}")
            return True
    print(f"[WARN] 未找到标签 {label}({text})")
    return False


def _crowd_detail_text(item) -> str:
    try:
        return item.evaluate("el => (el.querySelector('.static-target-detail-wrapper') || {innerText:''}).innerText") or ""
    except Exception:
        return ""


def _crowd_is_fully_tagged(item) -> bool:
    detail = _crowd_detail_text(item)
    return all(t in detail for t, _ in TARGET_TAGS)


def _dismiss_modals_after_submit(b):
    """提交后处理确认弹窗。轮询找「继续提交/继续」按钮并用真实点击触发 React 事件。
    返回是否点到了确认按钮。"""
    deadline = time.time() + 10
    while time.time() < deadline:
        for pg in b.ctx.pages:
            for f in pg.frames:
                try:
                    for btn_sel in [
                        ".merchant-modal button:has-text('继续提交')",
                        ".merchant-modal button:has-text('继续保存')",
                        "button:has-text('继续提交')",
                        ".merchant-modal button:has-text('继续')",
                        ".merchant-modal button:has-text('确定')",
                        "button:has-text('确定')",
                        "button:has-text('确认')",
                    ]:
                        btn = f.locator(btn_sel).first
                        if btn.count() > 0:
                            try:
                                btn.scroll_into_view_if_needed(timeout=3000)
                                btn.click(timeout=5000)  # 真实点击触发 React
                            except Exception:
                                btn.evaluate("el => el.click()")
                            print(f"[弹窗] 真实点击: {btn_sel}")
                            time.sleep(1.5)
                            return True
                except Exception:
                    continue
        time.sleep(0.5)
    return False


def _wait_submit_success(b, timeout_s=12):
    """等待提交成功标志：中间弹出「提交成功/保存成功」文本，或页面离开 cpm-edit。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not any("cpm-edit" in (f.url or "") for f in b.page.frames):
            return True
        for f in b.page.frames:
            try:
                for kw in ["提交成功", "保存成功", "提交完成"]:
                    if f.locator(f"text={kw}").count() > 0:
                        return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _return_to_list(b, sel, settings):
    """极速返回：优先等平台自动跳回列表；失败用浏览器后退回到固定页；最后才 goto。"""
    frame = _wait_for_table(b, sel, timeout_s=10.0)
    if frame:
        return frame
    try:
        b.page.go_back(wait_until="domcontentloaded")
        time.sleep(1.5)
        frame = _wait_for_table(b, sel, timeout_s=6.0)
        if frame:
            return frame
    except Exception:
        pass
    b.page.goto("https://e.dianping.com/app/peon-cpm-ncpm/html/promo-list.html",
                wait_until="domcontentloaded")
    time.sleep(2)
    return _wait_for_table(b, sel, timeout_s=6.0)


def _modify_crowd(b: Browser, edit_frame, at: dict, sel: dict, dry_run: bool):
    """完整修改流程：定向人群 → 查看/修改 → 自定义人群标签 → 勾选 → 保存设置 → 提交。"""
    # 1) 点定向人群 radio
    item = _crowd_item(edit_frame)
    if item is None:
        raise RuntimeError("推广人群区域未找到")
    if not _click_targeted_radio(item):
        raise RuntimeError("点击定向人群 radio 失败")

    # 2) 点查看/修改打开抽屉
    if not _open_crowd_drawer(edit_frame):
        raise RuntimeError("人群抽屉未打开")

    # 3) 点自定义人群标签页签
    if not _click_custom_tag_tab(edit_frame):
        print("[WARN] 未找到自定义人群标签页签，尝试直接勾选")
    time.sleep(1)

    # 4) 勾选用户属性（年龄+性别）：滚回顶部渲染用户属性区
    _scroll_drawer_top(edit_frame)
    time.sleep(0.8)
    checked_count = 0
    for text, label in TARGET_TAGS[:5]:
        if _check_tag(edit_frame, text, label):
            checked_count += 1

    # 5) 勾选用户兴趣：先展开"休闲娱乐"分类，再逐步滚动触发懒加载渲染，再勾
    _ensure_industry_expanded(edit_frame)
    _scroll_drawer_gradual(edit_frame)
    time.sleep(1.2)
    for text, label in TARGET_TAGS[5:]:
        if _check_tag(edit_frame, text, label):
            checked_count += 1

    # 保护：勾选数不足则放弃提交（避免空标签提交）
    if checked_count < 6:
        raise RuntimeError(f"标签勾选不足({checked_count}/9)，跳过提交")

    # 6) 保存设置 → 真实鼠标点击（最多重试 3 次），确认抽屉真正关闭（保存生效）才能继续
    drawer_closed = False
    for attempt in range(1, 4):
        save_btn = edit_frame.locator("button:has-text('保存设置')").first
        if save_btn.count() == 0:
            save_btn = edit_frame.locator("text=保存设置").first
        if save_btn.count() == 0:
            break
        try:
            save_btn.scroll_into_view_if_needed(timeout=3000)
            save_btn.click(timeout=5000)  # 真实鼠标点击，触发完整事件
        except Exception:
            try:
                save_btn.evaluate("el => el.click()")
            except Exception:
                pass
        print(f"[OK] 点击保存设置 (第{attempt}次)")
        time.sleep(1.5)  # 等抽屉关闭动画
        try:
            if edit_frame.locator("button:has-text('保存设置')").count() == 0:
                drawer_closed = True
                break
        except Exception:
            drawer_closed = True
            break
    if not drawer_closed:
        raise RuntimeError("保存设置后抽屉未关闭，保存可能未生效")
    print("[OK] 抽屉已关闭，保存设置已生效")

    # 7) 保存并提交 / 保存为草稿（真实鼠标点击）
    if dry_run:
        btn_sel = "text=保存为草稿"
        label = "保存为草稿"
    else:
        try:
            edit_frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.8)
        except Exception:
            pass
        btn_sel = "text=保存并提交"
        label = "保存并提交"
    btn = edit_frame.locator(btn_sel).first
    if btn.count() == 0:
        btn = edit_frame.locator("button:has-text('保存并提交')").first
    if btn.count() == 0:
        raise RuntimeError(f"未找到{label}按钮")
    try:
        btn.scroll_into_view_if_needed(timeout=3000)
        btn.click(timeout=5000)  # 真实鼠标点击
    except Exception:
        btn.evaluate("el => el.click()")
    print(f"[OK] 点击{label}")

    if dry_run:
        time.sleep(2)
        return
    # 真实提交：处理确认弹窗（真实点"继续提交"）→ 等"提交成功"（中间弹窗）或离开编辑页
    ok = False
    for _ in range(3):
        clicked = _dismiss_modals_after_submit(b)
        if _wait_submit_success(b, timeout_s=10):
            ok = True
            break
        if not clicked:
            break  # 没有确认弹窗，也没检测到成功 → 提交可能未触发
    if not ok:
        raise RuntimeError("提交后未确认成功（无继续提交弹窗或无提交成功提示）")
    print("[OK] 提交成功（已确认）")


def main():
    parser = argparse.ArgumentParser(description="批量修改智选展位推广人群")
    parser.add_argument("--dry-run", action="store_true", help="只保存草稿，不提交")
    parser.add_argument("--max-pages", type=int, default=13, help="最大翻页数，默认13")
    parser.add_argument("--start-page", type=int, default=1, help="起始页，默认1")
    parser.add_argument("--clear-progress", action="store_true", help="清空进度文件")
    args = parser.parse_args()

    settings = load_json("settings.json")
    sel = load_json("selectors.json")
    at = sel["audience_targeting"]
    progress = load_progress()

    if args.clear_progress:
        progress = {"processed_ids": [], "failed": []}
        save_progress(progress)
        print("[INFO] 进度已清空")

    processed = set(progress["processed_ids"])
    failed = progress["failed"]
    print(f"[模式] {'真实提交' if not args.dry_run else '保存草稿'}")
    print(f"[范围] 第 {args.start_page} 页 ~ 第 {args.max_pages} 页")
    print(f"[进度] 已处理 {len(processed)} 条")

    modified_count = 0
    skipped_count = 0
    fail_count = 0

    with Browser(settings) as b:
        # 自动接受所有弹窗/确认框（含 beforeunload 未保存提示），保证导航能跳走
        try:
            b.page.on("dialog", lambda d: d.accept())
        except Exception:
            pass
        list_frame = _navigate_to_promotion_list(b, sel, settings)
        _set_page_size(list_frame, "100条/页")
        list_frame = _wait_for_table(b, sel) or list_frame

        page_no = 1
        while page_no <= args.max_pages:
            print(f"\n===== 第 {page_no} 页 =====")
            list_frame = _wait_for_table(b, sel, timeout_s=6) or list_frame
            # 一次性提取本页全部未处理推广，逐个直达编辑页（改完不回列表）
            promotions = _extract_promotions(list_frame, sel, processed)
            print(f"[本页待处理] {len(promotions)} 条")
            for pid, lid in promotions:
                print(f"\n--- {pid} ---")
                try:
                    edit_frame = _goto_edit(b, at, lid)
                    if not edit_frame:
                        raise RuntimeError("未进入编辑页")
                    item = _crowd_item(edit_frame)
                    if item is None:
                        raise RuntimeError("推广人群区域未找到")
                    if _crowd_is_targeted(item):
                        print("[跳过] 已定向（自定义或精选人群）")
                        skipped_count += 1
                    else:
                        print("[修改]")
                        _modify_crowd(b, edit_frame, at, sel, dry_run=args.dry_run)
                        modified_count += 1
                    processed.add(pid)
                    progress["processed_ids"].append(pid)
                    save_progress(progress)
                except Exception as e:
                    print(f"[ERROR] {pid}: {str(e)[:60]}")
                    processed.add(pid)
                    progress["processed_ids"].append(pid)
                    failed.append({"id": pid, "page": page_no, "reason": str(e),
                                   "time": datetime.now().isoformat()})
                    progress["failed"] = failed
                    save_progress(progress)
                    fail_count += 1
                # 不回列表，直接进入下一个推广（循环顶部 goto 下一个 cpm-edit）

            # 本页全部改完 → 回列表一次 → 点 next 进下一页
            page_no += 1
            if page_no > args.max_pages:
                break
            list_frame = _return_to_list(b, sel, settings) or _navigate_to_promotion_list(b, sel, settings)
            if _click_next(list_frame):
                print(f"[翻页] → 第 {page_no} 页")
                time.sleep(1.5)
                list_frame = _wait_for_table(b, sel, timeout_s=6) or list_frame
            else:
                print("[INFO] 已是最后一页")
                break

    print("\n===== 汇总 =====")
    print(f"修改成功: {modified_count}")
    print(f"已定向跳过: {skipped_count}")
    print(f"失败: {fail_count}")
    print(f"数据库已记录: {len(processed)}")
    if failed:
        print("失败列表:")
        for item in failed[-20:]:
            print(f"  {item['id']} (第{item['page']}页): {item['reason']}")
    save_progress(progress)


if __name__ == "__main__":
    main()
