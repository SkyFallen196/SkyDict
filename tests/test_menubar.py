"""Menubar logic tested without an event loop.

The app is built so every AppKit call happens while draining a queue on the main thread;
these tests drive that drain directly with a stub for the rumps status item.
"""

from __future__ import annotations

import threading

import pytest

from skydict.config import Settings
from skydict.history import History
from skydict.pipeline import DictationResult, State
from skydict.secrets import MissingCredentialError
from skydict.stt.base import TranscriptResult
from skydict.ui.menubar import STATE_ICONS, SkyDictApp


class FakeApp:
    """Stands in for rumps.App — only the title is touched by the drain loop."""

    def __init__(self) -> None:
        self.title = ""


class FakeController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = FakeSession()
        self.rebuilt = 0
        self.rebuild_error: Exception | None = None
        self.stopped = False

    def rebuild_backend(self) -> None:
        if self.rebuild_error:
            raise self.rebuild_error
        self.rebuilt += 1

    def stop(self) -> None:
        self.stopped = True


class FakeSession:
    def __init__(self) -> None:
        self.listeners: list = []

    def add_listener(self, listener) -> None:
        self.listeners.append(listener)


@pytest.fixture
def app(tmp_path, monkeypatch) -> SkyDictApp:
    settings = Settings()
    monkeypatch.setattr(Settings, "save", lambda self, path=None: path)
    instance = SkyDictApp(
        settings,
        controller=FakeController(settings),
        history=History(tmp_path / "history.db"),
    )
    instance.app = FakeApp()
    instance.notifications: list[tuple[str, str]] = []
    monkeypatch.setattr(
        instance, "notify", lambda title, msg: instance.notifications.append((title, msg))
    )
    return instance


def make_result(text: str = "продиктовано") -> DictationResult:
    return DictationResult(
        text=text,
        transcript=TranscriptResult(text=text, backend="local", model="gigaam-v3-e2e-rnnt"),
        audio_duration=2.0,
    )


def test_state_events_update_the_icon(app):
    for state in (State.RECORDING, State.TRANSCRIBING, State.IDLE):
        app._on_state(state, None)
        app._drain()
        assert app.app.title == STATE_ICONS[state]


def test_icon_updates_only_while_draining(app):
    """Proves the worker thread never touches AppKit: nothing changes until the drain."""
    app._on_state(State.RECORDING, None)

    assert app.app.title == ""

    app._drain()

    assert app.app.title == STATE_ICONS[State.RECORDING]


def test_result_is_written_to_history(app):
    app._on_result(make_result("сохрани меня"))
    app._drain()

    assert [e.text for e in app.history.recent()] == ["сохрани меня"]


def test_error_shows_the_warning_icon_and_notifies(app):
    app._on_error(RuntimeError("сеть недоступна"))
    app._drain()

    assert app.app.title == STATE_ICONS[State.ERROR]
    assert app.notifications == [("Dictation failed", "сеть недоступна")]
    assert isinstance(app.last_error, RuntimeError)


def test_events_from_a_worker_thread_are_applied_on_drain(app):
    thread = threading.Thread(target=lambda: app._on_state(State.RECORDING, None))
    thread.start()
    thread.join(timeout=2)

    app._drain()

    assert app.app.title == STATE_ICONS[State.RECORDING]


def test_drain_survives_a_failing_handler(app, monkeypatch):
    monkeypatch.setattr(
        app, "set_icon", lambda state: (_ for _ in ()).throw(RuntimeError("AppKit hiccup"))
    )
    app._on_state(State.RECORDING, None)
    app._on_state(State.IDLE, None)

    app._drain()  # must not raise

    assert app._events.empty()


def test_drain_on_an_empty_queue_is_a_no_op(app):
    app._drain()

    assert app.app.title == ""


def test_switching_backend_rebuilds_and_persists(app):
    app.set_backend("local")

    assert app.settings.backend == "local"
    assert app.controller.rebuilt == 1


def test_switching_to_the_current_backend_does_nothing(app):
    app.set_backend("cloud")  # already the default

    assert app.controller.rebuilt == 0


def test_backend_switch_without_a_key_is_reported_not_raised(app):
    app.controller.rebuild_error = MissingCredentialError("groq")

    app.set_backend("local")

    assert app.notifications[0][0] == "Backend needs a key"


def test_trigger_mode_is_persisted(app):
    app.set_mode("hold_vad")

    assert app.settings.trigger_mode == "hold_vad"


def test_set_icon_without_a_status_item_is_safe(app):
    app.app = None

    app.set_icon(State.RECORDING)  # must not raise


def menu_titles(app) -> list[str]:
    """Titles in the Recent submenu, skipping separators (which have no title)."""
    return [
        item.title
        for item in app._menu_items["recent"].values()
        if hasattr(item, "title")
    ]


class TestRecentMenu:
    """Against real rumps objects — these need no event loop, and a stub would have
    hidden the lazily-created submenu that made refresh_recent crash on first run."""

    @pytest.fixture
    def wired(self, app):
        import rumps

        app._menu_items["recent"] = rumps.MenuItem("Recent")
        return app

    def test_refresh_on_an_empty_history_shows_a_placeholder(self, wired):
        wired.refresh_recent()

        assert menu_titles(wired) == ["Nothing yet"]

    def test_refresh_twice_on_an_empty_history_is_safe(self, wired):
        wired.refresh_recent()
        wired.refresh_recent()

        assert len(wired._menu_items["recent"]) == 1

    def test_entries_appear_with_a_clear_action(self, wired):
        wired.history.add(make_result("первая заметка"))
        wired.history.add(make_result("вторая заметка"))

        wired.refresh_recent()

        titles = menu_titles(wired)
        assert titles[0] == "вторая заметка"
        assert titles[1] == "первая заметка"
        assert "Clear history" in titles

    def test_refresh_replaces_rather_than_appends(self, wired):
        wired.history.add(make_result("старое"))
        wired.refresh_recent()

        wired.history.add(make_result("новое"))
        wired.refresh_recent()

        titles = menu_titles(wired)
        assert titles.count("старое") == 1
        assert titles[0] == "новое"

    def test_clicking_an_entry_copies_it_to_the_clipboard(self, wired, monkeypatch):
        copied: list[str] = []
        monkeypatch.setattr(wired.clipboard, "write_text", copied.append)
        entry_id = wired.history.add(make_result("скопируй меня"))

        wired._make_copier(entry_id)(None)

        assert copied == ["скопируй меня"]

    def test_copying_a_deleted_entry_does_nothing(self, wired, monkeypatch):
        copied: list[str] = []
        monkeypatch.setattr(wired.clipboard, "write_text", copied.append)
        entry_id = wired.history.add(make_result("исчезну"))
        wired.history.delete(entry_id)

        wired._make_copier(entry_id)(None)

        assert copied == []

    def test_clear_history_empties_the_menu(self, wired):
        wired.history.add(make_result("удалить"))
        wired.refresh_recent()

        wired._clear_history(None)

        assert wired.history.count() == 0
        titles = menu_titles(wired)
        assert titles == ["Nothing yet"]

    def test_result_event_refreshes_the_menu(self, wired):
        wired._on_result(make_result("через очередь"))
        wired._drain()

        titles = menu_titles(wired)
        assert titles[0] == "через очередь"


class TestSettingsWindow:
    """Opening Settings from the menu. The window was originally built into a local
    variable, so Python collected it — along with its widgets and the Save button's
    target — the moment the click handler returned, and nothing usable appeared."""

    def test_the_window_is_kept_alive_after_the_click(self, app, monkeypatch):
        import gc

        shown: list[str] = []
        monkeypatch.setattr(
            "skydict.ui.settings_window.SettingsWindow.show",
            lambda self: shown.append("shown"),
        )

        app._open_settings(None)
        gc.collect()

        assert shown == ["shown"]
        assert app._settings_window is not None

    def test_reopening_reuses_the_same_window(self, app, monkeypatch):
        monkeypatch.setattr("skydict.ui.settings_window.SettingsWindow.show", lambda self: None)

        app._open_settings(None)
        first = app._settings_window
        app._open_settings(None)

        assert app._settings_window is first

    def test_saving_applies_the_new_settings(self, app):
        new = Settings()
        new.backend = "local"

        app._settings_saved(new)

        assert app.settings.backend == "local"
        assert app.controller.rebuilt == 1

    def test_a_failing_rebuild_after_save_is_swallowed(self, app):
        app.controller.rebuild_error = RuntimeError("model gone")

        app._settings_saved(Settings())  # must not raise


def test_result_callback_is_wired_to_the_session(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: path)
    settings = Settings()
    controller = FakeController(settings)

    instance = SkyDictApp(settings, controller=controller, history=History(tmp_path / "h.db"))

    assert instance._on_state in controller.session.listeners
