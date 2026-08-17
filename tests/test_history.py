from __future__ import annotations

import threading

import pytest

from skydict.history import History
from skydict.pipeline import DictationResult
from skydict.stt.base import TranscriptResult


def make_result(text: str, backend: str = "local", duration: float = 1.5) -> DictationResult:
    return DictationResult(
        text=text,
        transcript=TranscriptResult(
            text=text, backend=backend, model="gigaam-v3-e2e-rnnt", language="ru"
        ),
        audio_duration=duration,
    )


@pytest.fixture
def history(tmp_path) -> History:
    return History(tmp_path / "history.db")


def test_added_entry_comes_back(history):
    history.add(make_result("привет мир"))

    entries = history.recent()

    assert len(entries) == 1
    assert entries[0].text == "привет мир"
    assert entries[0].backend == "local"
    assert entries[0].model == "gigaam-v3-e2e-rnnt"
    assert entries[0].duration == 1.5


def test_recent_returns_newest_first(history):
    for text in ("первый", "второй", "третий"):
        history.add(make_result(text))

    assert [e.text for e in history.recent()] == ["третий", "второй", "первый"]


def test_recent_respects_the_count(history):
    for index in range(10):
        history.add(make_result(f"запись {index}"))

    assert len(history.recent(3)) == 3


def test_empty_text_is_not_stored(history):
    assert history.add(make_result("")) is None
    assert history.add(make_result("   ")) is None
    assert history.count() == 0


def test_search_matches_substrings(history):
    history.add(make_result("совещание в четверг"))
    history.add(make_result("купить молоко"))

    found = history.search("молоко")

    assert [e.text for e in found] == ["купить молоко"]


def test_get_and_delete(history):
    entry_id = history.add(make_result("удалить меня"))

    assert history.get(entry_id).text == "удалить меня"
    assert history.delete(entry_id)
    assert history.get(entry_id) is None
    assert not history.delete(entry_id)


def test_clear_removes_everything(history):
    history.add(make_result("раз"))
    history.add(make_result("два"))

    history.clear()

    assert history.count() == 0


def test_pruning_keeps_only_the_limit(tmp_path):
    history = History(tmp_path / "history.db", limit=3)

    for index in range(6):
        history.add(make_result(f"запись {index}"))

    assert history.count() == 3
    assert [e.text for e in history.recent()] == ["запись 5", "запись 4", "запись 3"]


def test_pruning_can_be_disabled(tmp_path):
    history = History(tmp_path / "history.db", limit=None)

    for index in range(20):
        history.add(make_result(f"запись {index}"))

    assert history.count() == 20


def test_preview_collapses_whitespace_and_truncates(history):
    history.add(make_result("строка\nс   переносами"))
    history.add(make_result("я" * 100))

    entries = history.recent()

    assert entries[1].preview == "строка с переносами"
    assert len(entries[0].preview) == 58
    assert entries[0].preview.endswith("…")


def test_database_is_created_on_demand(tmp_path):
    path = tmp_path / "nested" / "dir" / "history.db"

    History(path).add(make_result("создалась"))

    assert path.exists()


def test_writes_from_several_threads_all_land(history):
    """Dictations arrive on worker threads; none may be lost to a locked database."""

    def write(index: int) -> None:
        history.add(make_result(f"поток {index}"))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert history.count() == 10


def test_reopening_keeps_the_data(tmp_path):
    path = tmp_path / "history.db"
    History(path).add(make_result("переживёт перезапуск"))

    assert History(path).recent()[0].text == "переживёт перезапуск"
