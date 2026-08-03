"""共享预算/推广数据库：增量爬取 + 关键词匹配 + 笔记去重。

表结构（与 notes.py 共用同一个 promoted.db）：
- shared_budgets: 共享预算名称（用于和门店做关键词匹配）
- promotions: 推广名称 + 推广内容（用于笔记去重）

使用：
- 进入共享预算列表页 -> 调 scrape_and_upsert(b) 增量入库
- 选门店 -> budget_name 转 store_keyword
- 选笔记 -> list_promoted_titles_for_keyword() 跳过已推广
"""
import contextlib
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "promoted.db"


@contextlib.contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_budgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            store_keyword TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            UNIQUE(budget_id, name),
            FOREIGN KEY(budget_id) REFERENCES shared_budgets(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_budget ON promotions(budget_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_promo_content ON promotions(content)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scrape_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraped_at TEXT NOT NULL,
            new_budgets INTEGER NOT NULL DEFAULT 0,
            new_promotions INTEGER NOT NULL DEFAULT 0,
            updated_promotions INTEGER NOT NULL DEFAULT 0,
            total_budgets INTEGER NOT NULL DEFAULT 0,
            total_promotions INTEGER NOT NULL DEFAULT 0
        )
    """)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


# ---------- CRUD ----------

def extract_keyword(budget_name: str, overrides: dict | None = None) -> str:
    if overrides:
        for substr, kw in overrides.items():
            if substr in budget_name:
                return kw
    if "共享" in budget_name:
        kw = budget_name.split("共享")[0].strip()
    elif "预算" in budget_name:
        kw = budget_name.split("预算")[0].strip()
    else:
        kw = budget_name.strip()
    kw = re.sub(r"\d+$", "", kw).strip()
    return kw


def upsert_budget(name: str, overrides: dict | None = None) -> tuple[int, bool]:
    keyword = extract_keyword(name, overrides)
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        row = c.execute(
            "SELECT id, store_keyword FROM shared_budgets WHERE name=?", (name,)
        ).fetchone()
        if row:
            bid, old_kw = row
            if not old_kw and keyword:
                c.execute("UPDATE shared_budgets SET store_keyword=?, last_seen_at=? WHERE id=?", (keyword, now, bid))
            else:
                c.execute("UPDATE shared_budgets SET last_seen_at=? WHERE id=?", (now, bid))
            return bid, False
        else:
            cur = c.execute(
                "INSERT INTO shared_budgets(name, store_keyword, first_seen_at, last_seen_at) VALUES(?,?,?,?)",
                (name, keyword, now, now),
            )
            return cur.lastrowid, True


def upsert_promotion(budget_id: int, name: str, content: str, status: str | None = None) -> tuple[bool, bool]:
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as c:
        row = c.execute("SELECT id, content, status FROM promotions WHERE budget_id=? AND name=?", (budget_id, name)).fetchone()
        if row:
            pid, old_content, old_status = row
            # 新的内容为空时不覆盖已有内容（页面可能尚未完全加载）
            new_content = content if content else old_content
            changed = (new_content != old_content) or (status and status != old_status)
            c.execute("UPDATE promotions SET last_seen_at=?, content=?, status=? WHERE id=?", (now, new_content, status or old_status, pid))
            return False, changed
        else:
            c.execute("INSERT INTO promotions(budget_id, name, content, status, first_seen_at, last_seen_at) VALUES(?,?,?,?,?,?)",
                      (budget_id, name, content or "", status, now, now))
            return True, False


def list_budgets() -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT b.id, b.name, b.store_keyword, b.first_seen_at, b.last_seen_at, COUNT(p.id) AS promo_count
            FROM shared_budgets b LEFT JOIN promotions p ON p.budget_id = b.id
            GROUP BY b.id ORDER BY b.id
        """).fetchall()
        return [{"id": r[0], "name": r[1], "store_keyword": r[2], "first_seen_at": r[3], "last_seen_at": r[4], "promo_count": r[5]} for r in rows]


def list_promotions_for_budget(budget_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id, name, content, status, first_seen_at, last_seen_at FROM promotions WHERE budget_id=? ORDER BY id", (budget_id,)).fetchall()
        return [{"id": r[0], "name": r[1], "content": r[2], "status": r[3], "first_seen_at": r[4], "last_seen_at": r[5]} for r in rows]


def list_promoted_titles_for_keyword(keyword: str) -> set[str]:
    with _conn() as c:
        budget_ids = [r[0] for r in c.execute("SELECT id FROM shared_budgets WHERE store_keyword=? OR store_keyword LIKE ?", (keyword, f"%{keyword}%")).fetchall()]
        if not budget_ids:
            return set()
        placeholders = ",".join("?" * len(budget_ids))
        rows = c.execute(f"SELECT DISTINCT content FROM promotions WHERE budget_id IN ({placeholders})", budget_ids).fetchall()
        titles = set()
        for (content,) in rows:
            t = content[:80].strip() if content else ""
            if t:
                titles.add(t)
        return titles


def count_active_promotions(budget_id: int) -> int:
    """统计某预算组下状态为「推广中」的推广记录数（单共享预算上限判断）。"""
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM promotions WHERE budget_id=? AND status='推广中'",
            (budget_id,),
        ).fetchone()[0]


def list_promoted_contents_for_budget(budget_id: int) -> list[str]:
    """返回该预算组所有非空推广内容（含「推广中/待审核」等状态）。
    用于笔记去重验证：候选笔记标题若出现在历史推广内容中, 视为已推广。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT content FROM promotions WHERE budget_id=? AND content IS NOT NULL AND content != ''",
            (budget_id,),
        ).fetchall()
        return [r[0] for r in rows]


def find_budget_id_by_keyword(keyword: str) -> int | None:
    """按预算组关键词(模糊)查找 budget_id, 用于单门店上限判断。"""
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM shared_budgets WHERE name LIKE ? LIMIT 1",
            (f"%{keyword}%",),
        ).fetchone()
        return row[0] if row else None


def record_session(new_budgets: int, new_promotions: int, updated_promotions: int, total_budgets: int, total_promotions: int):
    with _conn() as c:
        c.execute("INSERT INTO scrape_sessions(scraped_at, new_budgets, new_promotions, updated_promotions, total_budgets, total_promotions) VALUES(?,?,?,?,?,?)",
                  (datetime.now().isoformat(timespec="seconds"), new_budgets, new_promotions, updated_promotions, total_budgets, total_promotions))


def latest_session() -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT scraped_at, new_budgets, new_promotions, updated_promotions, total_budgets, total_promotions FROM scrape_sessions ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"scraped_at": row[0], "new_budgets": row[1], "new_promotions": row[2], "updated_promotions": row[3], "total_budgets": row[4], "total_promotions": row[5]}


# ---------- 页面爬取（含翻页）----------

def _scrape_one_page(edit_frame) -> list[dict]:
    """爬取当前页的预算和推广数据（点「更多」展开所有行后采集）。"""
    return edit_frame.evaluate(r"""
        () => {
            const out = [];
            const allRows = document.querySelectorAll('tr.merchant-table__row');
            for (const row of allRows) {
                if (row.className.includes('expanded-row')) continue;
                const cells = row.children;
                if (cells.length < 3) continue;
                const name = (cells[0].innerText || '').trim().split('\n')[0].trim();
                if (!name || name.length < 2) continue;
                if (name.includes('共') && name.includes('个推广')) continue;
                if (name === '共享预算名称') continue;
                // 跳过「新增推广/暂停/数据/删除」操作行
                if (name === '新增推广') continue;
                const status = (cells[1].innerText || '').trim().split('\n')[0].trim();

                // 如果该行有「更多」按钮, 点开再采集
                const collapseBtn = row.querySelector('.launch-item-collapse-btn');
                if (collapseBtn && collapseBtn.textContent.trim() === '更多') {
                    collapseBtn.click();
                }

                const cell2 = cells[2];
                const items = cell2.querySelectorAll('.launch-name-item');
                const promotions = [];
                for (const it of items) {
                    const st = (it.querySelector('.launch-status-tag')?.innerText || '').trim();
                    const nm = (it.querySelector('.launch-name')?.innerText || '').trim();
                    const sn = (it.querySelector('.shop-name')?.innerText || '').trim();
                    if (nm && nm.length >= 3) {
                        promotions.push({ status: st, name: nm, content: sn });
                    }
                }
                out.push({ name, status, promotions, wasExpanded: !!(collapseBtn && collapseBtn.textContent.trim() === '更多') });
            }
            return out;
        }
    """)


def _scrape_one_page_after_expand(edit_frame, prev_data: list[dict]) -> list[dict]:
    """采集上一次「更多」展开后的所有 .launch-name-item。
    由于「更多」点击是同步的 (数据立即出现在 DOM), 但需要等一帧渲染。"""
    import time as _time
    _time.sleep(0.5)
    # 重新抓所有 launch-name-item (含已展开的)
    return edit_frame.evaluate(r"""
        () => {
            const out = [];
            const allRows = document.querySelectorAll('tr.merchant-table__row');
            for (const row of allRows) {
                if (row.className.includes('expanded-row')) continue;
                const cells = row.children;
                if (cells.length < 3) continue;
                const name = (cells[0].innerText || '').trim().split('\n')[0].trim();
                if (!name || name.length < 2) continue;
                if (name.includes('共') && name.includes('个推广')) continue;
                if (name === '共享预算名称') continue;
                if (name === '新增推广') continue;
                const status = (cells[1].innerText || '').trim().split('\n')[0].trim();
                const cell2 = cells[2];
                const items = cell2.querySelectorAll('.launch-name-item');
                const promotions = [];
                for (const it of items) {
                    const st = (it.querySelector('.launch-status-tag')?.innerText || '').trim();
                    const nm = (it.querySelector('.launch-name')?.innerText || '').trim();
                    const sn = (it.querySelector('.shop-name')?.innerText || '').trim();
                    if (nm && nm.length >= 3) {
                        promotions.push({ status: st, name: nm, content: sn });
                    }
                }
                out.push({ name, status, promotions });
            }
            return out;
        }
    """)


def _expand_all_rows(edit_frame) -> int:
    """点开当前页所有未展开的预算行（按钮文字为「更多」），返回点开数量。"""
    # 找所有「更多」按钮
    more_btns = edit_frame.locator("span.launch-item-collapse-btn")
    count = more_btns.count()
    clicked = 0
    for i in range(count):
        try:
            btn = more_btns.nth(i)
            if btn.is_visible() and btn.text_content().strip() == "更多":
                btn.click()
                clicked += 1
        except Exception:
            continue
    if clicked > 0:
        time.sleep(1.5)  # 等 React 重新渲染
    return clicked


def scrape_and_upsert(edit_frame) -> dict:
    """从共享预算列表 frame 爬取所有页面的预算和推广，upsert 入库。
    每行预算都有「更多▾」折叠: 必须先点开, 再采集所有 .launch-name-item。"""
    time.sleep(0.5)

    new_budgets = 0
    new_promotions = 0
    updated_promotions = 0
    page_num = 1

    while True:
        # 1) 点开所有「更多」(必须在 Python 侧用 Playwright .click(), JS .click() 不触发 React 渲染)
        expanded = _expand_all_rows(edit_frame)
        if expanded > 0:
            print(f"  [爬取] 第 {page_num} 页: 已展开 {expanded} 行")
        # 2) 等 0.5s 渲染后采集全部 launch-name-item
        rows_data = _scrape_one_page_after_expand(edit_frame, None)
        count = len(rows_data)
        for row in rows_data:
            budget_id, is_new = upsert_budget(row["name"])
            if is_new:
                new_budgets += 1
            for p in row["promotions"]:
                if not p["name"] or "推广" not in p["name"]:
                    continue
                is_new_p, is_updated_p = upsert_promotion(budget_id, p["name"], p["content"], p["status"])
                if is_new_p:
                    new_promotions += 1
                elif is_updated_p:
                    updated_promotions += 1
        print(f"  [爬取] 第 {page_num} 页: {count} 个预算")

        # 检查下一页
        try:
            next_btn = edit_frame.locator("li.merchant-pagination__next:not(.merchant-pagination--disabled)").first
            if next_btn.count() > 0 and next_btn.is_visible():
                next_btn.click()
                time.sleep(1.5)
                page_num += 1
                continue
        except Exception:
            pass
        break

    # 爬完后回到第 1 页（后续流程需要在这页找门店预算行）
    if page_num > 1:
        p1_selectors = [
            "li.merchant-pagination__item:text-is(\"1\")",
            "li.merchant-pagination__item:has-text(\"1\")",
            ".merchant-pagination [data-page='1']",
            "li.merchant-pagination__item:first-child",
        ]
        returned = False
        for sel in p1_selectors:
            try:
                btn = edit_frame.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    time.sleep(1.5)
                    returned = True
                    print(f"  [爬取] 已回到第 1 页 (选择器: {sel})")
                    break
            except Exception:
                continue
        if not returned:
            print("  [WARN] 爬取后未能回到第 1 页, 后续 hover 预算行可能失败")

    with _conn() as c:
        total_budgets = c.execute("SELECT COUNT(*) FROM shared_budgets").fetchone()[0]
        total_promotions_db = c.execute("SELECT COUNT(*) FROM promotions").fetchone()[0]

    record_session(new_budgets, new_promotions, updated_promotions, total_budgets, total_promotions_db)

    return {
        "new_budgets": new_budgets,
        "new_promotions": new_promotions,
        "updated_promotions": updated_promotions,
        "total_budgets": total_budgets,
        "total_promotions": total_promotions_db,
    }