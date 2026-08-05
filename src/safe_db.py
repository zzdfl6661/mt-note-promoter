"""SQLite 安全访问层：默认只读，写操作需显式开启并留审计。

由 data-tier-guard skill 生成。零第三方依赖，可直接复制进任意项目。

    from safe_db import SafeDB, DangerousSQL

    db = SafeDB("data/promoted.db")                  # 默认只读
    rows = db.query("SELECT * FROM t WHERE x = ?", (1,))

    with db.writer("导入今日推广记录") as w:            # 写必须说明原因
        w.insert("promoted_notes", {"title": "x", "store": "y"})
        w.update("store_done", {"reason": "done"}, where="store = ?", args=("A",))

能力边界：只拦截走本层的操作。防不住 rm 删文件、别的客户端连库、
shell 批量操作。真正的兜底是备份。
"""

import os
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime

__all__ = ["SafeDB", "DangerousSQL", "GuardConfig"]


class DangerousSQL(Exception):
    """危险 SQL 被拦截。"""


# ---- 危险语句特征。正则 + 语句类型白名单双保险，不依赖 SQL 解析库 -------
DANGER_PATTERNS = [
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW|TRIGGER)\b", re.I),
     "DROP 语句"),
    (re.compile(r"\bTRUNCATE\b", re.I), "TRUNCATE 语句"),
    (re.compile(r"\bDELETE\s+FROM\s+[\"'`\[]?\w+[\"'`\]]?\s*(;|--|$)", re.I),
     "无 WHERE 的 DELETE（会清空整表）"),
    (re.compile(r"\bUPDATE\s+[\"'`\[]?\w+[\"'`\]]?\s+SET\b(?![\s\S]*\bWHERE\b)", re.I),
     "无 WHERE 的 UPDATE（会改写整表）"),
    (re.compile(r"\bATTACH\s+DATABASE\b", re.I), "ATTACH DATABASE"),
    (re.compile(r"\bPRAGMA\s+writable_schema\b", re.I), "写 schema 的 PRAGMA"),
    (re.compile(r"\bALTER\s+TABLE\s+\w+\s+DROP\b", re.I), "ALTER DROP COLUMN"),
    (re.compile(r"\bREPLACE\s+INTO\b(?![\s\S]*\bWHERE\b)", re.I),
     "无条件 REPLACE INTO"),
    (re.compile(r"\bVACUUM\s+INTO\b", re.I), "VACUUM INTO"),
]
WRITE_VERBS = ("insert", "update", "delete", "replace", "create", "alter", "drop")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_sql(sql):
    for pat, why in DANGER_PATTERNS:
        if pat.search(sql):
            raise DangerousSQL(f"拒绝执行：{why}\n  SQL: {sql.strip()[:160]}")


def _ident(name):
    """表名/列名白名单校验，杜绝拼接注入。"""
    if not _IDENT.match(str(name)):
        raise DangerousSQL(f"非法标识符: {name!r}")
    return f'"{name}"'


# ---- 分级配置 ----------------------------------------------------------

class GuardConfig:
    """读 data-classification.yaml，决定各表写保护强度。缺文件时用保守默认。"""

    def __init__(self, path=None):
        self.tables = {}
        self.loaded = False
        if path and os.path.isfile(path):
            self._parse(path)
            self.loaded = True

    def _parse(self, path):
        cur = None
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("#") or not line.strip():
                continue
            ind = len(line) - len(line.lstrip())
            s = line.strip()
            if ind == 2 and s.endswith(":"):
                cur = s[:-1]
                self.tables[cur] = {"tier": "L2", "cascade_risk": False,
                                    "append_only": False}
            elif cur and ind == 4 and ":" in s:
                k, v = (x.strip() for x in s.split(":", 1))
                if k in ("tier", "cascade_risk", "append_only"):
                    self.tables[cur][k] = (v == "true") if v in ("true", "false") else v

    def tier(self, table):
        return self.tables.get(table, {}).get("tier", "L2")  # 未知表按敏感处理

    def cascade_risk(self, table):
        return bool(self.tables.get(table, {}).get("cascade_risk"))

    def append_only(self, table):
        return bool(self.tables.get(table, {}).get("append_only"))


# ---- 主体 --------------------------------------------------------------

class SafeDB:
    def __init__(self, path, classification=None, snapshot_dir=None, actor="ai"):
        self.path = os.path.abspath(path)
        if not os.path.isfile(self.path):
            raise FileNotFoundError(self.path)
        base = os.path.dirname(self.path)
        self.cfg = GuardConfig(classification or os.path.join(
            os.path.dirname(base), "data-classification.yaml"))
        self.snapshot_dir = snapshot_dir or os.path.join(base, "_snapshots")
        self.actor = actor
        self._ro = None

    # -- 只读通道 --
    @property
    def ro(self):
        if self._ro is None:
            uri = f"file:{self.path.replace(os.sep, '/')}?mode=ro"
            # isolation_level=None：只读连接绝不开启隐式事务。
            # 否则一旦有人在只读连接上试写，失败的语句会留下未回滚的事务，
            # 长期占着锁，后续正常写全部 "database is locked"。
            self._ro = sqlite3.connect(uri, uri=True, isolation_level=None)
            self._ro.row_factory = sqlite3.Row
        return self._ro

    def query(self, sql, args=()):
        if sql.strip().split(None, 1)[0].lower() in WRITE_VERBS:
            raise DangerousSQL("query() 只能执行读操作，写请用 writer() 上下文")
        _check_sql(sql)
        return [dict(r) for r in self.ro.execute(sql, args)]

    def tables(self):
        return [r["name"] for r in self.ro.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%'")]

    # -- 快照 --
    def snapshot(self, tag="manual"):
        os.makedirs(self.snapshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(self.snapshot_dir,
                           f"{os.path.basename(self.path)}.{ts}.{tag}.bak")
        shutil.copy2(self.path, dst)
        return dst

    # -- 写通道 --
    @contextmanager
    def writer(self, reason, snapshot=None):
        """写必须显式开启并说明原因。原因会进审计表。"""
        if not reason or not str(reason).strip():
            raise DangerousSQL("writer() 必须提供非空的 reason")
        snap = None
        if snapshot is None:
            snapshot = any(self.cfg.cascade_risk(t) for t in self.cfg.tables) \
                if self.cfg.loaded else True
        if snapshot:
            snap = self.snapshot("prewrite")
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        _ensure_audit(con)
        w = _Writer(con, self.cfg, self.actor, reason)
        try:
            yield w
            con.commit()
        except Exception:
            con.rollback()
            if snap:
                print(f"[safe_db] 已回滚。写前快照: {snap}")
            raise
        finally:
            con.close()

    def audit_log(self, limit=20):
        try:
            return self.query(
                "select * from _audit order by id desc limit ?", (limit,))
        except sqlite3.Error:
            return []

    def close(self):
        if self._ro:
            self._ro.close()
            self._ro = None


def _ensure_audit(con):
    con.execute("""CREATE TABLE IF NOT EXISTS _audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
        op TEXT NOT NULL, target TEXT, rows_affected INTEGER, sql TEXT)""")


class _Writer:
    """只暴露白名单 DAO 方法。不提供裸 execute。"""

    def __init__(self, con, cfg, actor, reason):
        self._con = con
        self._cfg = cfg
        self._actor = actor
        self._reason = reason

    def _audit(self, op, target, n, sql):
        self._con.execute(
            "INSERT INTO _audit (ts, actor, reason, op, target, rows_affected, sql)"
            " VALUES (?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), self._actor,
             self._reason, op, target, n, sql[:500]))

    def insert(self, table, row, or_ignore=False):
        t = _ident(table)
        cols = [_ident(c) for c in row]
        sql = (f"INSERT{' OR IGNORE' if or_ignore else ''} INTO {t} "
               f"({', '.join(cols)}) VALUES ({', '.join('?' * len(row))})")
        _check_sql(sql)
        cur = self._con.execute(sql, tuple(row.values()))
        self._audit("insert", table, cur.rowcount, sql)
        return cur.lastrowid

    def update(self, table, values, where, args=()):
        if not where or not str(where).strip():
            raise DangerousSQL(f"update({table}) 缺少 WHERE，拒绝整表改写")
        tier = self._cfg.tier(table)
        if self._cfg.append_only(table):
            raise DangerousSQL(
                f"{table} 标记为 append_only（仅追加），不允许 UPDATE")
        t = _ident(table)
        sets = ", ".join(f"{_ident(k)} = ?" for k in values)
        sql = f"UPDATE {t} SET {sets} WHERE {where}"
        _check_sql(sql)
        cur = self._con.execute(sql, tuple(values.values()) + tuple(args))
        if tier in ("L2", "L3") and cur.rowcount > 500:
            self._con.rollback()
            raise DangerousSQL(
                f"{table}({tier}) 单次更新 {cur.rowcount} 行超过阈值 500，已回滚。"
                " 确需批量请分批或显式确认。")
        self._audit("update", table, cur.rowcount, sql)
        return cur.rowcount

    def delete(self, table, where, args=()):
        if not where or not str(where).strip():
            raise DangerousSQL(f"delete({table}) 缺少 WHERE，拒绝清空整表")
        if self._cfg.cascade_risk(table):
            refs = self._con.execute(
                "select name from sqlite_master where type='table' "
                "and sql like ?", (f"%REFERENCES {table}%",)).fetchall()
            if refs:
                print(f"[safe_db] 警告：{table} 被 "
                      f"{', '.join(r['name'] for r in refs)} 引用，删除可能破坏引用完整性")
        t = _ident(table)
        sql = f"DELETE FROM {t} WHERE {where}"
        _check_sql(sql)
        cur = self._con.execute(sql, tuple(args))
        tier = self._cfg.tier(table)
        if tier in ("L2", "L3") and cur.rowcount > 200:
            self._con.rollback()
            raise DangerousSQL(
                f"{table}({tier}) 单次删除 {cur.rowcount} 行超过阈值 200，已回滚。")
        self._audit("delete", table, cur.rowcount, sql)
        return cur.rowcount

    def execute_checked(self, sql, args=()):
        """兜底通道：仍过危险检查，但允许自定义语句。慎用。"""
        _check_sql(sql)
        verb = sql.strip().split(None, 1)[0].lower()
        if verb in ("drop", "alter", "attach", "vacuum"):
            raise DangerousSQL(f"{verb.upper()} 不允许通过本层执行")
        cur = self._con.execute(sql, args)
        self._audit("execute", "-", cur.rowcount, sql)
        return cur
