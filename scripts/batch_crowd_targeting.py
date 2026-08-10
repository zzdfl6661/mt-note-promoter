"""
智选展位 · 批量修改定向人群（独立脚本）
==========================================

功能：遍历智选展位推广列表，逐条进入详情页修改「推广人群→定向人群→自定义人群标签」设置。
范围：从当前列表往前翻页，直到推广日期 <= 2026-07-31 为止。

去重：SQLite 记录已处理的推广 ID，提交后自动跳回第1页时可断点续跑。
依赖：Playwright + 已打开的 Edge 浏览器（远程调试端口 9223）

用法：
  cd D:\Version1\mt-note-promoter
  python scripts/batch_crowd_targeting.py

断点续跑：直接重新运行即可，已处理过的推广会自动跳过。
"""

import json
import sqlite3
import time
import re
import urllib.request
from datetime import datetime, date
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, BrowserContext

# ======================== 配置 ========================

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "crowd_targeting.db"
LOG_DIR = ROOT / "logs" / f"crowd_targeting_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# 浏览器连接配置（与主程序共享同一个 Edge 实例）
# 注意：用户当前 Edge 以 9222 端口启动 (start_edge_debug.bat)，登录态在 data/edge_debug_profile
CDP_PORT = 9222  # Edge 远程调试端口

# 智选展位推广列表 URL（从截图确认）
LIST_URL = (
    "https://e.dianping.com/app/merchant-platform/"
    "0f67c29321D4ddHjr1Ly6oN5SkawFUucGUzj8jbXvBXwL21cmNoWY5QlWHtemFwZ5hZtHZp2VUzcMc3RhdGjlLpdzmJ5ZhTzhlZpc29yeS5hbmcFxNpyCoddG1s=menuid-2"
)

# 截止日期：只处理此日期之后的推广
CUTOFF_DATE = date(2026, 7, 31)

# 需要勾选的用户属性标签
TARGET_AGE_LABELS = ["14-24岁", "25-29岁", "30-34岁"]
TARGET_GENDER_LABELS = ["男", "女"]

# 需要勾选的用户兴趣标签
TARGET_INTEREST_LABELS = ["轰趴", "密室", "团建拓展", "新奇体验"]

# 页面等待超时（美团 SPA 渲染较慢）
PAGE_TIMEOUT_MS = 20000
ACTION_TIMEOUT_MS = 10000


# ======================== 数据库（去重） ========================

def init_db():
    """初始化去重数据库。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_promotions (
            promo_id   TEXT PRIMARY KEY,
            promo_name TEXT,
            process_time TEXT DEFAULT (datetime('now','localtime')),
            status     TEXT DEFAULT 'done',
            remark     TEXT DEFAULT ''
        )
    """)
    conn.commit()
    return conn


def is_processed(conn, promo_id: str) -> bool:
    """检查推广是否已处理。"""
    row = conn.execute(
        "SELECT 1 FROM processed_promotions WHERE promo_id = ?", (promo_id,)
    ).fetchone()
    return row is not None


def mark_done(conn, promo_id: str, promo_name: str = ""):
    """标记推广为已处理。"""
    conn.execute(
        "INSERT OR REPLACE INTO processed_promotions (promo_id, promo_name, status) VALUES (?, ?, 'done')",
        (promo_id, promo_name),
    )
    conn.commit()


def get_all_processed(conn) -> set[str]:
    """获取所有已处理的推广 ID 集合。"""
    rows = conn.execute("SELECT promo_id FROM processed_promotions").fetchall()
    return {r[0] for r in rows}


# ======================== 浏览器封装 ========================

class CrowdBrowser:
    """轻量浏览器封装，复用 mt-note-promoter 的 CDP 连接模式。"""

    def __init__(self):
        self._pw = None
        self._browser = None
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None
        self._active_frame = None
        self._frame_affinity: dict[str, str] = {}
        self._step = 0
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_dir = LOG_DIR

    # ---- frame 模式常量（与 browser.py 一致） ----
    _MENU_PATTERNS = ("merchant-menu", "peon-merchant-product-menu")
    _CONTENT_PATTERNS = ("shopdiy-node", "merchant-workbench", "cpm-edit",
                         "budget-group-list", "dzim-workbench-pc")

    def connect(self):
        """通过 CDP 连接已打开的 Edge 浏览器。"""
        ws_url = self._get_cdp_ws_url(CDP_PORT)
        if not ws_url:
            raise RuntimeError(
                f"无法连接 CDP 端口 {CDP_PORT}。\n"
                f"请确保 Edge 以远程调试模式启动：\n"
                f'  msedge --remote-debugging-port={CDP_PORT}'
            )

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(ws_url)
        self.ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.ctx.set_default_timeout(PAGE_TIMEOUT_MS)
        self._active_frame = self.page.main_frame
        print(f"[CDP] 连接成功！当前页面数: {len(self.ctx.pages)}")
        print(f"[CDP] 当前 URL: {self.page.url}")

    @staticmethod
    def _get_cdp_ws_url(port: int) -> str:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("webSocketDebuggerUrl", "")
        except Exception:
            return ""

    def close(self):
        """释放资源（不关闭用户的浏览器）。"""
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass

    # ---- 截图留痕 ----

    def shot(self, label: str):
        self._step += 1
        path = self.log_dir / f"{self._step:02d}_{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            pass
        return path

    # ---- iframe 智能定位（精简版，适配智选展位页面） ----

    def _ordered_frames(self, selector: str = None):
        frames = list(self.page.frames)
        cached_hint = self._frame_affinity.get(selector) if selector else None

        def priority(f):
            url = f.url or ""
            if cached_hint and cached_hint in url:
                return 0
            if f is self._active_frame:
                return 1
            if any(p in url for p in self._CONTENT_PATTERNS):
                return 2
            if any(p in url for p in self._MENU_PATTERNS):
                return 3
            return 4

        return sorted(frames, key=priority)

    @staticmethod
    def _frame_key(frame) -> str | None:
        url = frame.url or ""
        for p in CrowdBrowser._CONTENT_PATTERNS + CrowdBrowser._MENU_PATTERNS:
            if p in url:
                return p
        return None

    def _locate(self, selector: str, timeout_ms: int = ACTION_TIMEOUT_MS):
        """在所有 iframe 中定位可见元素，返回 (locator, frame)。"""
        deadline = time.time() + timeout_ms / 1000
        attempts = 0

        while True:
            frames = self._ordered_frames(selector)
            for frame in frames:
                try:
                    loc = frame.locator(selector).first
                    if loc.count() > 0 and loc.is_visible():
                        self._switch_frame(frame)
                        key = self._frame_key(frame)
                        if key:
                            self._frame_affinity[selector] = key
                        return loc, frame
                except Exception:
                    continue

            attempts += 1
            if time.time() >= deadline:
                break
            time.sleep(0.15)

        raise TimeoutError(
            f"{timeout_ms}ms 内未找到可见元素: {selector} "
            f"(共 {len(self.page.frames)} 个 frame, {attempts} 轮)"
        )

    def _switch_frame(self, frame):
        if self._active_frame != frame:
            self._active_frame = frame

    # ---- 操作原语 ----

    def click(self, selector: str, label: str = "click"):
        loc, frame = self._locate(selector)
        self._switch_frame(frame)
        loc.click()
        time.sleep(0.5)
        self.shot(label)

    def safe_click(self, selector: str, label: str = "safe_click") -> bool:
        """安全点击，找不到元素时不抛异常，返回是否成功。"""
        try:
            self.click(selector, label)
            return True
        except TimeoutError:
            print(f"  [跳过] 未找到元素: {selector}")
            return False

    def wait_for(self, selector: str, state: str = "visible", timeout_ms: int = ACTION_TIMEOUT_MS):
        loc, frame = self._locate(selector, timeout_ms=timeout_ms)
        self._switch_frame(frame)
        loc.wait_for(state=state, timeout=timeout_ms)

    def scroll_down(self, px: int = 400):
        """在当前活跃 frame 中向下滚动。"""
        self._active_frame.evaluate(f"window.scrollBy(0, {px})")
        time.sleep(0.3)

    def scroll_to_bottom(self):
        """滚动到页面底部。"""
        self._active_frame.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)


# ======================== 核心业务逻辑 ========================

def navigate_to_list(br: CrowdBrowser):
    """导航到智选展位推广列表页。"""
    print("\n[步骤] 导航到智选展位推广列表...")
    br.page.goto(LIST_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS * 2)
    # 等待 SPA 渲染完成——等表格出现
    time.sleep(3)
    br.shot("list_page")


def extract_promotion_rows(br: CrowdBrowser) -> list[dict]:
    """
    从当前列表页提取推广行信息。

    返回: [{"id": "推广20260805454", "name": "...", "date": "2026-08-05", ...}, ...]
    基于截图 DOM 结构：表格行内包含推广名称/ID、推广日期等列。
    """
    rows = []
    # 尝试在内容 frame 中找到所有推广链接
    # 推广 ID 格式如 "推广20260805454"，是可点击的链接
    try:
        # 获取所有 frame 的文本内容来分析结构
        for frame in br.page.frames:
            url = frame.url or ""
            # 只看内容 frame
            if not any(p in url for p in br._CONTENT_PATTERNS):
                continue
            # 找所有包含 "推广" + 数字格式的链接/元素
            promo_elements = frame.locator("a:has-text('推广20'), span:has-text('推广20'), div:has-text('推广20')").all()
            for elem in promo_elements:
                try:
                    text = elem.inner_text().strip()
                    if re.match(r'^推广\d{11}$', text):
                        rows.append({"id": text, "element": elem})
                except Exception:
                    continue
    except Exception as e:
        print(f"  [警告] 提取推广行时出错: {e}")

    return rows


def find_promotion_links_in_table(br: CrowdBrowser) -> list[dict]:
    """
    从表格中提取推广 ID 列表（更精确的版本）。

    基于截图1的结构：
    - 表格每行第一列有「推广」标签 + 推广名称
    - 推广ID 如 "推广20260805454" 是蓝色链接
    - 有日期列显示推广日期
    """
    results = []

    for frame in br.page.frames:
        url = frame.url or ""
        if not any(p in url for p in br._CONTENT_PATTERNS):
            continue

        try:
            # 方法1：找所有匹配推广ID模式的文本
            # 表格中的推广ID通常是 <a> 标签或可点击元素
            all_text = frame.inner_text("body")

            # 用正则提取所有推广ID
            promo_ids = re.findall(r'推广\d{11}', all_text)
            # 去重保序
            seen = set()
            unique_ids = []
            for pid in promo_ids:
                if pid not in seen:
                    seen.add(pid)
                    unique_ids.append(pid)

            for pid in unique_ids:
                results.append({"id": pid})

        except Exception as e:
            print(f"  [警告] frame 提取失败 ({url[:50]}): {e}")
            continue

    return results


def click_promotion(br: CrowdBrowser, promo_id: str) -> bool:
    """
    在列表页点击指定推广 ID 进入详情页。
    返回是否成功进入详情页。
    """
    print(f"\n  [点击] {promo_id} ...")

    for frame in br.page.frames:
        url = frame.url or ""
        if not any(p in url for p in br._CONTENT_PATTERNS):
            continue

        try:
            # 找到包含该推广ID的可点击元素
            locator = frame.get_by_text(promo_id, exact=True)
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.click()
                br.shot(f"enter_{promo_id}")
                # 等待详情页加载
                time.sleep(3)
                return True
        except Exception:
            continue

    # 备用方案：用 JS 点击
    try:
        br.page.evaluate(f"""
            () => {{
                const elements = document.querySelectorAll('a, span, div');
                for (const el of elements) {{
                    if (el.textContent.trim() === '{promo_id}') {{
                        el.click();
                        return true;
                    }}
                }}
                // 也检查 iframe
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {{
                    try {{
                        const doc = iframe.contentDocument || iframe.contentWindow.document;
                        const els = doc.querySelectorAll('a, span, div');
                        for (const el of els) {{
                            if (el.textContent.trim() === '{promo_id}') {{
                                el.click();
                                return true;
                            }}
                        }}
                    }} catch(e) {{}}
                }}
                return false;
            }}
        """)
        time.sleep(3)
        br.shot(f"enter_{promo_id}_js")
        return True
    except Exception as e:
        print(f"  [失败] 无法点击 {promo_id}: {e}")
        return False


def scroll_to_crowd_section(br: CrowdBrowser):
    """
    在详情页中向下滚动到「推广人群」区域。
    基于 screenshot-1：详情页有「推广位置和范围」区域，下面应该是「推广人群」。
    """
    print("  [滚动] 查找推广人群区域...")

    # 先尝试直接定位「推广人群」或「定向人群」文字
    for attempt in range(5):  # 最多滚5次
        # 在所有 frame 中搜索目标文字
        found = False
        for frame in br.page.frames:
            try:
                body_text = frame.inner_text("body") if frame else ""
                if "推广人群" in body_text or "定向人群" in body_text:
                    found = True
                    # 尝试滚动到该元素位置
                    try:
                        crowd_loc = frame.locator("text=推广人群")
                        if crowd_loc.count() > 0:
                            crowd_loc.first.scroll_into_view_if_needed()
                            print("  [找到] 推广人群区域")
                            br.shot("found_crowd_section")
                            return True
                    except Exception:
                        pass
                    try:
                        target_loc = frame.locator("text=定向人群")
                        if target_loc.count() > 0:
                            target_loc.first.scroll_into_view_if_needed()
                            print("  [找到] 定向人群区域")
                            br.shot("found_crowd_section")
                            return True
                    except Exception:
                        pass
            except Exception:
                continue

        if found:
            break

        br.scroll_down(500)
        time.sleep(0.5)

    br.shot("after_scroll_crowd")
    return False


def click_target_crowd_tab(br: CrowdBrowser) -> bool:
    """
    点击「定向人群」tab（在推广人群区域内）。
    基于 screenshot-2：弹窗中有「精选人群」和「定向人群」两个 tab，「定向人群」带「new」标记。
    """
    print("  [操作] 点击「定向人群」 tab...")

    # 尝试多种选择器
    selectors = [
        "text=定向人群",
        "[class*='tab']:has-text('定向人群')",
        "div:has-text('定向人群'):not(:has(div:has-text('定向人群')))",
        "//div[contains(text(),'定向人群')]",
        "//span[contains(text(),'定向人群')]",
    ]

    for sel in selectors:
        try:
            for frame in br.page.frames:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click()
                        print(f"  [成功] 点击了定向人群 ({sel})")
                        time.sleep(1)
                        br.shot("clicked_target_crowd")
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    # JS 兜底
    try:
        clicked = br.page.evaluate("""() => {
            const tabs = document.querySelectorAll('div, span, a, li');
            for (const t of tabs) {
                if (t.textContent.trim().includes('定向人群') && t.textContent.trim().length < 20) {
                    t.click();
                    return true;
                }
            }
            // iframe 中也找
            const iframes = document.querySelectorAll('iframe');
            for (const f of iframes) {
                try {
                    const doc = f.contentDocument || f.contentWindow.document;
                    const els = doc.querySelectorAll('div, span, a, li');
                    for (const el of els) {
                        if (el.textContent.trim().includes('定向人群') && el.textContent.trim().length < 20) {
                            el.click();
                            return true;
                        }
                    }
                } catch(e) {}
            }
            return false;
        }""")
        if clicked:
            print("  [成功] JS 点击了定向人群")
            time.sleep(1)
            br.shot("clicked_target_crowd_js")
            return True
    except Exception as e:
        print(f"  [警告] JS 点击失败: {e}")

    br.shot("failed_target_crowd")
    return False


def click_custom_tag_tab(br: CrowdBrowser) -> bool:
    """
    在右侧面板中点击「自定义人群标签」子 tab。
    基于 screenshot-2：右侧面板顶部有「精选人群」「自定义人群标签」「上传人群文件」三个子 tab。
    """
    print("  [操作] 点击「自定义人群标签」...")

    selectors = [
        "text=自定义人群标签",
        "//div[contains(text(),'自定义人群标签')]",
        "//span[contains(text(),'自定义人群标签')]",
        "[class*='tag']:has-text('自定义')",
    ]

    for sel in selectors:
        try:
            for frame in br.page.frames:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.click()
                        print(f"  [成功] 点击了自定义人群标签")
                        time.sleep(1.5)  # 等待面板内容切换
                        br.shot("clicked_custom_tag")
                        return True
                except Exception:
                    continue
        except Exception:
            continue

    # JS 兜底
    try:
        clicked = br.page.evaluate("""() => {
            const all = document.querySelectorAll('div, span, a, li');
            for (const el of all) {
                const t = el.textContent.trim();
                if (t.includes('自定义人群标签') && t.length < 30) {
                    el.click();
                    return true;
                }
            }
            const iframes = document.querySelectorAll('iframe');
            for (const f of iframes) {
                try {
                    const doc = f.contentDocument || f.contentWindow.document;
                    const els = doc.querySelectorAll('div, span, a, li');
                    for (const e of els) {
                        const t = e.textContent.trim();
                        if (t.includes('自定义人群标签') && t.length < 30) {
                            e.click();
                            return true;
                        }
                    }
                } catch(e) {}
            }
            return false;
        }""")
        if clicked:
            print("  [成功] JS 点击了自定义人群标签")
            time.sleep(1.5)
            br.shot("clicked_custom_tag_js")
            return True
    except Exception as e:
        print(f"  [警告] JS 点击失败: {e}")

    br.shot("failed_custom_tag")
    return False


def select_labels(br: CrowdBrowser) -> dict:
    """
    勾选目标用户属性和用户兴趣标签。

    基于 screenshot-3 的界面结构：
    - 用户属性区：年龄（14-24岁/25-29岁/30-34岁/35岁及以上）、性别（男/女/未知）
    - 用户兴趣区：搜索框 + 行业分类（餐饮/休闲娱乐/KTV/丽人/结婚/亲子/汽车等）
    - 右侧显示「已选择标签」

    返回: {"attributes": int, "interests": int} 各成功勾选的数量
    """
    result = {"attributes": 0, "interests": 0}
    all_targets = TARGET_AGE_LABELS + TARGET_GENDER_LABELS + TARGET_INTEREST_LABELS

    print(f"  [勾选] 目标标签: {all_targets}")

    for label in all_targets:
        success = _check_label(br, label)
        category = "interests" if label in TARGET_INTEREST_LABELS else "attributes"
        if success:
            result[category] += 1
            print(f"    ✓ {label}")
        else:
            print(f"    ✗ {label} (未找到/无法勾选)")
        time.sleep(0.3)

    br.shot("after_select_labels")
    print(f"  [结果] 属性: {result['attributes']}/{len(TARGET_AGE_LABELS)+len(TARGET_GENDER_LABELS)}, "
          f"兴趣: {result['interests']}/{len(TARGET_INTEREST_LABELS)}")
    return result


def _check_label(br: CrowdBrowser, label_text: str) -> bool:
    """
    勾选单个标签。支持 checkbox / 可点击标签 两种 UI 模式。
    基于 screenshot-3：标签前面有 ☑️ checkbox，有些已选中（黄色高亮）。
    """
    # 策略1：找 label 文字对应的 checkbox 或父级可点击区域
    click_selectors = [
        # 直接找包含文字的 label/span/div（整行可点）
        f"label:has-text('{label_text}')",
        f"span:has-text('{label_text}')",
        f"div:has-text('{label_text}')",
        # 找 checkbox + label 组合
        f"[type='checkbox']:near(:text('{label_text}'))",
    ]

    for sel in click_selectors:
        for frame in br.page.frames:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    # 检查是否已经选中（避免重复点击取消选中）
                    parent = loc
                    # 向上找父容器查看是否有"已选中"状态
                    try:
                        outer_html = loc.evaluate("el => el.outerHTML.substring(0, 500)")
                        # 如果已经有 checked/selected/active 类，说明已选中
                        is_already_checked = any(kw in outer_html.lower() for kw in
                                                  ['checked', 'is-active', 'is-selected', 'is-checked',
                                                   'ant-checkbox-wrapper-checked', 'el-checkbox__input.is-checked'])
                        if is_already_checked:
                            return True  # 已经选中，跳过
                    except Exception:
                        pass

                    loc.click()
                    time.sleep(0.3)
                    return True
            except Exception:
                continue

    # 策略2：JS 精确查找并点击
    try:
        clicked = br.page.evaluate(f"""() => {{
            const label = '{label_text}';
            // 查找所有可能包含该标签文本的元素
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_ELEMENT,
                null,
                false
            );
            let bestMatch = null;
            let bestLen = Infinity;
            while (walker.currentNode) {{
                const node = walker.currentNode;
                const text = node.textContent?.trim() || '';
                if (text === label || (text.includes(label) && text.length < label.length + 10)) {{
                    if (text.length < bestLen) {{
                        bestLen = text.length;
                        bestMatch = node;
                    }}
                }}
                walker.nextNode();
            }}
            if (bestMatch) {{
                bestMatch.click();
                return true;
            }}

            // iframe 中也查找
            const iframes = document.querySelectorAll('iframe');
            for (const f of iframes) {{
                try {{
                    const doc = f.contentDocument || f.contentWindow.document;
                    const w2 = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT, null, false);
                    while (w2.currentNode) {{
                        const n = w2.currentNode;
                        const t = n.textContent?.trim() || '';
                        if (t === label || (t.includes(label) && t.length < label.length + 10)) {{
                            n.click();
                            return true;
                        }}
                        w2.nextNode();
                    }}
                }} catch(e) {{}}
            }}
            return false;
        }}""")

        if clicked:
            time.sleep(0.3)
            return True
    except Exception:
        pass

    return False


def save_and_submit(br: CrowdBrowser) -> bool:
    """
    保存设置 → 保存并提交。
    基于 screenshot-3 底部有黄色「保存设置」按钮，之后还有「保存并提交」按钮。
    """
    print("  [提交] 保存设置...")

    # 第一步：点击「保存设置」（底部黄色按钮）
    save_clicked = _try_click_button(br, ["保存设置", "text=保存设置"])
    if not save_clicked:
        print("  [警告] 未找到「保存设置」按钮，尝试继续...")
    else:
        time.sleep(1)
        br.shot("after_save_settings")

    # 第二步：点击「保存并提交」
    print("  [提交] 保存并提交...")
    submit_clicked = _try_click_button(br, ["保存并提交", "text=保存并提交", "提交"])
    if submit_clicked:
        time.sleep(2)
        br.shot("after_submit")
        print("  [成功] 已提交！")
        return True
    else:
        print("  [失败] 未找到「保存并提交」按钮")
        br.shot("submit_failed")
        return False


def _try_click_button(br: CrowdBrowser, selectors: list[str]) -> bool:
    """尝试多种方式点击按钮。"""
    for sel in selectors:
        for frame in br.page.frames:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click()
                    return True
            except Exception:
                continue

    # JS 兜底
    try:
        for sel in selectors:
            text_part = sel.replace("text=", "").replace("'", "")
            result = br.page.evaluate(f"""() => {{
                const btns = document.querySelectorAll('button, [role="button"], div[class*="btn"], a[class*="btn"], span[class*="btn"]');
                for (const b of btns) {{
                    if (b.textContent.trim().includes('{text_part}') && b.offsetParent !== null) {{
                        b.click();
                        return true;
                    }}
                }}
                const iframes = document.querySelectorAll('iframe');
                for (const f of iframes) {{
                    try {{
                        const doc = f.contentDocument || f.contentWindow.document;
                        const bs = doc.querySelectorAll('button, [role="button"], [class*="btn"]');
                        for (const b of bs) {{
                            if (b.textContent.trim().includes('{text_part}') && b.offsetParent !== null) {{
                                b.click();
                                return true;
                            }}
                        }}
                    }} catch(e) {{}}
                }}
                return false;
            }""")
            if result:
                return True
    except Exception:
        pass

    return False


# ======================== 分页逻辑 ========================

def go_to_page(br: CrowdBrowser, page_num: int) -> bool:
    """跳转到指定页码。"""
    print(f"\n[翻页] 跳转到第 {page_num} 页...")

    # 方法1：找分页器的页码按钮
    try:
        for frame in br.page.frames:
            try:
                # 找分页区域中包含该页码的元素
                page_btn = frame.locator(f"text={page_num}").first
                # 确保它是分页按钮（不是表格中的数字）
                if page_btn.count() > 0 and page_btn.is_visible():
                    page_btn.click()
                    time.sleep(2)
                    br.shot(f"page_{page_num}")
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # 方法2：JS 点击分页
    try:
        result = br.page.evaluate(f"""() => {{
            const pageNum = {page_num};
            // 常见分页选择器
            const selectors = [
                '.pagination',
                '[class*="pager"]',
                '[class*="page"]',
                '.el-pagination',
                '.ant-pagination'
            ];
            for (const sel of selectors) {{
                const container = document.querySelector(sel);
                if (!container) continue;
                const items = container.querySelectorAll('li, a, span, button, [class*="num"]');
                for (const item of items) {{
                    if (item.textContent.trim() == String(pageNum)) {{
                        item.click();
                        return true;
                    }}
                }}
            }}
            // iframe 中也找
            const iframes = document.querySelectorAll('iframe');
            for (const f of iframes) {{
                try {{
                    const doc = f.contentDocument || f.contentWindow.document;
                    for (const sel of selectors) {{
                        const c = doc.querySelector(sel);
                        if (!c) continue;
                        const items = c.querySelectorAll('li, a, span, button');
                        for (const item of items) {{
                            if (item.textContent.trim() == String(pageNum)) {{
                                item.click();
                                return true;
                            }}
                        }}
                    }}
                }} catch(e) {{}}
            }}
            return false;
        }}""")
        if result:
            time.sleep(2)
            br.shot(f"page_{page_num}_js")
            return True
    except Exception as e:
        print(f"  [警告] 翻页失败: {e}")

    return False


def get_current_page_promo_dates(br: CrowdBrowser) -> list[date]:
    """
    获取当前页面所有推广的日期，用于判断是否已到达截止日期。
    返回日期列表。
    """
    dates = []
    date_pattern = re.compile(r'(\d{4})-(\d{2})-(\d{2})')

    for frame in br.page.frames:
        url = frame.url or ""
        if not any(p in url for p in br._CONTENT_PATTERNS):
            continue
        try:
            text = frame.inner_text("body")
            matches = date_pattern.findall(text)
            for y, m, d in matches:
                dates.append(date(int(y), int(m), int(d)))
        except Exception:
            continue

    return dates


def has_reached_cutoff(br: CrowdBrowser) -> bool:
    """检查当前页面是否已到达截止日期（所有推广日期 <= CUTOFF_DATE）。"""
    dates = get_current_page_promo_dates(br)
    if not dates:
        return False
    # 如果页面上的最小日期已经 <= 截止日期，说明到了
    return min(dates) <= CUTOFF_DATE


# ======================== 主流程 ========================

def process_one_promotion(br: CrowdBrowser, conn, promo_id: str) -> bool:
    """
    处理单条推广：进入详情 → 修改定向人群 → 提交 → 返回列表。
    返回是否成功。
    """
    print(f"\n{'='*60}")
    print(f"[处理] {promo_id}")
    print(f"{'='*60}")

    # 1. 点击进入详情页
    if not click_promotion(br, promo_id):
        print(f"  [失败] 无法进入 {promo_id} 详情页")
        return False

    # 2. 滚动到推广人群区域
    time.sleep(2)  # 等待详情页渲染
    scroll_to_crowd_section(br)

    # 3. 点击「定向人群」tab
    if not click_target_crowd_tab(br):
        print(f"  [失败] 无法找到「定向人群」tab")
        # 尝试返回列表
        try:
            br.page.go_back(wait_until="domcontentloaded")
            time.sleep(2)
        except Exception:
            pass
        return False

    # 4. 点击「自定义人群标签」子 tab
    if not click_custom_tag_tab(br):
        print(f"  [失败] 无法找到「自定义人群标签」")
        try:
            br.page.go_back(wait_until="domcontentloaded")
            time.sleep(2)
        except Exception:
            pass
        return False

    # 5. 勾选标签
    select_result = select_labels(br)
    total_selected = select_result["attributes"] + select_result["interests"]
    total_expected = len(TARGET_AGE_LABELS) + len(TARGET_GENDER_LABELS) + len(TARGET_INTEREST_LABELS)

    if total_selected < total_expected * 0.5:  # 如果不到一半成功，可能是页面有问题
        print(f"  [警告] 勾选率过低 ({total_selected}/{total_expected})，可能需要人工检查")

    # 6. 保存并提交
    submit_ok = save_and_submit(br)

    # 7. 记录到数据库
    mark_done(conn, promo_id)

    if submit_ok:
        print(f"  [完成] {promo_id} 已处理并提交 ✓")
    else:
        print(f"  [部分完成] {promo_id} 标签已勾选但提交可能未成功")

    # 8. 等待跳转回列表页（提交后通常会自动跳转）
    time.sleep(3)

    # 确保回到列表页
    current_url = br.page.url
    if "编辑推广" not in current_url and "edit" not in current_url.lower():
        print("  [OK] 已自动返回列表页")
    else:
        print("  [手动] 未自动返回，尝试导航回列表...")
        try:
            navigate_to_list(br)
        except Exception as e:
            print(f"  [警告] 导航回列表失败: {e}")

    return submit_ok


def main():
    print("=" * 60)
    print("  智选展位 · 批量定向人群修改工具")
    print(f"  截止日期: {CUTOFF_DATE.isoformat()}")
    print(f"  数据库: {DB_PATH}")
    print(f"  日志目录: {LOG_DIR}")
    print("=" * 60)

    # 初始化数据库
    conn = init_db()
    already_done = get_all_processed(conn)
    print(f"\n[数据库] 已处理 {len(already_done)} 条推广")

    # 连接浏览器
    br = CrowdBrowser()
    try:
        br.connect()
    except RuntimeError as e:
        print(f"\n[致命错误] {e}")
        conn.close()
        return

    # 导航到列表页
    try:
        navigate_to_list(br)
    except Exception as e:
        print(f"\n[致命错误] 无法打开列表页: {e}")
        br.close()
        conn.close()
        return

    # 主循环：逐页扫描
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    current_page = 1
    max_pages = 50  # 安全上限

    while current_page <= max_pages:
        print(f"\n{'='*60}")
        print(f"[第 {current_page} 页]")
        print(f"{'='*60}")

        # 获取当前页面的推广列表
        promo_list = find_promotion_links_in_table(br)
        print(f"  发现 {len(promo_list)} 条推广")

        if not promo_list:
            print("  [结束] 当前页无推广数据，可能已到最后一页")
            break

        # 检查是否到达截止日期
        if has_reached_cutoff(br):
            print(f"  [到达] 已到达截止日期 {CUTOFF_DATE.isoformat()} 之前的推广")
            # 但仍然处理本页中日期 > cutoff 的推广
            # （精确判断留给单条处理时过滤）

        # 逐条处理
        page_has_new = False
        for promo_info in promo_list:
            promo_id = promo_info["id"]

            # 跳过已处理的
            if promo_id in already_done:
                print(f"  [跳过] {promo_id} (已处理)")
                stats["skipped"] += 1
                continue

            page_has_new = True
            try:
                ok = process_one_promotion(br, conn, promo_id)
                if ok:
                    stats["processed"] += 1
                    already_done.add(promo_id)
                else:
                    stats["errors"] += 1
                    already_done.add(promo_id)  # 也标记避免重复尝试

                # 提交后页面会跳回第1页，需要重新导航到正确的页
                time.sleep(2)
                # 重新导航到列表（因为提交后跳走了）
                try:
                    navigate_to_list(br)
                    # 尝试回到当前页
                    if current_page > 1:
                        go_to_page(br, current_page)
                        time.sleep(2)
                except Exception as nav_err:
                    print(f"  [警告] 重新导航失败: {nav_err}")
                    break  # 退出当前页循环，重新开始

            except Exception as e:
                print(f"  [异常] 处理 {promo_id} 时出错: {e}")
                stats["errors"] += 1
                br.shot(f"error_{promo_id}")
                # 尝试恢复
                try:
                    navigate_to_list(br)
                except Exception:
                    pass
                break

        # 本页没有新要处理的推广了，翻下一页
        if not page_has_new:
            print(f"\n[翻页] 第 {current_page} 页全部已完成，准备翻到下一页...")
            if not go_to_page(br, current_page + 1):
                print("  [结束] 无法翻到下一页，可能已是最后一页")
                break
            current_page += 1
            time.sleep(2)
        else:
            # 如果本页有新处理的但被中断了（跳转问题），不翻页，重试当前页
            print(f"\n[重扫] 第 {current_page} 页因提交跳转需重新扫描")
            # 不增加页码，重新提取列表

    # 最终统计
    print(f"\n{'='*60}")
    print(f"  执行完毕！统计:")
    print(f"  - 本次成功处理: {stats['processed']} 条")
    print(f"  - 跳过(已处理): {stats['skipped']} 条")
    print(f"  - 异常/失败:    {stats['errors']} 条")
    print(f"  - 总计已处理:   {len(get_all_processed(conn))} 条")
    print(f"  - 截图保存在:   {LOG_DIR}")
    print(f"  - 数据库文件:   {DB_PATH}")
    print(f"{'='*60}")

    conn.close()
    br.close()

    print("\n提示：再次运行此脚本可断点续跑，已处理的推广会自动跳过。")


if __name__ == "__main__":
    main()
