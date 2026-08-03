"""Playwright 浏览器封装：持久化登录态、悬停/点击/等待原语、截图留痕。"""
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, BrowserContext

from retry import retry_on_failure
from logging_setup import setup_logging

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"


def load_json(name: str) -> dict:
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def _get_cdp_ws_url(port: int) -> str:
    """从 CDP 端口获取 webSocketDebuggerUrl。"""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("webSocketDebuggerUrl", "")
    except Exception:
        return ""


class Browser:
    """持久化上下文浏览器。登录态保存在 profile_dir，重复运行无需再登录。"""

    def __init__(self, settings: dict, keep_alive: bool = False):
        self.settings = settings
        self.keep_alive = keep_alive
        self._pw = None
        self._browser = None  # CDP 模式下的 Browser 对象
        self.ctx: BrowserContext | None = None
        self.page: Page | None = None
        self._active_frame = None
        # 选择器 → 上次命中的 frame URL 特征。让重复调用优先探测正确的 frame。
        self._frame_affinity: dict[str, str] = {}
        self.run_dir = LOGS / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log = setup_logging(self.run_dir)
        self._step = 0

    def __enter__(self):
        cfg = self.settings["browser"]
        connect_mode = cfg.get("connect_mode", "launch")
        self._connect_mode = connect_mode
        self._pw = sync_playwright().start()

        if connect_mode == "cdp":
            # CDP 模式：连接已打开的 Edge/Chrome 浏览器
            cdp_port = cfg.get("cdp_port", 9222)
            ws_url = _get_cdp_ws_url(cdp_port)
            if not ws_url:
                raise RuntimeError(
                    f"无法连接到 CDP 端口 {cdp_port}。\n"
                    f"请先以远程调试模式启动 Edge：\n"
                    f'  msedge --remote-debugging-port={cdp_port}\n'
                    f"或 Chrome：\n"
                    f'  chrome --remote-debugging-port={cdp_port}'
                )
            print(f"[CDP] 正在连接已打开的浏览器 (端口 {cdp_port})...")
            self._browser = self._pw.chromium.connect_over_cdp(ws_url)
            # CDP 模式下自动保活，不关闭用户的浏览器
            self.keep_alive = True
            self.ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            print(f"[CDP] 连接成功！当前页面数: {len(self.ctx.pages)}")
        else:
            # Launch 模式：启动新浏览器（profile 保存在项目目录，登录态自动持久化）
            channel = cfg.get("channel")  # e.g. "msedge" → 用 Edge 而非 Chromium

            profile = ROOT / cfg["profile_dir"]
            profile.mkdir(parents=True, exist_ok=True)
            user_data_dir = str(profile)

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ]

            self.ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel=channel,
                headless=cfg.get("headless", False),
                slow_mo=cfg.get("slow_mo_ms", 0),
                viewport={"width": 1440, "height": 900},
                args=launch_args,
            )
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
            print(f"[Launch] 浏览器已启动 (channel={channel or 'chromium'}, profile={user_data_dir})")
            print(f"[Launch] 登录态保存在 {cfg['profile_dir']}，重复运行无需再登录")

        self.ctx.set_default_timeout(cfg.get("default_timeout_ms", 15000))
        self._active_frame = self.page.main_frame
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.keep_alive:
            print(f"\n[保活模式] 浏览器将保持打开，登录态已保存到: {self.settings['browser']['profile_dir']}")
            print("你可以继续在浏览器中操作。操作完成后可手动关闭浏览器窗口。")
            if exc_val:
                print(f"(此前异常: {exc_val})")
            return True  # 抑制异常

        close_exc = None
        try:
            if self.ctx:
                self.ctx.close()
        except Exception as e:
            close_exc = e
        finally:
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass
        if close_exc and exc_val:
            raise close_exc from exc_val
        elif close_exc:
            raise close_exc
        return False

    # ---------- 操作原语（每步自动截图留痕） ----------

    def shot(self, label: str):
        self._step += 1
        path = self.run_dir / f"{self._step:02d}_{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=False)
        except Exception:
            pass
        return path

    # 美团经营宝页面由多个 iframe 组成：
    # 1) 顶部/左侧菜单 iframe (merchant-menu / peon-merchant-product-menu)
    # 2) 主内容 iframe (shopdiy-node / merchant-workbench / cpm-edit / budget-group-list)
    _MENU_PATTERNS = ("merchant-menu", "peon-merchant-product-menu")
    _CONTENT_PATTERNS = ("shopdiy-node", "merchant-workbench", "cpm-edit", "budget-group-list")

    def _ordered_frames(self, selector: str = None):
        """按命中概率排序 frame：亲和缓存 > 活跃 frame > 菜单/内容 iframe > 其余。"""
        frames = list(self.page.frames)
        cached_hint = self._frame_affinity.get(selector) if selector else None

        def priority(f):
            url = f.url or ""
            # 0) 该选择器上次命中的 frame 特征，优先级最高
            if cached_hint and cached_hint in url:
                return 0
            # 1) 当前活跃 frame
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
        """从 frame URL 提取稳定特征，用作亲和缓存的 key。"""
        url = frame.url or ""
        for p in Browser._CONTENT_PATTERNS + Browser._MENU_PATTERNS:
            if p in url:
                return p
        return None

    def _locate(self, selector: str, timeout_ms: int = 5000, only_in_frame: str = None):
        """在所有 iframe 中定位元素，返回 (locator, frame)。

        关键设计：**单一全局 deadline**，所有 frame 在每一轮里各探测一次（非阻塞判定），
        命中即返回。总耗时 = 元素真实就绪时间，而不是 timeout × frame 数。

        （旧实现先对活跃 frame 等满 timeout，miss 后再逐个 frame 各等满 timeout，
        导致命中第 3 个 frame 时耗时 = timeout×2 + 命中时间，实测单步空等达 32s。）

        only_in_frame: 非空时严格限定只在 URL 包含此字串的 frame 中搜索，
        避免内容页同名元素干扰 nav 类选择器。"""
        deadline = time.time() + timeout_ms / 1000
        attempts = 0

        while True:
            if only_in_frame:
                frames = [f for f in self.page.frames if only_in_frame in (f.url or "")]
            else:
                frames = self._ordered_frames(selector)

            for frame in frames:
                try:
                    loc = frame.locator(selector).first
                    # count()/is_visible() 是即时判定，不阻塞等待
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
            time.sleep(0.12)

        scope = f"限定 frame [{only_in_frame}]" if only_in_frame else f"全部 {len(self.page.frames)} 个 frame"
        raise TimeoutError(
            f"{timeout_ms}ms / {attempts} 轮探测内，在{scope}中未找到可见元素: {selector}"
        )

    def _switch_frame(self, frame):
        if self._active_frame != frame:
            self._active_frame = frame

    def goto(self, url: str):
        self.page.goto(url, wait_until="domcontentloaded")
        self.shot("goto")

    @retry_on_failure(max_attempts=3, base_delay=1.0, backoff=2.0)
    def click(self, selector: str, label: str = "click", only_in_frame: str = None):
        self._guard(selector)
        loc, frame = self._locate(selector, only_in_frame=only_in_frame)
        self._switch_frame(frame)
        loc.click()
        try:
            self._active_frame.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        self.shot(label)

    @retry_on_failure(max_attempts=3, base_delay=1.0, backoff=2.0)
    def hover(self, selector: str, label: str = "hover", settle_ms: int = 800,
              wait_for_selector: str = None, wait_timeout_ms: int = 5000,
              only_in_frame: str = None):
        """悬停并等待弹层稳定。支持两种等待模式:
        - wait_for_selector: 轮询等待目标元素出现（更可靠）
        - settle_ms: 固定睡眠（兜底，当目标元素不确定时使用）
        """
        self._guard(selector)
        loc, frame = self._locate(selector, only_in_frame=only_in_frame)
        self._switch_frame(frame)
        loc.scroll_into_view_if_needed()
        loc.hover()
        if wait_for_selector:
            try:
                ws_loc, ws_frame = self._locate(wait_for_selector, timeout_ms=wait_timeout_ms)
                ws_loc.wait_for(state="visible", timeout=wait_timeout_ms)
            except Exception:
                pass
        else:
            time.sleep(settle_ms / 1000)
        self.shot(label)

    @retry_on_failure(max_attempts=3, base_delay=1.0, backoff=2.0)
    def hover_menu_click(self, parent_selector: str, child_selector: str,
                         label: str = "hover_click", settle_ms: int = 800,
                         steps: int = 10):
        """级联菜单专用：悬停父项 → 鼠标竖直滑入子项 → 点击。
        鼠标全程不离开菜单区域，避免 hover 弹层塌陷。"""
        self._guard(parent_selector)
        self._guard(child_selector)

        # 1) 定位父菜单项，并把鼠标真实移过去触发 hover
        p_loc, p_frame = self._locate(parent_selector)
        self._switch_frame(p_frame)
        p_box = p_loc.bounding_box()
        if not p_box:
            raise TimeoutError(f"无法获取父菜单项坐标: {parent_selector}")
        p_cx = p_box["x"] + p_box["width"] / 2
        p_cy = p_box["y"] + p_box["height"] / 2

        self.page.mouse.move(p_cx, p_cy)
        # 轻微向下抖动，确保 CSS/JS hover 真正生效
        self.page.mouse.move(p_cx, p_cy + 1)
        time.sleep(settle_ms / 1000)

        # 2) 等待子菜单项出现
        c_loc, c_frame = self._locate(child_selector, timeout_ms=8000)
        self._switch_frame(c_frame)

        # 3) 鼠标竖直下滑到子项中心并点击
        c_box = c_loc.bounding_box()
        if not c_box:
            raise TimeoutError(f"子菜单项悬停后仍未显示: {child_selector}")
        c_cx = c_box["x"] + c_box["width"] / 2
        c_cy = c_box["y"] + c_box["height"] / 2

        self.page.mouse.move(c_cx, c_cy, steps=steps)
        self.page.mouse.down()
        self.page.mouse.up()

        try:
            self._active_frame.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        self.shot(label)

    @retry_on_failure(max_attempts=3, base_delay=1.0, backoff=2.0)
    def fill(self, selector: str, value: str, label: str = "fill"):
        self._guard(selector)
        loc, frame = self._locate(selector)
        self._switch_frame(frame)
        loc.clear()
        loc.fill(str(value))
        self.shot(label)

    def wait_for(self, selector: str, state: str = "visible", label: str = "wait",
                 timeout_ms: int = None, only_in_frame: str = None):
        """等待元素到达指定状态。与其他原语一样受 guard 保护。"""
        self._guard(selector)
        if timeout_ms is None:
            timeout_ms = self.settings["browser"].get("default_timeout_ms", 15000)
        loc, frame = self._locate(selector, timeout_ms=timeout_ms, only_in_frame=only_in_frame)
        self._switch_frame(frame)
        loc.wait_for(state=state, timeout=timeout_ms)
        self.shot(label)

    def _guard(self, selector: str):
        if selector.startswith("TODO_EXPLORE"):
            # 崩溃前先截图, 让用户看到卡在哪一步
            try:
                self.shot("TODO_EXPLORE_blocked")
            except Exception:
                pass
            raise RuntimeError(
                f"\n[卡点] 选择器未回填: {selector}\n"
                f"[步骤] 见 logs/<最新时间戳>/ 中截图 TODO_EXPLORE_blocked.png\n"
                f"[请用户] 把当前页面截图发给我, 我会从图中找出正确的选择器回填。\n"
            )

    def is_visible(self, selector: str, timeout_ms: int = 5000) -> bool:
        if selector.startswith("TODO_EXPLORE"):
            return False
        try:
            loc, frame = self._locate(selector, timeout_ms=timeout_ms)
            self._switch_frame(frame)
            return True
        except Exception:
            return False

    def keep_browser_alive(self):
        """阻塞直到用户手动关闭浏览器窗口。不依赖 input()，在任何终端环境下都有效。"""
        print(f"\n[保活模式] 浏览器已启动，进程将保持运行。")
        print(f"  登录态已保存到: {self.settings['browser']['profile_dir']}")
        print(f"  你可以在浏览器中自由操作。")
        print(f"  关闭浏览器窗口后，进程将自动退出。")
        print()
        try:
            while True:
                # 检查页面是否还存在
                if not self.ctx or self.ctx.pages == []:
                    print("[保活模式] 浏览器已关闭，进程退出。")
                    break
                # 检查当前页是否已关闭
                try:
                    if self.page and self.page.is_closed():
                        # 还有其他页面的话切换过去
                        alive_pages = [p for p in self.ctx.pages if not p.is_closed()]
                        if alive_pages:
                            self.page = alive_pages[0]
                        else:
                            print("[保活模式] 所有页面已关闭，进程退出。")
                            break
                except Exception:
                    pass
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n[保活模式] 收到中断信号，进程退出。")

    def pause_for_human(self, reason: str):
        """暂停等待人工操作。三种方式任选其一即可继续:
        1. 在真实终端按回车(input())
        2. 在浏览器控制台执行: localStorage.setItem('wb_continue', '1')
           或在地址栏访问: javascript:localStorage.setItem('wb_continue','1')
        3. 创建信号文件: data/continue.flag (空文件即可)
        """
        flag = ROOT / "data" / "continue.flag"
        if flag.exists():
            try:
                flag.unlink()
            except Exception:
                pass
        print(f"\n[需要人工介入] {reason}")
        print("请在打开的浏览器窗口中完成操作。")
        print("完成后用以下任一方式让脚本继续:")
        print("  (A) 在终端按回车")
        print("  (B) 创建空文件:", flag)
        print("  (C) 浏览器控制台执行: localStorage.setItem('wb_continue', '1')")

        # 先尝试 input() (真实终端)
        try:
            print("\n[等待终端输入 / 信号文件 / 浏览器信号] ...", flush=True)
            input()
            self.shot("after_human")
            return
        except EOFError:
            pass

        # 非交互终端, 进入轮询模式
        print("[非交互终端] 切换为轮询模式, 等待信号文件或浏览器信号。")
        try:
            while True:
                # 方式 B: 信号文件
                if flag.exists():
                    try:
                        flag.unlink()
                    except Exception:
                        pass
                    print("[信号] 检测到 continue.flag, 继续执行。")
                    self.shot("after_human")
                    return
                # 方式 C: 浏览器 localStorage 信号
                try:
                    val = self.page.evaluate("() => localStorage.getItem('wb_continue')")
                    if val == "1":
                        self.page.evaluate("() => localStorage.removeItem('wb_continue')")
                        print("[信号] 检测到浏览器 localStorage 信号, 继续执行。")
                        self.shot("after_human")
                        return
                except Exception:
                    pass
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[中断] 用户中断, 退出。")
