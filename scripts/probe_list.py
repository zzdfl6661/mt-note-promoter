#!/usr/bin/env python3
"""探测智选展位推广列表的 DOM 结构，用于回填选择器。"""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from browser import Browser, load_json  # noqa: E402


def main():
    settings = load_json("settings.json")
    sel = load_json("selectors.json")
    nav = sel["nav"]
    at = sel["audience_targeting"]

    with Browser(settings) as b:
        b.goto(settings["portal_url"])
        import time
        time.sleep(2)
        b.click(nav["promotion_center"], "推广中心")
        time.sleep(1.5)
        b.click(nav["promotion_center_sub"], "推广中心(子菜单)")
        time.sleep(1.5)
        b.click(nav["smart_display"], "智选展位")
        time.sleep(3)
        b.wait_for(at["promotion_list_page_indicator"], state="visible", timeout_ms=15000)

        print("\n=== frames ===")
        for i, f in enumerate(b.page.frames):
            print(f"[{i}] {f.url[:120]}")

        # 找列表 frame
        list_frame = None
        for f in b.page.frames:
            try:
                if f.locator("tr.merchant-table__row").count() > 0:
                    list_frame = f
                    break
            except Exception:
                continue

        if not list_frame:
            print("未找到列表 frame")
            return

        print(f"\n列表 frame URL: {list_frame.url}")

        rows = list_frame.locator("tr.merchant-table__row").all()
        print(f"行数: {len(rows)}")
        for idx, row in enumerate(rows[:5]):
            print(f"\n--- 行 {idx} ---")
            print("inner_text:")
            print(row.inner_text()[:500])
            print("\nHTML:")
            print(row.evaluate("el => el.outerHTML")[:800])
            # 查找所有 a 标签
            links = row.locator("a").all()
            print(f"\n链接数: {len(links)}")
            for link in links:
                try:
                    print(f"  text={link.inner_text().strip()[:60]} href={link.get_attribute('href') or ''}")
                except Exception:
                    pass

        # 分页器
        print("\n=== 分页器 ===")
        paginations = list_frame.locator(".merchant-pagination").all()
        print(f"分页器个数: {len(paginations)}")
        if paginations:
            print(paginations[0].evaluate("el => el.outerHTML")[:1200])


if __name__ == "__main__":
    main()
