"""笔记数据模型、SQLite 查重库、纯规则选择引擎（浏览量最高优先）。"""
import contextlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "promoted.db"


@dataclass
class Note:
    note_id: str          # 页面唯一标识；探测前可能为空, 退化用 title+store
    title: str
    store: str
    views: int
    publish_date: str


# ---------- 查重库 ----------

@contextlib.contextmanager
def _conn():
    """获取 SQLite 连接，退出时自动关闭。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS promoted_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id TEXT,
            title TEXT NOT NULL,
            store TEXT NOT NULL,
            view_count INTEGER,
            promoted_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'auto'
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_title_store
        ON promoted_notes(title, store)
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


def _is_real_note_id(note_id) -> bool:
    """只有真实的 data-id 才用于查重; card-N 是抽屉位置序号(无唯一性, 跨门店会冲突)。"""
    return bool(note_id) and not str(note_id).startswith("card-")


def is_promoted(note: Note) -> bool:
    with _conn() as c:
        if _is_real_note_id(note.note_id):
            row = c.execute(
                "SELECT 1 FROM promoted_notes WHERE note_id=? LIMIT 1", (note.note_id,)
            ).fetchone()
            if row:
                return True
        row = c.execute(
            "SELECT 1 FROM promoted_notes WHERE title=? AND store=? LIMIT 1",
            (note.title, note.store),
        ).fetchone()
        return row is not None


def mark_promoted(note: Note, source: str = "auto"):
    with _conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO promoted_notes
               (note_id, title, store, view_count, promoted_at, source)
               VALUES (?,?,?,?,?,?)""",
            (note.note_id if _is_real_note_id(note.note_id) else None,
             note.title, note.store, note.views,
             datetime.now().isoformat(timespec="seconds"), source),
        )


def mark_unpromoted(note: Note):
    """回滚：从查重库中移除笔记记录（用于提交失败回退）。"""
    with _conn() as c:
        if note.note_id:
            c.execute("DELETE FROM promoted_notes WHERE note_id=?", (note.note_id,))
        c.execute("DELETE FROM promoted_notes WHERE title=? AND store=?",
                  (note.title, note.store))


def list_promoted(store: str | None = None) -> list[tuple]:
    with _conn() as c:
        if store:
            return c.execute(
                "SELECT title, store, promoted_at, source FROM promoted_notes WHERE store=?",
                (store,),
            ).fetchall()
        return c.execute(
            "SELECT title, store, promoted_at, source FROM promoted_notes"
        ).fetchall()


# ---------- 门店"已推完"状态（跳过已执行门店, 不重新打开页面） ----------
# 仅当「确定性推完」才标记: views_below_min(最高浏览量<门槛) 或 no_unpromoted_notes(全部已推)。
# 瞬态失败(note_card_not_rendered / drawer_not_open / no_notes_in_drawer / 导航失败)不标记,
# 否则会漏推(下次仍需重试该店)。

def mark_store_done(store: str, reason: str):
    """记录门店已推完, 下次 run 直接跳过该店(0 秒, 不打开页面)。"""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS store_done (
                store TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                done_at TEXT NOT NULL
            )
        """)
        c.execute(
            "INSERT OR REPLACE INTO store_done (store, reason, done_at) VALUES (?,?,?)",
            (store, reason, datetime.now().isoformat(timespec="seconds")),
        )


def is_store_done(store: str) -> bool:
    """门店是否已标记推完(确定性完成)。返回 True 则 run() 跳过, 不驱动浏览器。"""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS store_done (
                store TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                done_at TEXT NOT NULL
            )
        """)
        return c.execute("SELECT 1 FROM store_done WHERE store=?", (store,)).fetchone() is not None


def list_store_done() -> list[tuple]:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS store_done (
                store TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                done_at TEXT NOT NULL
            )
        """)
        return c.execute("SELECT store, reason, done_at FROM store_done ORDER BY store").fetchall()


def clear_store_done(store: str | None = None) -> int:
    """清除门店 done 标记(用户新增笔记后强制重扫)。store=None 清除全部, 返回清除条数。"""
    with _conn() as c:
        if store:
            cur = c.execute("DELETE FROM store_done WHERE store=?", (store,))
        else:
            cur = c.execute("DELETE FROM store_done")
        return cur.rowcount


# ---------- 工具 ----------

def parse_views(text: str) -> int:
    """将页面浏览量文本转为整数, 支持 '1.2万' / '3,456' / '789' 等格式。"""
    text = text.strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*万", text)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else 0


# ---------- 选择引擎（纯规则, 无 LLM） ----------

def select_best_note(candidates: list[Note], count: int = 1) -> tuple[list[Note], list[Note]]:
    """过滤已推广 -> 浏览量降序 -> 取前 count 篇。
    返回 (选中列表, 被过滤的已推广列表)。"""
    fresh, skipped = [], []
    for n in candidates:
        (skipped if is_promoted(n) else fresh).append(n)
    fresh.sort(key=lambda n: n.views, reverse=True)
    return fresh[:count], skipped
