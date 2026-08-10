"""主流程编排：共享预算 -> 新建推广 -> 内容种草 -> 选笔记 -> 地域/出价 -> 提交。"""
import json
import sys as _sys
import time
from datetime import date
from pathlib import Path as _Path

from browser import Browser, load_json, ROOT
from notes import Note, select_best_note, mark_promoted, mark_unpromoted, parse_views, list_promoted
from notes import is_store_done, mark_store_done
from budget_db import scrape_and_upsert, list_promoted_titles_for_keyword

# 复用 batch_target_audience 的定向人群修改 helper（已验证稳定跑通）
_SCRIPTS_DIR = _Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))
from batch_target_audience import (  # noqa: E402
    TARGET_TAGS,
    _crowd_item,
    _click_targeted_radio,
    _open_crowd_drawer,
    _click_custom_tag_tab,
    _scroll_drawer_top,
    _ensure_industry_expanded,
    _check_tag,
)


class FlowError(RuntimeError):
    pass


def _has_direct_url(settings: dict) -> bool:
    """是否配置了可用的共享预算直达 URL。"""
    return bool((settings.get("direct_urls") or {}).get("budget_list"))


def load_stores(settings: dict) -> list[dict]:
    """门店列表：优先 config/stores.json；缺失时回退 settings.json 的 store_whitelist。
    每个门店为 dict: {name, search_keyword, budget_keyword}。"""
    stores_file = ROOT / "config" / "stores.json"
    if stores_file.exists():
        data = json.loads(stores_file.read_text(encoding="utf-8"))
        return [s for s in data.get("stores", []) if s.get("enabled", True)]
    # 回退旧配置
    names = settings.get("store_whitelist", [])
    kw_map = settings.get("budget_keyword_by_store", {})
    out = []
    for n in names:
        if not n or n == "示例门店名称-请修改":
            continue
        prefix = n.split("(")[0].split("（")[0]
        out.append({
            "name": n,
            "search_keyword": prefix[:6] or None,
            "budget_keyword": kw_map.get(n, n),
        })
    return out


def run(dry_run: bool = True, single_store: str = None):
    settings = load_json("settings.json")
    sel = load_json("selectors.json")
    stores = load_stores(settings)
    if single_store:
        stores = [s for s in stores if single_store in s["name"]]
        if not stores:
            stores = [{"name": single_store, "search_keyword": None,
                       "budget_keyword": settings.get("budget_keyword_by_store", {}).get(single_store, single_store)}]
    promo = settings["promotion"]
    safety = settings["safety"]

    # ---- 配置校验 ----
    if not stores:
        raise FlowError("门店列表为空, 请检查 config/stores.json 或 settings.json 的 store_whitelist")
    if promo["distance_km"] <= 0 or promo["distance_km"] > 20:
        raise FlowError(f"distance_km={promo['distance_km']} 不在合理范围(0~20km)")
    if promo["bid_per_click"] <= 0:
        raise FlowError(f"bid_per_click={promo['bid_per_click']} 必须大于0")
    if promo["notes_per_run"] < 1:
        raise FlowError(f"notes_per_run={promo['notes_per_run']} 至少为1")
    if safety["max_promotions_per_day"] < 1:
        raise FlowError(f"max_promotions_per_day={safety['max_promotions_per_day']} 至少为1")
    if safety["max_bid_allowed"] <= 0:
        raise FlowError(f"max_bid_allowed={safety['max_bid_allowed']} 必须大于0")

    # 每日投放上限兜底
    today_count = sum(
        1 for _, _, at, src in list_promoted() if src == "auto" and at.startswith(date.today().isoformat())
    )
    if today_count >= safety["max_promotions_per_day"]:
        raise FlowError(f"今日已投放 {today_count} 条, 达到上限 {safety['max_promotions_per_day']}, 停止。")
    if promo["bid_per_click"] > safety["max_bid_allowed"]:
        raise FlowError(f"出价 {promo['bid_per_click']} 超过安全上限 {safety['max_bid_allowed']}, 停止。")

    # ---- 完整流程：登录 → 逐门店(循环推广) → 提交/存草稿 ----
    submit_mode = not dry_run  # --dry-run 存草稿; --run 真实提交
    print(f"[模式] {'真实提交(--run)' if submit_mode else '保存草稿(--dry-run)'}")
    results = []
    with Browser(settings) as b:
        _ensure_login(b, sel)
        for idx, store in enumerate(stores):
            name = store["name"]
            print(f"\n===== 处理门店: {name} =====")
            if not store.get("budget_keyword"):
                print(f"[SKIP] {name}: 未配置 budget_keyword(共享预算表中无此门店), 跳过")
                results.append({"store": name, "skipped": True, "reason": "no_budget_keyword"})
                continue
            # 已确定性推完的门店: 直接跳过, 不驱动浏览器(0 秒), 避免每家重走完整流程
            if is_store_done(name):
                print(f"[SKIP] {name}: 已标记推完(store_done), 跳过该门店(0秒)")
                results.append({"store": name, "skipped": True, "reason": "store_done_cached"})
                continue
            # 第 2 个门店起, 重置页面避免上个门店留下的抽屉/弹窗干扰导航。
            # 走直达 URL 时 goto 本身即完整重置, 无需先绕门户页。
            if idx > 0 and not _has_direct_url(settings):
                try:
                    b.goto(settings["portal_url"])
                    b.page.wait_for_timeout(3000)
                    print("[RESET] 已重置到门户页")
                except Exception as e:
                    print(f"[WARN] 重置页面失败: {e}")

            # 单门店循环：直到该门店无可推广笔记 / 推广中达上限 / 连续失败
            round_no = 0
            store_results = []
            while True:
                round_no += 1
                print(f"\n--- {name} 第 {round_no} 轮 ---")

                # 第 2 轮起(提交后页面已跳转)需要重置。走直达 URL 时 goto 即重置, 跳过绕门户页。
                if round_no > 1 and not _has_direct_url(settings):
                    try:
                        b.goto(settings["portal_url"])
                        b.page.wait_for_timeout(3000)
                        print("[RESET] 已重置到门户页")
                    except Exception as e:
                        print(f"[WARN] 重置页面失败: {e}")

                # 每日投放上限兜底（提交模式下每轮检查）
                if submit_mode:
                    today_count = sum(
                        1 for _, _, at, src in list_promoted()
                        if src == "auto" and at.startswith(date.today().isoformat())
                    )
                    if today_count >= safety["max_promotions_per_day"]:
                        print(f"[上限] 今日已投放 {today_count} 条, 达每日上限 {safety['max_promotions_per_day']}, 停止全部")
                        break

                try:
                    r = _promote_one_store(b, sel, settings, store, dry_run,
                                           submit_mode=submit_mode, skip_scrape=(round_no > 1))
                    store_results.append(r)
                except Exception as e:
                    import traceback as _tb
                    print(f"[ERROR] {name} 第{round_no}轮失败: {e}", flush=True)
                    _tb.print_exc()
                    try:
                        b.shot("store_fail")
                    except Exception:
                        pass
                    store_results.append({"store": name, "round": round_no, "error": str(e)})
                    break

                # 该轮结果决定是否继续
                if r.get("skipped") or r.get("error"):
                    if r.get("error"):
                        print(f"[结束] {name}: 出错 → 停止该门店")
                        break
                    reason = str(r.get("reason", ""))
                    # 瞬态失败(美团接口/页面抖动): 同一次 run 内自动重试 1 次, 不放弃该门店
                    transient = reason.startswith(("note_card_not_rendered", "drawer_not_open",
                                                   "no_notes_in_drawer", "note_modify_click_fail"))
                    if transient and round_no < 2:
                        print(f"[重试] {name}: 瞬态失败({reason}), 同轮自动重试(第{round_no + 1}次)...")
                        continue
                    print(f"[结束] {name}: {'无可用笔记' if r.get('skipped') else '出错'} → 停止该门店")
                    break

                # 提交模式下, 检查该预算组「推广中」上限 100
                if submit_mode and store.get("budget_keyword"):
                    try:
                        from budget_db import find_budget_id_by_keyword, count_active_promotions
                        bid = find_budget_id_by_keyword(store["budget_keyword"])
                        if bid is not None:
                            active = count_active_promotions(bid)
                            if active >= 100:
                                print(f"[上限] {name} 预算组推广中已达 {active} 条(>=100), 停止该门店")
                                break
                    except Exception as e:
                        print(f"[WARN] 上限检查失败: {e}")
                        break
                else:
                    # dry-run 模式: 只跑 1 轮验证
                    break
            results.extend(store_results)
    return results


def _ensure_login(b: Browser, sel: dict):
    b.goto(b.settings["portal_url"])
    b.page.wait_for_timeout(3000)  # 等页面和菜单 iframe 加载
    login = sel["login"]
    li = login["login_page_indicator"]
    lo = login["logged_in_indicator"]
    if li.startswith("TODO_EXPLORE") or lo.startswith("TODO_EXPLORE"):
        print("[WARN] 登录指示器未回填, 截图后继续（假设已登录）")
        b.shot("login_check")
        return
    if b.is_visible(login["login_page_indicator"], 5000) or not b.is_visible(
        login["logged_in_indicator"], 8000
    ):
        b.pause_for_human("检测到未登录, 请在浏览器中完成账号密码+验证码登录。")
        if not b.is_visible(login["logged_in_indicator"], 10000):
            raise FlowError("登录校验失败, 终止。")
    print("[OK] 登录态正常")


def _ensure_budget_page1(b: Browser, keyword: str):
    """确保共享预算表格中找到目标关键词行（从当前页开始逐页查找）。
    之前只检查第 1 页, 但目标行可能在后续页, 导致多门店遍历时找不到。"""
    for f in b.page.frames:
        if "budget-group-list" not in (f.url or ""):
            continue
        # 从当前页开始, 最多翻 6 页查找
        for page in range(1, 7):
            try:
                rows = f.locator("tr.merchant-table__row")
                n = rows.count()
                for i in range(min(n, 60)):
                    txt = rows.nth(i).inner_text()
                    # 2026-08-05 修复: 关键词子串可能命中"已删除"的同名预算组(如"崇文门店..."含"崇文门"),
                    # 必须跳过已删除行继续翻页, 否则永远 hover 不到生效行(可能在下一页)。
                    if keyword in txt and "已删除" not in txt:
                        print(f"[预算页] 目标行 '{keyword}' 已在第 {page} 页第 {i+1} 行")
                        return
                if n == 0:
                    print(f"[预算页] 第 {page} 页暂无数据行, 等待加载...")
                    time.sleep(1)
                    continue
            except Exception as e:
                print(f"[预算页] 检查行异常: {e}")

            # 尝试翻到下一页
            next_btn = f.locator("li.merchant-pagination__next:not(.merchant-pagination--disabled)").first
            clicked_next = False
            try:
                if next_btn.count() > 0 and next_btn.is_visible():
                    next_btn.click()
                    time.sleep(1.5)
                    clicked_next = True
                    print(f"[预算页] 已翻到第 {page+1} 页")
            except Exception:
                pass
            if not clicked_next:
                print(f"[WARN] 第 {page} 页未找到 '{keyword}' 且无下一页, 后续 hover 可能仍失败")
                b.shot("budget_keyword_not_found")
                return

        print(f"[WARN] 翻遍 6 页仍未找到 '{keyword}', 后续 hover 可能仍失败")
        b.shot("budget_keyword_not_found_all_pages")
        return


def _budget_table_ready(b: Browser, timeout_s: float = 12.0):
    """等待共享预算 frame 出现且表格渲染出数据行。返回 frame 或 None。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in b.page.frames:
            if "budget-group-list" not in (f.url or ""):
                continue
            try:
                if f.locator("tr.merchant-table__row").count() > 0:
                    return f
            except Exception:
                pass
        time.sleep(0.3)
    return None


def _nav_direct(b: Browser) -> bool:
    """直达 URL 打开共享预算页，跳过四级菜单点击。
    2026-07-31 实测 2.4s 到位（菜单点击路径约 62s）。
    失败返回 False，由调用方回退到菜单点击。"""
    url = (b.settings.get("direct_urls") or {}).get("budget_list")
    if not url:
        return False
    try:
        t0 = time.time()
        b.page.goto(url, wait_until="domcontentloaded")
        if "budget-group-list" not in (b.page.url or ""):
            print(f"[导航] 直达 URL 被重定向到 {b.page.url}, 回退菜单点击")
            return False
        frame = _budget_table_ready(b)
        if frame:
            print(f"[导航] ✓ 直达共享预算页 ({time.time() - t0:.1f}s)")
            b.shot("direct_nav_budget")
            return True
        print("[导航] 直达 URL 已加载但表格未渲染, 回退菜单点击")
    except Exception as e:
        print(f"[导航] 直达 URL 异常: {e}, 回退菜单点击")
    return False


def _nav_to_shared_budget(b: Browser, sel: dict, max_try: int = 3) -> bool:
    """导航到共享预算页并验证 budget-group-list frame 出现。

    直达 URL 已配置时: 直接重试直达(单程 ~2.4s), 不再走四级菜单点击(单程 ~62s)。
      重试耗尽即返回 False —— 文档约定 _has_direct_url() 为真时, 置 direct_urls=null
      可强制回退到菜单点击路径, 作为 URL 失效时的逃生舱。
    未配置直达 URL 时: 回退旧的四级菜单点击路径(整段重试), 重置走 portal + 等待。"""
    if _has_direct_url(b.settings):
        for attempt in range(1, max_try + 1):
            if _nav_direct(b):
                return True
            if attempt < max_try:
                time.sleep(1)
        print(f"[导航] 直达 URL 重试 {max_try} 次仍失败, 放弃(可置 direct_urls=null 回退菜单)")
        return False

    nav = sel["nav"]
    for attempt in range(1, max_try + 1):
        try:
            b.click(nav["promotion_center"], "推广中心")
            time.sleep(1.5)
            b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
            time.sleep(1.5)
            b.click(nav["smart_display"], "智选展位")
            time.sleep(2)
            try:
                b.wait_for(nav["toolbox_hover"], state="visible", label="等待工具箱tab", timeout_ms=15000)
            except Exception:
                print("[WARN] 工具箱 tab 未在 15s 内出现，仍尝试 hover_menu_click")
            b.hover_menu_click(nav["toolbox_hover"], nav["shared_budget"], "工具箱-共享预算")
            time.sleep(1)
        except Exception as e:
            print(f"[导航] 第{attempt}次导航异常: {e}", flush=True)
            if attempt < max_try:
                try:
                    b.goto(b.settings["portal_url"])
                    b.page.wait_for_timeout(3000)
                except Exception as ge:
                    print(f"[导航] 重置页面失败: {ge}", flush=True)
                continue
            return False

        # 验证是否到达共享预算页（frame 出现 + 表格行渲染）
        frame_ok = None
        for f in b.page.frames:
            if "budget-group-list" in (f.url or ""):
                frame_ok = f
                break
        if frame_ok:
            # 等表格有数据行（避免 frame 出现但内容未加载导致后续找不到预算行）
            deadline = time.time() + 8
            while time.time() < deadline:
                try:
                    if frame_ok.locator("tr.merchant-table__row").count() > 0:
                        print(f"[导航] 第{attempt}次成功到达共享预算页(表格已加载)")
                        return True
                except Exception:
                    pass
                time.sleep(0.8)
            print(f"[导航] 第{attempt}次到达预算页但表格为空, 重试...")
        else:
            print(f"[导航] 第{attempt}次未检测到共享预算页, 重试...")
        if attempt < max_try:
            b.goto(b.settings["portal_url"])
            b.page.wait_for_timeout(3000)
    return False


def _open_new_promotion(b: Browser, sel: dict, sb: dict, keyword: str, name: str, max_try: int = 3) -> bool:
    """定位预算行 -> hover 名称 -> 新增推广 -> 去新建推广。
    表格异步渲染, hover 弹窗可能未就绪, 整段重试直至成功。"""
    for attempt in range(1, max_try + 1):
        try:
            _ensure_budget_page1(b, keyword)
            time.sleep(1.2)  # 等表格行渲染稳定
            row_cell = sb["budget_row_name_cell"].replace("{budget_keyword}", keyword)
            b.hover(row_cell, "悬停预算名称")
            time.sleep(0.8)
            b.hover(sb["hover_popup_new_promotion"], "悬停新增推广")
            time.sleep(0.8)
            b.click(sb["goto_create_promotion"], "去新建推广")
            # 等 cpm-edit frame 出现（最多 8 秒, 分次检测）
            deadline = time.time() + 8
            while time.time() < deadline:
                for f in b.page.frames:
                    if "cpm-edit" in (f.url or ""):
                        print(f"[新建] 第{attempt}次成功打开新建推广")
                        time.sleep(1.5)  # 等表单内部渲染
                        return True
                time.sleep(1)
            print(f"[新建] 第{attempt}次未检测到新建推广页, 重试...")
        except Exception as e:
            print(f"[新建] 第{attempt}次打开新建推广异常: {e}", flush=True)
        if attempt < max_try:
            # 重新导航到共享预算页（内部含 goto 重置）
            try:
                _nav_to_shared_budget(b, sel, max_try=1)
            except Exception:
                pass
            time.sleep(1)
    return False


def _promote_one_store(b: Browser, sel: dict, settings: dict, store: dict, dry_run: bool,
                       submit_mode: bool = False, skip_scrape: bool = False) -> dict:
    nav, sb, cp = sel["nav"], sel["shared_budget_page"], sel["create_promotion"]
    promo = settings["promotion"]
    name = store["name"]
    keyword = store["budget_keyword"]

    # 导航到共享预算页（含验证+重试）
    if not _nav_to_shared_budget(b, sel):
        raise FlowError(f"导航到共享预算页失败(重试{3}次仍不到), 门店 {name}")

    # 爬取共享预算列表到数据库（增量; 多轮循环时仅首轮执行, 避免展开状态干扰）
    # DB 已是单一数据源: 历史运行已全量入库 + 每次成功推广都写库, 默认跳过全量爬取以加速。
    skip_full = skip_scrape or bool((settings.get("skip_full_scrape") or {}).get("enabled", False))
    if not skip_full:
        for f in b.page.frames:
            if "budget-group-list" in (f.url or ""):
                scrape_result = scrape_and_upsert(f)
                print(f"[爬取] 共享预算={scrape_result['new_budgets']}新/{scrape_result['total_budgets']}总, "
                      f"推广={scrape_result['new_promotions']}新/{scrape_result['updated_promotions']}更新/{scrape_result['total_promotions']}总")
                break
    else:
        print("[爬取] 跳过全量爬取(DB已有历史数据, 用既有查重库执行)")

    # 定位门店预算行 -> 悬停名称 -> 新增推广 -> 去新建推广（整段重试）
    print(f"[门店] {name} -> 关键词: {keyword}")
    if not _open_new_promotion(b, sel, sb, keyword, name):
        raise FlowError(f"打开新建推广失败(重试{3}次), 门店 {name}")

    # 推广目的=内容种草, 门店选择
    b.click(cp["purpose_content_seeding"], "内容种草")
    _select_store(b, cp, store)

    # 解析该预算组 budget_id（用于历史内容去重 + 上限判断）
    try:
        from budget_db import find_budget_id_by_keyword
        budget_id = find_budget_id_by_keyword(keyword)
        if budget_id:
            print(f"[预算组] 解析到 budget_id={budget_id} ({keyword})")
        else:
            budget_id = None
            print(f"[WARN] 未解析到 budget_id ({keyword})")
    except Exception as e:
        budget_id = None
        print(f"[WARN] 解析 budget_id 失败: {e}")

    return _do_post_store_steps(b, sel, settings, store, dry_run, keyword,
                                submit_mode=submit_mode, budget_id=budget_id)


def _dismiss_modals(b: Browser, max_wait: float = 6.0) -> bool:
    """通用弹窗清理：关闭页面上所有可见弹窗(modal/dialog/toast)。
    美团弹窗类型不固定(merchant-modal / vertical-top-dialog / ant-modal 等),
    统一匹配后逐个点 确定/确认/知道了/关闭 按钮, 兜底按 ESC。
    返回是否处理过至少一个弹窗。"""
    modal_selectors = [
        ".merchant-modal",
        ".merchant-confirm-modal",
        ".ant-modal-content",
        ".merchant-modal__container-wrap",
        ".vertical-top-dialog",
        "[class*='modal']",
        "[class*='dialog']",
    ]
    btn_texts = ["确定", "确认", "知道了", "好的", "我知道了", "关闭"]
    dismissed = False
    deadline = time.time() + max_wait
    while time.time() < deadline:
        found_modal = False
        for f in b.page.frames:
            try:
                for sel in modal_selectors:
                    try:
                        loc = f.locator(sel)
                        n = loc.count()
                    except Exception:
                        continue
                    for i in range(n):
                        el = loc.nth(i)
                        try:
                            if not el.is_visible():
                                continue
                        except Exception:
                            continue
                        found_modal = True
                        # 1) 点常见按钮
                        clicked = False
                        for txt in btn_texts:
                            try:
                                btn = el.locator(f"button:has-text('{txt}'), a:has-text('{txt}')").first
                                if btn.count() > 0:
                                    btn.click(timeout=3000)
                                    print(f"[弹窗] 点击「{txt}」")
                                    dismissed = True
                                    clicked = True
                                    break
                            except Exception:
                                continue
                        if clicked:
                            continue
                        # 2) 点关闭按钮
                        try:
                            close = el.locator(".merchant-modal__close, .ant-modal-close, [class*='close'], i.merchant-icon-close").first
                            if close.count() > 0:
                                close.click(timeout=3000)
                                print("[弹窗] 点击关闭按钮")
                                dismissed = True
                        except Exception:
                            pass
            except Exception:
                continue
        if not found_modal:
            break
        time.sleep(0.5)
    # 兜底: ESC 关闭剩余弹窗
    if dismissed:
        try:
            b.page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass
    return dismissed


def _do_post_store_steps(b: Browser, sel: dict, settings: dict, store: dict, dry_run: bool, keyword: str,
                         submit_mode: bool = False, budget_id: int | None = None) -> dict:
    """门店选择之后的全部步骤：选笔记 -> 地域范围 -> 出价 -> 下一步 -> 保存草稿/保存并提交。
    submit_mode=True(--run): 点「保存并提交」真实提交, 成功后 mark_promoted 写查重库。
    submit_mode=False(--dry-run): 点「保存为草稿」, 不写库。"""
    cp = sel["create_promotion"]
    store_name = store["name"]

    # 8. 笔记选择：卡片在「内容种草」+「门店选择」完成后自动渲染
    # 容错: 等待超时(卡住)则叉掉门店重新选择, 看是否刷新出笔记卡片, 最多重试 3 轮
    note_card_ok = False
    for attempt in range(1, 4):
        print(f"[INFO] 等待笔记卡片自动渲染 (第{attempt}次尝试)...")
        try:
            b.wait_for("div.N-premium-note-wrapper", state="visible",
                       label="等待笔记卡片", timeout_ms=8000)
            note_card_ok = True
            print("[OK] 笔记卡片已渲染")
            break
        except Exception:
            if attempt < 3:
                print("[容错] 笔记卡片未渲染, 叉掉门店重新选择后再等...")
                try:
                    _select_store(b, cp, store)  # 内部会先叉掉所有门店标签再重选
                except Exception as e:
                    print(f"[WARN] 重选门店失败: {e}")
            else:
                print("[ERROR] 重试 3 轮后笔记卡片仍未渲染, 放弃该门店")
    if not note_card_ok:
        b.shot("note_card_not_rendered")
        return {"store": store_name, "skipped": True, "reason": "note_card_not_rendered"}

    # 点击笔记行的「修改」按钮打开笔记选择抽屉
    try:
        b.click(cp["note_modify_button"], "打开笔记选择抽屉")
    except Exception as e:
        b.shot("note_modify_click_fail")
        return {"store": store_name, "skipped": True, "reason": f"note_modify_click_fail: {e}"}
    # 等待笔记抽屉弹出
    drawer_found = False
    for drawer_sel in ["div.merchant-drawer__container-has-second",
                       "div.N-premium-notes-drawer__wrapper",
                       "div.merchant-drawer__container"]:
        try:
            b.wait_for(drawer_sel, state="visible", timeout_ms=10000)
            drawer_found = True
            print(f"[INFO] 笔记抽屉已出现: {drawer_sel}")
            break
        except Exception:
            continue
    if not drawer_found:
        print("[ERROR] 笔记抽屉未出现")
        b.shot("drawer_not_found")
        return {"store": store_name, "skipped": True, "reason": "drawer_not_open"}

    candidates = _scrape_notes(b, cp, store_name)
    print(f"[INFO] 抓取到 {len(candidates)} 篇候选笔记")
    if not candidates:
        print("[WARN] 未抓到任何笔记")
        b.shot("no_notes_found")
        return {"store": store_name, "skipped": True, "reason": "no_notes_in_drawer"}

    # --- 去重: 查重库(promoted_notes) + 预算组历史推广内容 双重过滤 ---
    # 1) 基于 promoted_notes 查重库（本次自动化提交过的）
    fresh, skipped_promoted = select_best_note(candidates, count=len(candidates))
    if skipped_promoted:
        print(f"[去重] 查重库跳过 {len(skipped_promoted)} 篇已提交笔记")
    candidates = fresh

    # 2) 基于该预算组历史推广内容（共享预算镜像 = 数据库）:
    #    仅当候选笔记与历史推广里的笔记是「同一篇」(标题精确一致) 才算重复, 不用主题关键词模糊匹配。
    if budget_id is not None:
        try:
            from budget_db import list_promoted_contents_for_budget
            hist_contents = list_promoted_contents_for_budget(budget_id)
            if hist_contents:
                before = len(candidates)
                candidates = [c for c in candidates
                              if not any(_same_note(c.title, hc) for hc in hist_contents if hc)]
                print(f"[去重] 预算组历史推广精确匹配跳过 {before - len(candidates)} 篇(同标题)")
        except Exception as e:
            print(f"[WARN] 预算组历史内容去重失败: {e}")

    if not candidates:
        print(f"[SKIP] 门店 {store_name}: 无可用的未推广笔记")
        mark_store_done(store_name, "no_unpromoted_notes")  # 确定性推完: 全部已推, 下次直接跳过
        return {"store": store_name, "skipped": True, "reason": "no_unpromoted_notes"}

    candidates.sort(key=lambda n: n.views, reverse=True)
    note = candidates[0]

    # 浏览量门槛: 仅推广浏览量 >= min_views 的笔记。
    # 因已按浏览量降序, 最高的一篇若 < 门槛, 后面全部更小 → 直接跳到下一家门店。
    min_views = int(settings.get("note_selection", {}).get("min_views") or 0)
    if min_views > 0 and note.views < min_views:
        print(f"[SKIP] {store_name}: 最高浏览量笔记 {note.views} < 门槛 {min_views}, 该门店无可推笔记, 跳到下一家")
        mark_store_done(store_name, f"views_below_min({note.views}<{min_views})")  # 确定性推完: 最高都<门槛, 下次直接跳过
        return {"store": store_name, "skipped": True,
                "reason": f"views_below_min({note.views}<{min_views})"}

    print(f"[选中] {note.title[:60]}... | 浏览量 {note.views}")

    _check_note(b, cp, note)
    b.click("text=确认修改", "确认修改笔记")
    time.sleep(2)

    # 「确认修改」后会弹出确认弹窗, 用通用清理函数关闭(覆盖各种弹窗类型)
    time.sleep(1.5)  # 等弹窗出现
    try:
        _dismiss_modals(b)
    except Exception as e:
        print(f"[WARN] 笔记确认弹窗处理失败: {e}")
        b.shot("note_confirm_modal_fail")

    # 等抽屉关闭
    for _ in range(10):
        closed = True
        for sel in ["div.merchant-drawer__container-has-second",
                    "div.N-premium-notes-drawer__wrapper"]:
            try:
                for f in b.page.frames:
                    if f.locator(sel).count() > 0 and f.locator(sel).first.is_visible():
                        closed = False
                        break
            except Exception:
                pass
        if closed:
            break
        time.sleep(0.5)
    print(f"[OK] 笔记抽屉已关闭" if closed else "[WARN] 笔记抽屉可能仍打开")

    # 滚动到 地域/出价 区域
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            try:
                f.evaluate("window.scrollTo(0, 500)")
                time.sleep(1)
            except Exception:
                pass
            break

    # 9. 修改地域范围
    _set_region(b, cp, settings)
    # 9.5 修改定向人群（地域下一行即推广人群区域）
    _set_crowd_targeting(b, cp, settings)
    # 修改单价
    _set_bid(b, cp, settings)

    # 10. 下一步 -> 创意页 -> 保存为草稿 / 保存并提交
    # 点「下一步」前先清理可能残留的弹窗(防止遮挡按钮)
    try:
        _dismiss_modals(b, max_wait=3.0)
    except Exception:
        pass
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            try:
                f.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
            except Exception:
                pass
            break
    b.click(cp["next_step"], "下一步")
    time.sleep(1.5)

    if submit_mode:
        # ===== 真实提交模式 (--run) =====
        print("[INFO] 创意页滑到底, 点保存并提交")
        b.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.8)
        try:
            b.click(cp["save_and_submit"], "保存并提交")
        except Exception as e:
            b.shot("submit_btn_fail")
            print(f"[ERROR] 点击「保存并提交」失败: {e}")
            return {"store": store_name, "submitted": False, "error": f"submit_btn_fail: {e}"}
        # 等提交结果: 可能弹确认弹窗或直接跳转, 用通用清理关闭
        time.sleep(2)
        b.shot("after_submit")
        try:
            _dismiss_modals(b, max_wait=6.0)
        except Exception as e:
            print(f"[WARN] 提交弹窗处理失败: {e}")
        time.sleep(1.5)
        b.shot("after_submit_confirm")
        # 提交成功 -> 写查重库
        try:
            mark_promoted(note, source="auto")
            print(f"[OK] 已写入查重库: {note.title[:40]}")
        except Exception as e:
            print(f"[WARN] 写入查重库失败: {e}")
        print(f"[OK] 保存并提交 点击成功 | 笔记: {note.title[:40]}")
        return {"store": store_name, "note": note.title[:60], "views": note.views,
                "submitted": True, "saved_draft": False}
    else:
        # ===== 草稿模式 (--dry-run) =====
        print("[INFO] 创意页滑到底, 点保存为草稿")
        b.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        try:
            b.click("text=保存为草稿", "保存为草稿")
            b.shot("after_save_draft")
            print(f"[OK] 保存为草稿 点击成功 | 笔记: {note.title[:40]}")
            return {"store": store_name, "note": note.title[:60], "views": note.views, "saved_draft": True}
        except Exception as e:
            b.shot("save_draft_failed")
            print(f"[ERROR] 保存为草稿 失败: {e}")
            return {"store": store_name, "saved_draft": False, "error": str(e)}


def _set_region(b: Browser, cp: dict, settings: dict):
    """修改地域范围：点「推广地域」修改 → 抽屉打开 → 选「门店附近区域」→ 填距离 → 保存抽屉。"""
    promo = settings["promotion"]
    # 1) 点击「推广地域」的修改按钮, 弹出抽屉
    try:
        b.click(".cpm-edit-item.promo-region button.edit-btn", "推广地域-修改")
        time.sleep(0.8)
    except Exception as e:
        print(f"[WARN] 点击推广地域修改按钮失败: {e}")
        b.shot("region_edit_btn_fail")
        return
    # 2) 等抽屉打开
    try:
        b.wait_for("div.merchant-drawer__container.promo-city-draw",
                    state="visible", label="等待地域抽屉", timeout_ms=10000)
    except Exception:
        print("[ERROR] 推广地域抽屉未打开")
        b.shot("region_drawer_not_open")
        return
    # 3) 选「门店附近区域」radio (value=1)
    try:
        b.click("text=门店附近区域", "选择门店附近区域")
        time.sleep(0.5)
    except Exception as e:
        print(f"[WARN] 选择门店附近区域失败: {e}")
        b.shot("region_nearby_fail")
    # 4) 填距离 (抽屉内的 input, 出现于选门店附近区域后)
    dist = promo.get("distance_km")
    if dist and not cp["distance_input"].startswith("TODO"):
        try:
            b.fill(cp["distance_input"], str(dist), "地域-距离km")
        except Exception as e:
            print(f"[WARN] 距离填充失败: {e}")
            b.shot("distance_fill_fail")
    elif dist:
        print(f"[SKIP] distance_input 仍是 TODO, 跳过距离填充 {dist}")
    # 5) 点抽屉底部的「保存」提交
    try:
        # drawer 在 cpm-edit frame 内
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                save_btn = f.locator(".merchant-drawer__container.promo-city-draw button:has-text(\"保存\")").first
                save_btn.click(timeout=5000)
                print("[OK] 地域抽屉已点保存")
                break
        time.sleep(0.8)
    except Exception as e:
        print(f"[WARN] 地域抽屉保存失败: {e}")
        b.shot("region_drawer_save_fail")


def _set_crowd_targeting(b: Browser, cp: dict, settings: dict):
    """修改定向人群（插入在地域之后、出价之前，页面顺序：地域下一行即推广人群）。
    流程与 batch_target_audience 一致：点定向radio → 查看/修改 → 自定义人群标签 →
    勾选9项（年龄+性别+兴趣）→ 点「保存设置」→ 确认抽屉关闭。
    注意：不点「保存并提交」，由主流程后续点「下一步」→ 创意页 → 保存并提交。
    失败只 WARN 不阻塞主流程。"""
    edit_frame = None
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            edit_frame = f
            break
    if edit_frame is None:
        print("[WARN] 未找到 cpm-edit frame, 跳过定向人群")
        return
    try:
        item = _crowd_item(edit_frame)
        if item is None:
            print("[WARN] 推广人群区域未找到, 跳过定向人群")
            return
        if not _click_targeted_radio(item):
            print("[WARN] 点定向人群 radio 失败, 跳过定向人群")
            return
        if not _open_crowd_drawer(edit_frame):
            print("[WARN] 人群抽屉未打开, 跳过定向人群")
            return
        if not _click_custom_tag_tab(edit_frame):
            print("[WARN] 未找到自定义人群标签页签, 尝试直接勾选")
        time.sleep(0.8)
        # 勾选年龄+性别（先滚回顶部渲染用户属性区）
        _scroll_drawer_top(edit_frame)
        time.sleep(0.5)
        checked = 0
        for text, label in TARGET_TAGS[:5]:
            if _check_tag(edit_frame, text, label):
                checked += 1
        # 勾选兴趣（展开"休闲娱乐" + 慢滚动触发渲染）
        _ensure_industry_expanded(edit_frame)
        time.sleep(0.8)
        for text, label in TARGET_TAGS[5:]:
            if _check_tag(edit_frame, text, label):
                checked += 1
        if checked < 6:
            print(f"[WARN] 人群标签勾选不足({checked}/{len(TARGET_TAGS)}), 放弃人群修改(继续主流程)")
            return
        # 保存设置 → 确认抽屉真正关闭（保存生效）
        drawer_closed = False
        for attempt in range(1, 4):
            save_btn = edit_frame.locator("button:has-text('保存设置')").first
            if save_btn.count() == 0:
                break
            try:
                save_btn.scroll_into_view_if_needed(timeout=3000)
                save_btn.click(timeout=5000)
            except Exception:
                try:
                    save_btn.evaluate("el => el.click()")
                except Exception:
                    pass
            print(f"[OK] 点击人群保存设置 (第{attempt}次)")
            time.sleep(1.5)
            try:
                if edit_frame.locator("button:has-text('保存设置')").count() == 0:
                    drawer_closed = True
                    break
            except Exception:
                drawer_closed = True
                break
        if not drawer_closed:
            print("[WARN] 人群保存设置后抽屉未关闭, 继续主流程")
            return
        print(f"[OK] 定向人群已设置({checked}项标签), 保存设置完成, 抽屉已关闭")
    except Exception as e:
        print(f"[WARN] 定向人群修改失败: {str(e)[:80]}")


def _set_bid(b: Browser, cp: dict, settings: dict):
    """修改单价：在「预算和出价」区填单次点击出价（min=0.8 max=100 step=0.1）。"""
    promo = settings["promotion"]
    bid = promo.get("bid_per_click")
    if not bid:
        print("[SKIP] 未配置 bid_per_click, 跳过出价填充")
        return
    # 先滚到出价区域（cpm-edit frame 内底部）
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            try:
                f.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
            except Exception:
                pass
            break
    if cp["bid_input"].startswith("TODO"):
        print(f"[SKIP] bid_input 仍是 TODO, 跳过填充 {bid}")
        b.shot("todo_skip_bid")
        return
    try:
        b.fill(cp["bid_input"], str(bid), "出价-单次点击")
    except Exception as e:
        print(f"[WARN] 出价填充失败: {e}")
        b.shot("bid_fill_fail")


def _is_store_dropdown_open(b: Browser) -> bool:
    for f in b.page.frames:
        try:
            items = f.locator("li.merchant-select-dropdown-menu__item")
            if items.count() > 0 and items.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _close_store_dropdown(b: Browser, cp: dict):
    # 优先点击店铺选择器右侧远处的水平空白
    try:
        sel = b.page.locator(cp["store_selector_open"]).first
        box = sel.bounding_box(timeout=3000)
        if box:
            vp = b.page.viewport_size or {"width": 1280, "height": 800}
            click_x = min(box["x"] + box["width"] + 180, vp["width"] - 15)
            click_y = box["y"] + box["height"] / 2
            b.page.mouse.click(click_x, click_y)
            time.sleep(0.5)
            if not _is_store_dropdown_open(b):
                return
            print("[门店] 点击后下拉仍开, 尝试菜单右侧空白")
    except Exception as e:
        print(f"[门店] 选择器空白点击失败: {e}")

    # 从下拉项父级包围盒右侧点击
    for f in b.page.frames:
        try:
            item = f.locator("li.merchant-select-dropdown-menu__item").first
            if item.count() == 0:
                continue
            box_data = item.evaluate("""el => {
                const p = el.parentElement;
                if (!p) return null;
                const r = p.getBoundingClientRect();
                return {x: r.x, y: r.y, width: r.width, height: r.height};
            }""")
            if box_data and box_data.get("width", 0) > 0:
                vp = b.page.viewport_size or {"width": 1280, "height": 800}
                click_x = min(box_data["x"] + box_data["width"] + 80, vp["width"] - 12)
                click_y = box_data["y"] + box_data["height"] / 2
                b.page.mouse.click(click_x, click_y)
                time.sleep(1.2)
                if not _is_store_dropdown_open(b):
                    return
        except Exception:
            continue
    # 兜底
    try:
        b.page.keyboard.press("Escape")
        time.sleep(1.0)
        if not _is_store_dropdown_open(b):
            return
    except Exception:
        pass
    try:
        b.page.mouse.click(300, 50)
        time.sleep(1.2)
    except Exception:
        pass


def _safe_fill(b: Browser, selector: str, value: str, label: str) -> bool:
    if not selector or str(selector).startswith("TODO_EXPLORE"):
        print(f"[SKIP] {label}: 选择器未回填(TODO), 跳过填充 {value}")
        try:
            b.shot(f"todo_skip_{label}")
        except Exception:
            pass
        return False
    try:
        b.fill(selector, value, label)
        return True
    except Exception as e:
        print(f"[WARN] {label} 填充失败: {e}")
        try:
            b.shot(f"fill_fail_{label}")
        except Exception:
            pass
        return False


def _split_store_name(name: str):
    """拆分门店名为 (品牌词, 分店词)。兼容中文括号（）和英文括号()。"""
    import re
    m = re.search(r"[（(](.+?)[)）]", name)
    if m:
        return name[:m.start()].strip(), m.group(1).strip()
    return name.strip(), ""


def _click_store_item(b: Browser, cp: dict, store: dict) -> bool:
    """在下拉中点击目标门店。用「品牌词+分店词」双关键词枚举匹配,
    不依赖括号全角/半角, 避免 has-text 完整名匹配失败。"""
    name = store["name"]
    brand, loc = _split_store_name(name)
    brand_kw = brand[:8] if brand else ""
    loc_kw = loc[:8] if loc else ""

    for f in b.page.frames:
        try:
            items = f.locator("li.merchant-select-dropdown-menu__item")
            n = items.count()
            for i in range(n):
                txt = (items.nth(i).inner_text() or "")
                if not txt.strip():
                    continue
                if loc_kw and brand_kw:
                    if brand_kw in txt and loc_kw in txt:
                        items.nth(i).click()
                        print(f"[门店] ✓ 点击下拉项: {txt[:50]}")
                        return True
                elif brand_kw and brand_kw in txt:
                    items.nth(i).click()
                    print(f"[门店] ✓ 点击下拉项: {txt[:50]}")
                    return True
        except Exception:
            continue
    # 兜底: 用完整名 has-text (英文括号)
    try:
        item_sel = cp["store_selector_item"].replace("{store_name}", name)
        b.click(item_sel, "选择门店")
        return True
    except Exception:
        return False


def _select_store(b: Browser, cp: dict, store: dict):
    """merchant-select 门店选择器：清空(优先)→ 兜底叉掉 → 打开 → 搜索 → 选中 → 面包屑提交。
    store: {name, search_keyword, budget_keyword}"""
    name = store["name"]
    search_kw = store.get("search_keyword") or name.split("(")[0].split("（")[0][:6] or name[:4]
    brand, _loc = _split_store_name(name)
    brand_kw = brand[:8]

    # 0) 优先点击「清空」按钮一次性清除所有已选门店。
    #    最稳健: 不受门店名长度 / 标签数量影响, 不用逐个点 X。
    #    若本就无选择(按钮不存在或不可见), 视为正常, 不报错。
    cleared = False
    try:
        for f in b.page.frames:
            if "cpm-edit" in (f.url or ""):
                cb = f.locator(cp["store_selector_clear"])
                if cb.count() > 0 and cb.first.is_visible():
                    cb.first.click(timeout=3000)
                    time.sleep(1.0)
                    cleared = True
                break
    except Exception:
        pass
    if cleared:
        print("[门店] ✓ 点击「清空」按钮, 已清除所有已选门店")

    # 1) 兜底: 逐个叉掉任何残留的门店标签(防清空按钮未出现/失效)
    deadline = time.time() + 12
    while time.time() < deadline:
        edit_frame_ready = None
        for f in b.page.frames:
            try:
                if "cpm-edit" in (f.url or "") and \
                   f.locator("i.merchant-tag__close-btn").count() > 0:
                    edit_frame_ready = f
                    break
            except Exception:
                continue
        if edit_frame_ready:
            break
        time.sleep(0.5)

    total_removed = 0
    for _ in range(20):
        removed_this_round = False
        for f in b.page.frames:
            try:
                close_btns = f.locator("i.merchant-tag__close-btn")
                n = close_btns.count()
                if n > 0:
                    close_btns.first.click()
                    time.sleep(0.5)
                    removed_this_round = True
                    total_removed += 1
                    break
            except Exception:
                continue
            if removed_this_round:
                break
        if not removed_this_round:
            break
    if total_removed:
        print(f"[门店] 兜底叉掉残留门店标签: {total_removed} 个")

    # 如果叉完后只剩目标门店，跳过搜索
    need_select = True
    try:
        current_tags = b.page.locator(".N-select-shop-item")
        if current_tags.count() == 1:
            tag_text = current_tags.first.inner_text()
            if brand_kw in tag_text:
                print(f"[门店] 已有目标门店: {tag_text}, 跳过搜索选择")
                need_select = False
    except Exception:
        pass

    if need_select:
        b.click(cp["store_selector_open"], "打开门店下拉")
        b.fill(cp["store_selector_search"], search_kw, "搜索门店")
        time.sleep(1.5)
        ok = _click_store_item(b, cp, store)
        if not ok:
            print("[WARN] 下拉中未找到目标门店, 截图留证")
            b.shot("store_item_not_found")
        time.sleep(2.0)

    # 验证目标门店已选中
    selected_ok = False
    try:
        for f in b.page.frames:
            tag_check = f.locator(f".N-select-shop-item:has-text(\"{brand_kw}\"), .merchant-tag--closable:has-text(\"{brand_kw}\")")
            if tag_check.count() > 0:
                print(f"[门店] ✓ 确认已选中: {tag_check.first.inner_text()[:40]}")
                selected_ok = True
                break
    except Exception:
        pass
    if not selected_ok:
        print(f"[WARN] 未在已选标签中找到 '{brand_kw}', 截图留证")
        b.shot("store_not_selected")

    # 关掉下拉（提交选择）。安全的关闭方法是点击面包屑/标题区域，
    # 避免点击表单内任何可能重置 推广目的 的交互元素。
    for f in b.page.frames:
        if "cpm-edit" in (f.url or ""):
            try:
                f.locator(".cpm-edit-bread-wrapper, .cpm-component-header").first.click(timeout=5000)
                time.sleep(1.5)
                print("[门店] 点面包屑关闭下拉(提交选择)")
            except Exception:
                print("[门店] 面包屑点击失败, 由后续操作自然关闭下拉")
            break


def _normalize_title(t: str) -> str:
    import re
    return re.sub(r'\s+', ' ', t.strip())


def _same_note(candidate_title: str, hist_content: str) -> bool:
    """判断候选笔记标题与历史推广内容是否为同一篇笔记(精确匹配)。
    promotions.content 格式: '门店优质笔记 <笔记标题> <正文>...'。
    去掉前缀后, 比较前 30 个字符(标题部分, 容忍页面/库两端截断差异)。"""
    body = hist_content
    if body.startswith("门店优质笔记"):
        body = body[len("门店优质笔记"):].strip()
    elif body.startswith("该店优质笔记"):
        body = body[len("该店优质笔记"):].strip()
    a = _normalize_title(candidate_title)[:30]
    b = _normalize_title(body)[:30]
    return bool(a) and a == b


def _scrape_notes(b: Browser, cp: dict, store: str) -> list[Note]:
    items = _locate_in_frames(b, cp["note_item"])
    notes = []
    for i in range(items.count()):
        try:
            it = items.nth(i)
            # title 不截断, 用 _normalize_title 统一处理(与 _check_note 保持一致)
            title = _normalize_title(it.locator(cp["note_title"]).first.inner_text())
            views = 0
            pub = ""
            try:
                views_txt = it.locator(cp["note_views"]).first.inner_text()
                views = parse_views(views_txt)
            except Exception:
                pass
            try:
                pub = it.locator(cp["note_publish_date"]).first.inner_text().strip()
            except Exception:
                pass
            note_id = it.get_attribute("data-id") or None  # 仅真实 data-id, 不用 card-N 序号
            notes.append(Note(note_id=note_id, title=title, store=store, views=views, publish_date=pub))
        except Exception as e:
            print(f"  [WARN] 抽屉笔记 #{i} 跳过: {e}")
            b.shot(f"scrape_skip_item_{i}")
            continue
    return notes


def _click_note_item(it, cp: dict) -> bool:
    """点击单张笔记卡片使其被选中。优先点 checkbox, 否则点卡片本体。"""
    try:
        checkbox = it.locator(cp["note_checkbox"]).first
        if checkbox.count() > 0:
            checkbox.click()
            return True
    except Exception:
        pass
    try:
        it.click()
        return True
    except Exception:
        return False


def _check_note(b: Browser, cp: dict, note: Note):
    """选中抽屉里的目标笔记。优先按 note_id 精确匹配, 其次按 title 精确匹配。
    匹配失败必须抛异常, 防止静默提交默认/其他笔记。"""
    items = _locate_in_frames(b, cp["note_item"])
    total = items.count()
    for i in range(total):
        it = items.nth(i)
        try:
            # 1) 优先 note_id 精确匹配
            if note.note_id and not note.note_id.startswith("card-"):
                data_id = it.get_attribute("data-id")
                if data_id and data_id == note.note_id:
                    if _click_note_item(it, cp):
                        print(f"[笔记] ✓ 按 note_id 选中: {note.title[:40]}")
                        return True
            # 2) title 精确匹配（与 _scrape_notes 一致: normalize 后比较）
            title_txt = _normalize_title(it.locator(cp["note_title"]).first.inner_text())
            if title_txt == note.title:
                if _click_note_item(it, cp):
                    print(f"[笔记] ✓ 按 title 选中: {note.title[:40]}")
                    return True
        except Exception:
            continue
    # 匹配失败: 必须报错, 不能静默提交默认笔记
    raise FlowError(
        f"笔记匹配失败, 未选中目标笔记: {note.title[:40]} (抽屉内共{total}张卡片)"
    )


def _locate_in_frames(b: Browser, selector: str):
    for f in b.page.frames:
        try:
            loc = f.locator(selector)
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return b.page.locator(selector)
