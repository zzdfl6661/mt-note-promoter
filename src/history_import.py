"""阶段1：从后台历史推广列表爬取已推广笔记, 初始化查重库。
运行: python src/main.py --import-history
选择器回填前会提示先完成阶段0探测。"""
from browser import Browser, load_json
from notes import Note, mark_promoted, list_promoted


def run():
    settings = load_json("settings.json")
    sel = load_json("selectors.json")
    h = sel["history"]

    with Browser(settings) as b:
        b.goto(settings["portal_url"])
        b.click(h["promotion_list_entry"], "历史推广列表")
        items = b.page.locator(h["promotion_list_item"])
        n = items.count()
        print(f"[INFO] 历史推广条目数: {n}")
        imported = 0
        for i in range(n):
            try:
                it = items.nth(i)
                title = it.locator(h["promotion_note_title"]).inner_text().strip()
                # 历史条目所属门店: 探测阶段确认字段后完善; 暂用白名单第一个
                store = settings["store_whitelist"][0]
                mark_promoted(
                    Note(note_id="", title=title, store=store, views=0, publish_date=""),
                    source="history",
                )
                imported += 1
                print(f"  [导入] {title}")
            except Exception as e:
                print(f"  [WARN] 跳过无法解析的历史条目 #{i}: {e}")
                continue
        print(f"\n[OK] 导入完成, 共 {imported} 条。当前库内记录:")
        for row in list_promoted():
            print(" ", row)
