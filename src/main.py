"""美团经营宝笔记推广自动化 · 入口

用法:
  python src/main.py --explore          阶段0: 打开浏览器供人工登录+探测页面(只读)
  python src/main.py --import-history   阶段1: 爬取历史推广初始化查重库
  python src/main.py --dry-run          阶段2: 演练模式(填完不提交)
  python src/main.py --run              阶段3: 正式运行(提交前按 auto_submit 决定是否确认)
  python src/main.py --db               查看查重库内容
"""
import argparse
import sys
import traceback


def main():
    p = argparse.ArgumentParser(description="美团经营宝笔记推广自动化")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--explore", action="store_true", help="阶段0: 页面探测(只读)")
    g.add_argument("--import-history", action="store_true", help="阶段1: 初始化查重库")
    g.add_argument("--dry-run", action="store_true", help="阶段2: 演练不提交")
    g.add_argument("--run", action="store_true", help="阶段3: 正式运行")
    g.add_argument("--db", action="store_true", help="查看查重库")
    g.add_argument("--refresh-stores", action="store_true",
                   help="清除门店 done 标记(新增笔记后强制重扫已推完门店)")
    p.add_argument("--store", type=str, default=None,
                   help="仅处理指定门店(覆盖 store_whitelist)")
    args = p.parse_args()

    try:
        if args.refresh_stores:
            from notes import clear_store_done, list_store_done
            n = clear_store_done(args.store)
            print(f"已清除 {n} 条门店 done 标记" + (f"({args.store})" if args.store else ""))
            rows = list_store_done()
            if rows:
                print("剩余 done 标记:")
                for s, r, at in rows:
                    print(f"  {s} | {r} | {at}")
            else:
                print("当前无 done 标记, 全部门店将重新执行")
            return

        if args.db:
            from notes import list_promoted
            rows = list_promoted()
            print(f"查重库共 {len(rows)} 条:")
            for title, store, at, src in rows:
                print(f"  [{src}] {store} | {title} | {at}")
            return

        if args.explore:
            explore()
            return

        if args.import_history:
            import history_import
            history_import.run()
            return

        import flow
        results = flow.run(dry_run=args.dry_run, single_store=args.store)
        print("\n===== 运行结果汇总 =====")
        for r in results:
            print(" ", r)

    except Exception as e:
        print(f"\n[失败] {e}", file=sys.stderr)
        traceback.print_exc()
        print("已停止。请查看 logs/ 下最新目录中的截图定位断点。", file=sys.stderr)
        sys.exit(1)


def explore():
    """阶段0: 启动持久化浏览器, 人工登录并沿流程走一遍。
    浏览器保持打开, 登录态自动保存到 data/browser_profile。"""
    from browser import Browser, load_json
    settings = load_json("settings.json")
    with Browser(settings, keep_alive=True) as b:
        b.goto(settings["portal_url"])
        print(
            "\n[探测模式] 浏览器已打开:\n"
            " 1. 请完成登录(登录态会自动保存, 下次无需再登)\n"
            " 2. 沿人工流程走到各关键页面, 停留在需要记录的界面\n"
            " 3. 探测期间不要点击「保存并提交」\n"
        )
        b.pause_for_human("完成登录和页面浏览后回车退出(登录态已保存)")


if __name__ == "__main__":
    main()
