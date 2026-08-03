"""只读验证：直达 URL 是否可用（不点任何提交按钮）。

验证内容：
1. goto budget-group-list.html 后最终 URL 是否被重定向
2. 预算表格是否正常渲染出数据行
3. 从 goto 到表格就绪耗时多少
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from browser import Browser, load_json  # noqa: E402

DIRECT_URLS = {
    "budget_list": "https://e.dianping.com/app/peon-cpm-ncpm/html/budget-group-list.html",
}


def main():
    settings = load_json("settings.json")
    settings["browser"]["slow_mo_ms"] = 0  # 验证时不加延迟

    with Browser(settings) as b:
        # 另开一个标签页做验证，不干扰用户当前页面
        page = b.ctx.new_page()
        b.page = page

        url = DIRECT_URLS["budget_list"]
        print(f"\n[验证] goto {url}")
        t0 = time.time()
        page.goto(url, wait_until="domcontentloaded")

        final_url = page.url
        print(f"[结果] 最终 URL: {final_url}")

        if "budget-group-list" not in final_url:
            print(f"[✗ 失败] 被重定向到了别处 → 直达 URL 不可用，必须走菜单点击")
            page.close()
            return

        print("[✓] URL 未被重定向")

        # 等预算表格 frame + 数据行
        row_count = 0
        frame_url = None
        deadline = time.time() + 20
        while time.time() < deadline:
            for f in page.frames:
                if "budget-group-list" in (f.url or ""):
                    try:
                        n = f.locator("tr.merchant-table__row").count()
                        if n > 0:
                            row_count = n
                            frame_url = f.url
                            break
                    except Exception:
                        pass
            if row_count:
                break
            time.sleep(0.3)

        elapsed = time.time() - t0

        if row_count:
            print(f"[✓] 预算表格已渲染: {row_count} 行")
            print(f"[✓] frame URL: {frame_url}")
            print(f"[✓] 总耗时: {elapsed:.1f}s  (对比菜单点击路径约 62s)")
            # 抽样打印前 3 行，确认是真实数据
            for f in page.frames:
                if "budget-group-list" in (f.url or ""):
                    rows = f.locator("tr.merchant-table__row")
                    for i in range(min(3, rows.count())):
                        txt = rows.nth(i).inner_text().replace("\n", " | ")[:80]
                        print(f"      行{i+1}: {txt}")
                    break
            print("\n[结论] 直达 URL 可用 ✓")
        else:
            print(f"[✗ 失败] {elapsed:.1f}s 内表格未渲染出数据行 → 直达 URL 不可靠")
            print("[结论] 需保留菜单点击路径")

        page.close()


if __name__ == "__main__":
    main()
