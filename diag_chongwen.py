"""临时诊断: 探测崇文预算行 hover 后是否出现"新增推广"按钮"""
import sys, time
sys.path.insert(0, 'src')
from browser import Browser, load_json

settings = load_json('settings.json')
with Browser(settings) as b:
    # 找预算列表页
    page = None
    for p in b.ctx.pages:
        if 'budget-group-list' in (p.url or ''):
            page = p
            break
    if page is None:
        page = b.ctx.pages[0]
        page.goto('https://e.dianping.com/app/peon-cpm-ncpm/html/budget-group-list.html')
        time.sleep(6)

    print('=== frames ===')
    for f in page.frames:
        print('  frame:', (f.url or '')[:100])

    print('=== 找崇文行 ===')
    for f in page.frames:
        try:
            rows = f.locator('tr.merchant-table__row:has(td.merchant-table__cell-fixed-left-first:has-text("崇文"))')
            n = rows.count()
            print(f'  frame {f.url[:50]}: 崇文行数 = {n}')
            if n:
                row = rows.first
                box = row.bounding_box()
                print(f'  行 bbox: {box}')
                # hover 前 dump 行内 button
                btns = row.locator('button').all_text_contents()
                print(f'  hover前 行内按钮: {btns}')
                try:
                    row.hover()
                    time.sleep(2)
                except Exception as e:
                    print(f'  hover 异常: {e}')
                btns2 = row.locator('button').all_text_contents()
                print(f'  hover后 行内按钮: {btns2}')
                # 全页找"新增推广"按钮
                for f2 in page.frames:
                    try:
                        add_btns = f2.locator('button:has-text("新增推广")')
                        print(f'  frame {f2.url[:40]} 新增推广按钮数: {add_btns.count()}')
                    except Exception as e:
                        print(f'  err {e}')
                # 行HTML摘要
                html = row.evaluate('el => el.outerHTML')
                print(f'  行HTML(前800): {html[:800]}')
                break
        except Exception as e:
            print(f'  scan err: {e}')
print('诊断完成')
