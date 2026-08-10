#!/usr/bin/env python3
"""单条执行：只处理第一个未定向(智选)推广，完整走通 修改→保存设置→保存并提交，供用户验证。"""
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

with Browser(settings) as b:
    b.page.on("dialog", lambda d: d.accept())
    lf = m._navigate_to_promotion_list(b, sel, settings)
    m._set_page_size(lf, "100条/页")
    time.sleep(1.5)
    lf = m._wait_for_table(b, sel, timeout_s=6) or lf
    rows = m._extract_promotions(lf, sel, set())  # 不过滤进度，全部扫描
    print(f"扫描到 {len(rows)} 条推广")

    done = False
    for pid, lid in rows:
        print(f"\n--- {pid} ---")
        try:
            ef = m._goto_edit(b, at, lid)
            if not ef:
                print(f"{pid} 未进入编辑页，跳过")
                continue
            item = m._crowd_item(ef)
            if item is None:
                print(f"{pid} 推广人群区域未找到，跳过")
                continue
            if m._crowd_is_targeted(item):
                print(f"{pid} 已定向，跳过")
                continue
            # 未定向 → 完整修改 + 保存设置 + 保存并提交
            m._modify_crowd(b, ef, at, sel, dry_run=False)
            print(f"\n>>> {pid} 已修改并提交，请到浏览器验证 <<<")
            done = True
            break
        except Exception as e:
            print(f"{pid} 处理异常: {str(e)[:100]}")
            continue

    if not done:
        print("\n本页无未定向推广（全部已定向或处理失败）")
