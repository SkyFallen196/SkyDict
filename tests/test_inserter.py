from __future__ import annotations

import pytest

from skydict.macos.permissions import PermissionError_
from skydict.output.inserter import ClipboardInserter, TextInserter, build_inserter


class FakeClipboard:
    def __init__(self, initial: str | None = None) -> None:
        self.text = initial
        self.snapshots = 0
        self.restores: list[list] = []
        self.writes: list[str] = []

    def write_text(self, text: str) -> None:
        self.text = text
        self.writes.append(text)

    def read_text(self) -> str | None:
        return self.text

    def snapshot(self) -> list:
        self.snapshots += 1
        return [{"public.utf8-plain-text": (self.text or "").encode()}]

    def restore(self, snapshot: list) -> None:
        self.restores.append(snapshot)
        payload = snapshot[0].get("public.utf8-plain-text") if snapshot else None
        self.text = payload.decode() if payload else None


@pytest.fixture
def can_post(monkeypatch):
    monkeypatch.setattr("skydict.output.inserter.check_post_access", lambda: True)


@pytest.fixture
def cannot_post(monkeypatch):
    monkeypatch.setattr("skydict.output.inserter.check_post_access", lambda: False)


@pytest.fixture
def pressed_keys(monkeypatch):
    keys: list[str] = []
    monkeypatch.setattr(TextInserter, "_press_paste", lambda self: keys.append("cmd+v"))
    return keys


def test_paste_writes_text_then_restores_clipboard(can_post, pressed_keys):
    clipboard = FakeClipboard("user's own clipboard")
    inserter = TextInserter(clipboard, settle=0)

    inserter.deliver("продиктованный текст")

    assert clipboard.writes == ["продиктованный текст"]
    assert pressed_keys == ["cmd+v"]
    assert clipboard.text == "user's own clipboard"


def test_clipboard_is_restored_even_if_pasting_fails(can_post, monkeypatch):
    clipboard = FakeClipboard("original")

    def explode(self):
        raise RuntimeError("CGEventPost failed")

    monkeypatch.setattr(TextInserter, "_press_paste", explode)

    with pytest.raises(RuntimeError):
        TextInserter(clipboard, settle=0).deliver("text")

    assert clipboard.text == "original"


def test_restore_can_be_disabled(can_post, pressed_keys):
    clipboard = FakeClipboard("original")

    TextInserter(clipboard, restore_clipboard=False, settle=0).deliver("dictated")

    assert clipboard.text == "dictated"
    assert clipboard.snapshots == 0


def test_empty_text_does_nothing(can_post, pressed_keys):
    clipboard = FakeClipboard("original")

    TextInserter(clipboard, settle=0).deliver("")

    assert clipboard.writes == []
    assert pressed_keys == []


def test_paste_without_permission_raises(cannot_post):
    clipboard = FakeClipboard()

    with pytest.raises(PermissionError_, match="clipboard_only"):
        TextInserter(clipboard, settle=0).deliver("text")

    assert clipboard.writes == []


def test_clipboard_inserter_leaves_text_and_presses_nothing():
    clipboard = FakeClipboard("original")

    ClipboardInserter(clipboard).deliver("dictated")

    assert clipboard.text == "dictated"


def test_build_inserter_falls_back_without_permission(cannot_post):
    inserter = build_inserter("paste")

    assert isinstance(inserter, ClipboardInserter)
    assert inserter.mode == "clipboard_only"


def test_build_inserter_can_refuse_to_fall_back(cannot_post):
    assert isinstance(build_inserter("paste", fallback=False), TextInserter)


def test_build_inserter_uses_paste_when_permitted(can_post):
    assert isinstance(build_inserter("paste"), TextInserter)


def test_build_inserter_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unknown insert mode"):
        build_inserter("telepathy")
