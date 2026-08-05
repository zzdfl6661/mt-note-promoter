"""笔记数据模型、SQLite 查重库、纯规则选择引擎（浏览量最高优先）。

DB 访问已由 data-tier-guard 的 SafeDB 包裹（src/safe_db.py，drop-in 资产）：
- 默认只读（mode=ro），写操作必须走 db.writer(reason) 并留审计；
- 危险 SQL（DROP / 无 WHERE 的 DELETE·UPDATE / 表名注入 / ATTACH / 写 schema PRAGMA）一律拦截。
外部调用方无感：本模块公开函数签名与返回形态与改造前完全一致。
"""
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "promoted.db"
CLASSIFICATION = ROOT / "data-classification.yaml"

# SafeDB 随项目内置（data-tier-guard 的 drop-in 资产）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from safe_db import SafeDB  # noqa: E402

# 建表 DDL：只读连接不能建表，故在首次访问前用独立连接 bootstrap。
# 仅覆盖本模块负责的表；budget_db.py 负责 shared_budgets / promotions / scrape_sessions。
_DDL = """
CREATE TABLE IF NOT EXISTS promoted_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT,
    title TEXT NOT NULL,
    store TEXT NOT NULL,
    view_count INTEGER,
    promoted_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto'
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_title_store
    ON promoted_notes(title, store);
CREATE TABLE IF NOT EXISTS store_done (
    store TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    done_at TEXT NOT NULL
);
"""

_db = None


def _init_schema():
    """用独立（可写）连接建立表结构；只读 SafeDB 连接不能执行 DDL。"""
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


@dataclass
class Note:
    note_id: str          # 页面唯一标识；探测前可能为空, 退化用 title+store
    title: str
    store: str
    views: int
    publish_date: str


# ---------- 查重库 ----------

def _is_real_note_id(note_id) -> bool:
    """只有真实的 data-id 才用于查重; card-N 是抽屉位置序号(无唯一性, 跨门店会冲突)。"""
    return bool(note_id) and not str(note_id).startswith("card-")


def is_promoted(note: Note) -> bool:
    db = _safe_db()
    if _is_real_note_id(note.note_id):
        if db.query(
            "SELECT 1 FROM promoted_notes WHERE note_id=? LIMIT 1", (note.note_id,)
        ):
            return True
    return bool(db.query(
        "SELECT 1 FROM promoted_notes WHERE title=? AND store=? LIMIT 1",
        (note.title, note.store),
    ))


def mark_promoted(note: Note, source: str = "auto"):
    """标记笔记已推广（幂等）。"""
    db = _safe_db()
    with db.writer("标记笔记已推广", snapshot=False) as w:
        w.insert(
            "promoted_notes",
            {
                "note_id": note.note_id if _is_real_note_id(note.note_id) else None,
                "title": note.title,
                "store": note.store,
                "view_count": note.views,
                "promoted_at": datetime.now().isoformat(timespec="seconds"),
                "source": source,
            },
            or_ignore=True,
        )


def mark_unpromoted(note: Note):
    """回滚：从查重库中移除笔记记录（用于提交失败回退）。"""
    db = _safe_db()
    with db.writer("回滚：标记笔记未推广", snapshot=False) as w:
        if note.note_id:
            w.delete("promoted_notes", "note_id=?", (note.note_id,))
        w.delete("promoted_notes", "title=? AND store=?", (note.title, note.store))


def list_promoted(store: str | None = None) -> list[tuple]:
    db = _safe_db()
    if store:
        rows = db.query(
            "SELECT title, store, promoted_at, source FROM promoted_notes WHERE store=?",
            (store,),
        )
    else:
        rows = db.query("SELECT title, store, promoted_at, source FROM promoted_notes")
    return [tuple(r.values()) for r in rows]


# ---------- 门店"已推完"状态（跳过已执行门店, 不重新打开页面） ----------
# 仅当「确定性推完」才标记: views_below_min(最高浏览量<门槛) 或 no_unpromoted_notes(全部已推)。
# 瞬态失败(note_card_not_rendered / drawer_not_open / no_notes_in_drawer / 导航失败)不标记,
# 否则会漏推(下次仍需重试该店)。

def mark_store_done(store: str, reason: str):
    """记录门店已推完, 下次 run 直接跳过该店(0 秒, 不打开页面)。"""
    db = _safe_db()
    exists = db.query("SELECT 1 FROM store_done WHERE store=? LIMIT 1", (store,))
    with db.writer("标记门店已推完", snapshot=False) as w:
        if exists:
            w.update("store_done", {"reason": reason, "done_at": datetime.now().isoformat(timespec="seconds")},
                     "store=?", (store,))
        else:
            w.insert("store_done", {
                "store": store, "reason": reason,
                "done_at": datetime.now().isoformat(timespec="seconds"),
            })


def is_store_done(store: str) -> bool:
    """门店是否已标记推完(确定性完成)。返回 True 则 run() 跳过, 不驱动浏览器。"""
    db = _safe_db()
    return bool(db.query("SELECT 1 FROM store_done WHERE store=? LIMIT 1", (store,)))


def list_store_done() -> list[tuple]:
    db = _safe_db()
    rows = db.query("SELECT store, reason, done_at FROM store_done ORDER BY store")
    return [tuple(r.values()) for r in rows]


def clear_store_done(store: str | None = None) -> int:
    """清除门店 done 标记(用户新增笔记后强制重扫)。store=None 清除全部, 返回清除条数。

    全表清除走 WHERE 1=1：SafeDB 禁止无 WHERE 的 DELETE（防误清整表），
    此处用显式条件 + 强制 reason + 审计，属于有意的运维操作，仍受审计约束。
    """
    db = _safe_db()
    with db.writer("清除门店 done 标记（强制重扫）", snapshot=False) as w:
        if store:
            return w.delete("store_done", "store=?", (store,))
        return w.delete("store_done", "1=1")


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
