"""notes.py 纯逻辑单元测试：parse_views、select_best_note、数据库操作。"""
import sys
import tempfile
from pathlib import Path

import pytest

# 将 src 加入路径，并将 DB_PATH 指向临时文件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import notes
from notes import Note, parse_views, select_best_note, is_promoted, mark_promoted, mark_unpromoted


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """每个测试使用独立的临时数据库。"""
    tmp = Path(tempfile.mkdtemp()) / "test_promoted.db"
    monkeypatch.setattr(notes, "DB_PATH", tmp)
    yield tmp
    # 清理
    if tmp.exists():
        tmp.unlink()
    tmp.parent.rmdir()


# ---------- parse_views ----------

class TestParseViews:
    def test_plain_number(self):
        assert parse_views("12345") == 12345

    def test_with_comma(self):
        assert parse_views("1,234") == 1234

    def test_wan_chinese(self):
        assert parse_views("1.2万") == 12000

    def test_wan_with_more_decimals(self):
        assert parse_views("3.56万") == 35600

    def test_wan_integer(self):
        assert parse_views("10万") == 100000

    def test_zero_wan(self):
        assert parse_views("0.5万") == 5000

    def test_empty_returns_zero(self):
        assert parse_views("") == 0

    def test_no_digits_returns_zero(self):
        assert parse_views("abc") == 0

    def test_spaces_around(self):
        assert parse_views("  1.2万 ") == 12000


# ---------- select_best_note ----------

class TestSelectBestNote:
    def test_selects_highest_views(self):
        candidates = [
            Note("1", "low", "s1", 100, "2024-01-01"),
            Note("2", "high", "s1", 500, "2024-01-02"),
            Note("3", "mid", "s1", 300, "2024-01-03"),
        ]
        chosen, skipped = select_best_note(candidates, count=1)
        assert len(chosen) == 1
        assert chosen[0].title == "high"
        assert chosen[0].views == 500

    def test_selects_top_n(self):
        candidates = [
            Note("1", "a", "s1", 100, ""),
            Note("2", "b", "s1", 500, ""),
            Note("3", "c", "s1", 300, ""),
        ]
        chosen, skipped = select_best_note(candidates, count=2)
        assert len(chosen) == 2
        assert chosen[0].views == 500
        assert chosen[1].views == 300

    def test_filters_promoted(self):
        promoted_note = Note("99", "already", "s1", 999, "2024-01-01")
        mark_promoted(promoted_note, source="auto")
        candidates = [promoted_note, Note("100", "fresh", "s1", 10, "2024-01-02")]
        chosen, skipped = select_best_note(candidates)
        assert len(chosen) == 1
        assert chosen[0].title == "fresh"
        assert len(skipped) == 1
        assert skipped[0].title == "already"

    def test_all_promoted_returns_empty(self):
        n = Note("1", "only", "s1", 100, "")
        mark_promoted(n, source="auto")
        chosen, skipped = select_best_note([n])
        assert len(chosen) == 0
        assert len(skipped) == 1


# ---------- 数据库操作 ----------

class TestDB:
    def test_mark_and_check(self):
        n = Note("id1", "test title", "store1", 100, "2024-01-01")
        assert not is_promoted(n)
        mark_promoted(n, source="auto")
        assert is_promoted(n)

    def test_unmark(self):
        n = Note("id2", "remove me", "store2", 50, "2024-01-01")
        mark_promoted(n, source="auto")
        assert is_promoted(n)
        mark_unpromoted(n)
        assert not is_promoted(n)

    def test_dedup_by_title_store(self):
        n1 = Note("id_a", "same title", "same store", 100, "")
        n2 = Note("id_b", "same title", "same store", 200, "")  # 同一标题+门店
        mark_promoted(n1, source="auto")
        assert is_promoted(n2)  # 不同 note_id 但 title+store 相同

    def test_list_promoted_filtered(self):
        mark_promoted(Note("a", "t1", "store_a", 10, ""), source="auto")
        mark_promoted(Note("b", "t2", "store_b", 20, ""), source="auto")
        result = notes.list_promoted(store="store_a")
        assert len(result) == 1
        assert result[0][0] == "t1"

    def test_ignore_duplicate_mark(self):
        n = Note("dup", "dup title", "dup store", 100, "")
        mark_promoted(n, source="auto")
        mark_promoted(n, source="auto")  # 不应抛异常
        assert is_promoted(n)
