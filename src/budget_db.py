"""共享预算/推广数据库：增量爬取 + 关键词匹配 + 笔记去重。

表结构（与 notes.py 共用同一个 promoted.db）：
- shared_budgets: 共享预算名称（用于和门店做关键词匹配）
- promotions: 推广名称 + 推广内容（用于笔记去重）

DB 访问已由 data-tier-guard 的 SafeDB 包裹（src/safe_db.py，drop-in 资产）：
- 默认只读；写操作走 db.writer(reason) 并留审计；危险 SQL 一律拦截。
- shared_budgets 为级联根表(cascade_risk)，其写入保留写前快照；
  其余高频写( promotions / scrape_sessions )关闭快照以控制磁盘与耗时。

使用：
- 进入共享预算列表页 -> 调 scrape_and_upsert(b) 增量入库
- 选门店 -> budget_name 转 store_keyword
- 选笔记 -> list_promoted_titles_for_keyword() 跳过已推广
"""
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "promoted.db"
CLASSIFICATION = ROOT / "data-classification.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_db import SafeDB  # noqa: E402

# 建表 DDL：只读 SafeDB 连接不能执行 DDL，故在首次访问前用独立连接 bootstrap。
_DDL = """
CREATE TABLE IF NOT EXISTS shared_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    store_keyword TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
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
);
CREATE INDEX IF NOT EXISTS idx_promo_budget ON promotions(budget_id);
CREATE INDEX IF NOT EXISTS idx_promo_content ON promotions(content);
CREATE TABLE IF NOT EXISTS scrape_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scraped_at TEXT NOT NULL,
    new_budgets INTEGER NOT NULL DEFAULT 0,
    new_promotions INTEGER NOT NULL DEFAULT 0,
    updated_promotions INTEGER NOT NULL DEFAULT 0,
    total_budgets INTEGER NOT NULL DEFAULT 0,
    total_promotions INTEGER NOT NULL DEFAULT 0
);
"""

_db = None


def _init_schema():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.executescript(_DDL)
    finally:
        con.close()


def _safe_db():
    global _db
    if _db is None:
        _init_schema()
        cls = str(CLASSIFICATION) if CLASSIFICATION.is_file() else None
        _db = SafeDB(str(DB_PATH), classification=cls, actor="ai")
    return _db


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
    db = _safe_db()
    row = db.query("SELECT id, store_keyword FROM shared_budgets WHERE name=?", (name,))
    # shared_budgets 是级联根表：保留写前快照。
    with db.writer("增量 upsert 共享预算", snapshot=True) as w:
        if row:
            bid, old_kw = row[0]["id"], row[0]["store_keyword"]
            if not old_kw and keyword:
                w.update("shared_budgets",
                         {"store_keyword": keyword, "last_seen_at": now},
                         "id=?", (bid,))
            else:
                w.update("shared_budgets", {"last_seen_at": now}, "id=?", (bid,))
            return bid, False
        else:
            rid = w.insert("shared_budgets", {
                "name": name, "store_keyword": keyword,
                "first_seen_at": now, "last_seen_at": now,
            })
            return rid, True


def upsert_promotion(budget_id: int, name: str, content: str, status: str | None = None) -> tuple[bool, bool]:
    now = datetime.now().isoformat(timespec="seconds")
    db = _safe_db()
    row = db.query(
        "SELECT id, content, status FROM promotions WHERE budget_id=? AND name=?",
        (budget_id, name),
    )
    with db.writer("增量 upsert 推广记录", snapshot=False) as w:
        if row:
            pid, old_content, old_status = row[0]["id"], row[0]["content"], row[0]["status"]
            # 新的内容为空时不覆盖已有内容（页面可能尚未完全加载）
            new_content = content if content else old_content
            changed = (new_content != old_content) or (status and status != old_status)
            w.update("promotions",
                     {"last_seen_at": now, "content": new_content, "status": status or old_status},
                     "id=?", (pid,))
            return False, changed
        else:
            w.insert("promotions", {
                "budget_id": budget_id, "name": name, "content": content or "",
                "status": status, "first_seen_at": now, "last_seen_at": now,
            })
            return True, False


def list_budgets() -> list[dict]:
    db = _safe_db()
    return db.query("""
        SELECT b.id, b.name, b.store_keyword, b.first_seen_at, b.last_seen_at,
               COUNT(p.id) AS promo_count
        FROM shared_budgets b LEFT JOIN promotions p ON p.budget_id = b.id
        GROUP BY b.id ORDER BY b.id
    """)


def list_promotions_for_budget(budget_id: int) -> list[dict]:
    db = _safe_db()
    return db.query(
        "SELECT id, name, content, status, first_seen_at, last_seen_at "
        "FROM promotions WHERE budget_id=? ORDER BY id",
        (budget_id,),
    )


def list_promoted_titles_for_keyword(keyword: str) -> set[str]:
    db = _safe_db()
    budget_ids = [r["id"] for r in db.query(
        "SELECT id FROM shared_budgets WHERE store_keyword=? OR store_keyword LIKE ?",
        (keyword, f"%{keyword}%"),
    )]
    if not budget_ids:
        return set()
    placeholders = ",".join("?" * len(budget_ids))
    rows = db.query(
        f"SELECT DISTINCT content FROM promotions WHERE budget_id IN ({placeholders})",
        budget_ids,
    )
    titles = set()
    for r in rows:
        t = (r["content"] or "")[:80].strip()
        if t:
            titles.add(t)
    return titles


def count_active_promotions(budget_id: int) -> int:
    """统计某预算组下状态为「推广中」的推广记录数（单共享预算上限判断）。"""
    db = _safe_db()
    rows = db.query(
        "SELECT COUNT(*) AS n FROM promotions WHERE budget_id=? AND status='推广中'",
        (budget_id,),
    )
    return rows[0]["n"] if rows else 0


def list_promoted_contents_for_budget(budget_id: int) -> list[str]:
    """返回该预算组所有非空推广内容（含「推广中/待审核」等状态）。
    用于笔记去重验证：候选笔记标题若出现在历史推广内容中, 视为已推广。"""
    db = _safe_db()
    rows = db.query(
        "SELECT content FROM promotions WHERE budget_id=? AND content IS NOT NULL AND content != ''",
        (budget_id,),
    )
    return [r["content"] for r in rows]


def find_budget_id_by_keyword(keyword: str) -> int | None:
    """按预算组关键词(模糊)查找 budget_id, 用于单门店上限判断。"""
    db = _safe_db()
    rows = db.query("SELECT id FROM shared_budgets WHERE name LIKE ? LIMIT 1",
                    (f"%{keyword}%",))
    return rows[0]["id"] if rows else None


def record_session(new_budgets: int, new_promotions: int, updated_promotions: int, total_budgets: int, total_promotions: int):
    db = _safe_db()
    with db.writer("记录爬取会话统计", snapshot=False) as w:
        w.insert("scrape_sessions", {
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "new_budgets": new_budgets, "new_promotions": new_promotions,
            "updated_promotions": updated_promotions,
            "total_budgets": total_budgets, "total_promotions": total_promotions,
        })


def latest_session() -> dict | None:
    db = _safe_db()
    rows = db.query(
        "SELECT scraped_at, new_budgets, new_promotions, updated_promotions, "
        "total_budgets, total_promotions FROM scrape_sessions ORDER BY id DESC LIMIT 1"
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "scraped_at": r["scraped_at"], "new_budgets": r["new_budgets"],
        "new_promotions": r["new_promotions"], "updated_promotions": r["updated_promotions"],
        "total_budgets": r["total_budgets"], "total_promotions": r["total_promotions"],
    }


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
    time.sleep(0.5)
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

    db = _safe_db()
    total_budgets = db.query("SELECT COUNT(*) AS n FROM shared_budgets")[0]["n"]
    total_promotions_db = db.query("SELECT COUNT(*) AS n FROM promotions")[0]["n"]

    record_session(new_budgets, new_promotions, updated_promotions, total_budgets, total_promotions_db)

    return {
        "new_budgets": new_budgets,
        "new_promotions": new_promotions,
        "updated_promotions": updated_promotions,
        "total_budgets": total_budgets,
        "total_promotions": total_promotions_db,
    }
