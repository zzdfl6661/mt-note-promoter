#!/usr/bin/env python3
"""强制修复指定推广：跳过已定向判断，直接走完整修改流程（勾9项→保存设置→保存并提交→确认）。"""
import sys, time
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

from browser import Browser, load_json
import batch_target_audience as m

settings = load_json("settings.json")
sel = load_json("selectors.json")
at = sel["audience_targeting"]

TARGET_IDS = ["推广20260804598", "推广20260804dfc", "推广202608045b1", "推广20260804a70",
              "推广20260804f45", "推广20260804227", "推广202608030ce", "推广20260803362",
              "推广20260803bec", "推广20260803dc6", "推广20260803387", "推广202608030af",
              "推广202607313aa"]

with Browser(settings) as b:
    b.page.on("dialog", lambda d: d.accept())
    lf = m._navigate_to_promotion_list(b, sel, settings)
    m._set_page_size(lf, "100条/页")
    time.sleep(2)
    lf = m._wait_for_table(b, sel, timeout_s=8) or lf
    # 映射 pid -> lid
    pid2lid = {}
    for r in lf.locator(at["promotion_row"]).all():
        try:
            pid = r.locator(at["promotion_id_link"]).first.inner_text().strip()
            lid = r.get_attribute("data-row-key")
            if pid and lid:
                pid2lid[pid] = lid
        except Exception:
            pass
    print(f"映射 {len(pid2lid)} 条")
    missing = [t for t in TARGET_IDS if t not in pid2lid]
    if missing:
        print(f"本页未找到: {missing}")

    for pid in TARGET_IDS:
        lid = pid2lid.get(pid)
        if not lid:
            print(f"{pid} 跳过（列表未找到）")
            continue
        print(f"\n--- {pid} 强制修改 ---")
        try:
            ef = m._goto_edit(b, at, lid)
            if not ef:
                print(f"{pid} 未进入编辑页")
                continue
            item = m._crowd_item(ef)
            if item is None:
                print(f"{pid} 人群区域未找到")
                continue
            m._modify_crowd(b, ef, at, sel, dry_run=False)
            print(f">>> {pid} 完成")
        except Exception as e:
            print(f"{pid} 异常: {str(e)[:80]}")
