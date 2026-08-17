"""The menubar app.

Threading is the whole story here. AppKit may only be touched from the main thread, but
pipeline state changes arrive on the audio and transcription threads. Rather than hop
threads per event, those threads push onto a queue and a rumps timer drains it on the
main thread. That keeps every AppKit call on the main runloop by construction, and makes
the UI logic testable without an event loop at all.

The hotkey listener runs its own CFRunLoop on a separate thread, so it coexists with
NSApplication's without either owning the other.
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable

from ..config import BackendName, Settings, TriggerMode
from ..controller import DictationController
from ..history import History
from ..macos.permissions import (
    check_listen_access,
    check_microphone,
    check_post_access,
    open_accessibility_settings,
)
from ..output.clipboard import Clipboard
from ..pipeline import DictationResult, State
from ..secrets import MissingCredentialError

log = logging.getLogger(__name__)

#: Status icons, chosen to read at a glance in a crowded menubar.
STATE_ICONS: dict[State, str] = {
    State.IDLE: "🎙",
    State.RECORDING: "🔴",
    State.TRANSCRIBING: "✳️",
    State.DELIVERING: "✳️",
    State.ERROR: "⚠️",
}

#: How often the main thread drains the event queue. Fast enough that the icon tracks
#: speech, cheap enough to be invisible.
POLL_INTERVAL = 0.1

RECENT_COUNT = 8


class SkyDictApp:
    """Wraps rumps.App, keeping the menu in sync with settings and pipeline state."""

    def __init__(
        self,
        settings: Settings | None = None,
        controller: DictationController | None = None,
        history: History | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.history = history if history is not None else History()
        self.clipboard = Clipboard()
        self.controller = controller or DictationController(
            self.settings,
            on_result=self._on_result,
            on_error=self._on_error,
        )
        self.controller.session.add_listener(self._on_state)

        #: Cross-thread channel: producers are the audio and transcription threads,
        #: the consumer is the rumps timer on the main thread.
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.app = None
        self._menu_items: dict[str, object] = {}
        self._settings_window = None
        self.last_error: Exception | None = None

    # ------------------------------------------------------------------ callbacks

    def _on_state(self, state: State, _session) -> None:
        self._events.put(("state", state))

    def _on_result(self, result: DictationResult) -> None:
        self.history.add(result)
        self._events.put(("result", result))

    def _on_error(self, exc: Exception) -> None:
        self.last_error = exc
        self._events.put(("error", exc))

    # ------------------------------------------------------------------ main thread

    def _drain(self, _timer=None) -> None:
        """Apply queued events. Runs on the main thread via the rumps timer."""
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                self._apply(kind, payload)
            except Exception as exc:  # a UI slip must never stop the drain loop
                log.exception("Failed to apply %s event: %s", kind, exc)

    def _apply(self, kind: str, payload: object) -> None:
        if kind == "state":
            self.set_icon(payload)
        elif kind == "result":
            self.refresh_recent()
        elif kind == "error":
            self.set_icon(State.ERROR)
            self.notify("Dictation failed", str(payload))

    def set_icon(self, state: State) -> None:
        if self.app is not None:
            self.app.title = STATE_ICONS.get(state, STATE_ICONS[State.IDLE])

    def notify(self, title: str, message: str) -> None:
        try:
            import rumps

            rumps.notification(title, "", message)
        except Exception as exc:  # notifications need a bundled app; never fatal
            log.debug("Could not post notification: %s", exc)

    # ------------------------------------------------------------------ menu

    def build_menu(self) -> list:
        import rumps

        backend_items = []
        for name in ("cloud", "local"):
            item = rumps.MenuItem(
                {"cloud": "Cloud (OpenAI-compatible)", "local": "Local (offline)"}[name],
                callback=self._make_backend_setter(name),
            )
            item.state = self.settings.backend == name
            self._menu_items[f"backend:{name}"] = item
            backend_items.append(item)

        mode_items = []
        for mode, label in (
            ("hold", "Hold to talk"),
            ("toggle", "Toggle"),
            ("hold_vad", "Hold, stop on silence"),
        ):
            item = rumps.MenuItem(label, callback=self._make_mode_setter(mode))
            item.state = self.settings.trigger_mode == mode
            self._menu_items[f"mode:{mode}"] = item
            mode_items.append(item)

        recent = rumps.MenuItem("Recent")
        self._menu_items["recent"] = recent

        return [
            rumps.MenuItem("Dictation", callback=None),
            {"Backend": backend_items},
            {"Trigger": mode_items},
            None,
            recent,
            None,
            rumps.MenuItem("Settings…", callback=self._open_settings),
            rumps.MenuItem("Permissions…", callback=self._show_permissions),
            None,
            rumps.MenuItem("Quit SkyDict", callback=self._quit),
        ]

    def refresh_recent(self) -> None:
        """Rebuild the Recent submenu from history."""
        import rumps

        recent = self._menu_items.get("recent")
        if recent is None:
            return

        # A MenuItem creates its NSMenu lazily on first insert, so clearing one that has
        # never had children would dereference None.
        if len(recent):
            recent.clear()
        entries = self.history.recent(RECENT_COUNT)
        if not entries:
            empty = rumps.MenuItem("Nothing yet", callback=None)
            recent.add(empty)
            return

        for entry in entries:
            recent.add(rumps.MenuItem(entry.preview, callback=self._make_copier(entry.id)))
        recent.add(rumps.separator)
        recent.add(rumps.MenuItem("Clear history", callback=self._clear_history))

    def _make_copier(self, entry_id: int) -> Callable:
        def copy(_sender) -> None:
            entry = self.history.get(entry_id)
            if entry is not None:
                self.clipboard.write_text(entry.text)

        return copy

    def _clear_history(self, _sender) -> None:
        self.history.clear()
        self.refresh_recent()

    def _make_backend_setter(self, name: BackendName) -> Callable:
        def choose(_sender) -> None:
            self.set_backend(name)

        return choose

    def set_backend(self, name: BackendName) -> None:
        """Switch backend at runtime, rebuilding the session around the new one."""
        if name == self.settings.backend:
            return
        self.settings.backend = name
        self.settings.save()

        for candidate in ("cloud", "local"):
            item = self._menu_items.get(f"backend:{candidate}")
            if item is not None:
                item.state = candidate == name

        try:
            self.controller.rebuild_backend()
        except MissingCredentialError as exc:
            self.notify("Backend needs a key", str(exc))
        except Exception as exc:
            log.exception("Could not switch backend: %s", exc)
            self.notify("Could not switch backend", str(exc))

    def _make_mode_setter(self, mode: TriggerMode) -> Callable:
        def choose(_sender) -> None:
            self.set_mode(mode)

        return choose

    def set_mode(self, mode: TriggerMode) -> None:
        self.settings.trigger_mode = mode
        self.settings.save()
        for candidate in ("hold", "toggle", "hold_vad"):
            item = self._menu_items.get(f"mode:{candidate}")
            if item is not None:
                item.state = candidate == mode

    def _open_settings(self, _sender) -> None:
        from .settings_window import SettingsWindow

        # Held on the app, not a local: dropping the last Python reference would let the
        # window, its widgets and the Save button's target be collected on the way out.
        if self._settings_window is None:
            self._settings_window = SettingsWindow(
                self.settings, on_save=self._settings_saved
            )
        self._settings_window.show()

    def _settings_saved(self, settings: Settings) -> None:
        self.settings = settings
        settings.save()
        try:
            self.controller.rebuild_backend()
        except Exception as exc:
            log.warning("Backend rebuild after settings change failed: %s", exc)

    def _show_permissions(self, _sender) -> None:
        import rumps

        lines = [
            f"Microphone: {check_microphone()}",
            f"Hotkey (listen events): {'granted' if check_listen_access() else 'MISSING'}",
            f"Paste (post events): {'granted' if check_post_access() else 'MISSING'}",
        ]
        window = rumps.Window(
            message="\n".join(lines),
            title="Permissions",
            ok="Open System Settings",
            cancel="Close",
            dimensions=(0, 0),
        )
        if window.run().clicked:
            open_accessibility_settings()

    def _quit(self, _sender) -> None:
        import rumps

        self.controller.stop()
        rumps.quit_application()

    # ------------------------------------------------------------------ lifecycle

    def run(self) -> None:
        import rumps

        self.app = rumps.App("SkyDict", title=STATE_ICONS[State.IDLE], quit_button=None)
        self.app.menu = self.build_menu()
        self.refresh_recent()

        try:
            self.controller.start()
        except Exception as exc:
            log.error("Could not start dictation: %s", exc)
            self.set_icon(State.ERROR)
            self.last_error = exc
            self.notify("SkyDict could not start", str(exc))

        rumps.Timer(self._drain, POLL_INTERVAL).start()
        self.app.run()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    SkyDictApp().run()


if __name__ == "__main__":
    main()
