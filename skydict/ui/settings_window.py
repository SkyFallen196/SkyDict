"""Native settings window.

Built directly on AppKit rather than a cross-platform toolkit so it shares the one
NSApplication runloop the menubar already owns — a second event loop in the same process
would deadlock one or the other.

The form is described by :data:`FIELDS` and the widgets are generated from it, so adding
a setting is a one-line change and the layout stays consistent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..audio.recorder import list_devices
from ..config import Settings
from ..secrets import get_key, set_key

log = logging.getLogger(__name__)

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 460
MARGIN = 20
ROW_HEIGHT = 32
LABEL_WIDTH = 150
FIELD_WIDTH = WINDOW_WIDTH - LABEL_WIDTH - MARGIN * 3


@dataclass(slots=True)
class Field:
    """One row of the form, bound to a dotted path into Settings."""

    path: str
    label: str
    kind: str  # text | choice | checkbox | number | secret | device
    choices: list[tuple[str, str]] = field(default_factory=list)
    help: str = ""


#: Tab title -> the fields shown under it.
FIELDS: dict[str, list[Field]] = {
    "General": [
        Field(
            "trigger_mode",
            "Trigger",
            "choice",
            [
                ("hold", "Hold to talk"),
                ("toggle", "Toggle"),
                ("hold_vad", "Hold, stop on silence"),
            ],
        ),
        Field("backend", "Backend", "choice", [("cloud", "Cloud"), ("local", "Local")]),
        Field("audio.input_device", "Microphone", "device"),
        Field("audio.min_recording_duration", "Ignore taps under (s)", "number"),
    ],
    "Cloud": [
        Field("cloud.base_url", "Base URL", "text", help="Any OpenAI-compatible endpoint."),
        Field("cloud.model", "Model", "text"),
        Field("cloud.language", "Language", "text", help="Blank to auto-detect."),
        Field("cloud.credential_name", "Key name", "text"),
        Field("cloud.__api_key__", "API key", "secret", help="Stored in the Keychain."),
    ],
    "Local": [
        Field(
            "local.model",
            "Model",
            "choice",
            [
                ("gigaam-v3-e2e-rnnt", "GigaAM v3 e2e RNN-T (ru, punctuated)"),
                ("gigaam-v3-e2e-ctc", "GigaAM v3 e2e CTC (ru, punctuated)"),
                ("gigaam-v3-rnnt", "GigaAM v3 RNN-T (ru)"),
                ("nemo-parakeet-tdt-0.6b-v3", "Parakeet TDT 0.6B v3 (multilingual)"),
                ("whisper-base", "Whisper base"),
            ],
        ),
        Field(
            "local.quantization",
            "Precision",
            "choice",
            [("int8", "int8 (smaller, faster)"), ("", "Full precision")],
        ),
    ],
    "Output": [
        Field(
            "insert_mode",
            "Delivery",
            "choice",
            [("paste", "Paste into the focused app"), ("clipboard_only", "Copy to clipboard")],
        ),
        Field("vad.silence_duration", "Silence before stop (s)", "number"),
        Field("vad.speech_threshold", "Speech threshold", "number"),
    ],
}


def get_path(settings: Settings, path: str) -> Any:
    value: Any = settings
    for part in path.split("."):
        value = getattr(value, part)
    return value


def set_path(settings: Settings, path: str, value: Any) -> None:
    parts = path.split(".")
    target: Any = settings
    for part in parts[:-1]:
        target = getattr(target, part)
    setattr(target, parts[-1], value)


class SettingsWindow:
    """A window whose widgets are generated from :data:`FIELDS`."""

    def __init__(self, settings: Settings, on_save: Callable[[Settings], None] | None = None):
        self.settings = settings.model_copy(deep=True)
        self.on_save = on_save
        self._window = None
        self._widgets: dict[str, Any] = {}
        #: Popup index -> setting value. Kept here because PyObjC objects reject
        #: arbitrary Python attributes, so the mapping cannot ride on the widget.
        self._choice_values: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ building

    def _device_choices(self) -> list[tuple[str, str]]:
        choices = [("", "System default")]
        try:
            for device in list_devices():
                choices.append((str(device["index"]), device["name"]))
        except Exception as exc:  # a missing audio stack must not block the window
            log.warning("Could not list input devices: %s", exc)
        return choices

    def _make_row(self, container, spec: Field, top: float) -> None:
        from AppKit import (
            NSButton,
            NSFont,
            NSPopUpButton,
            NSSecureTextField,
            NSSwitchButton,
            NSTextField,
        )
        from Foundation import NSMakeRect

        label = NSTextField.labelWithString_(spec.label)
        label.setFrame_(NSMakeRect(MARGIN, top, LABEL_WIDTH, 20))
        label.setAlignment_(2)  # right
        container.addSubview_(label)

        x = MARGIN * 2 + LABEL_WIDTH
        widget = None

        if spec.kind in {"choice", "device"}:
            choices = self._device_choices() if spec.kind == "device" else spec.choices
            widget = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(x, top - 4, FIELD_WIDTH, 26), False
            )
            for _value, title in choices:
                widget.addItemWithTitle_(title)
            values = [value for value, _ in choices]
            self._choice_values[spec.path] = values

            current = get_path(self.settings, spec.path) if "__" not in spec.path else None
            current = "" if current is None else str(current)
            if current in values:
                widget.selectItemAtIndex_(values.index(current))

        elif spec.kind == "checkbox":
            widget = NSButton.alloc().initWithFrame_(NSMakeRect(x, top - 2, FIELD_WIDTH, 22))
            widget.setButtonType_(NSSwitchButton)
            widget.setTitle_("")
            widget.setState_(bool(get_path(self.settings, spec.path)))

        else:
            factory = NSSecureTextField if spec.kind == "secret" else NSTextField
            widget = factory.alloc().initWithFrame_(NSMakeRect(x, top - 3, FIELD_WIDTH, 24))
            if spec.path.endswith("__api_key__"):
                stored = get_key(self.settings.cloud.credential_name)
                widget.setStringValue_(stored or "")
                widget.setPlaceholderString_("Not set")
            else:
                value = get_path(self.settings, spec.path)
                widget.setStringValue_("" if value is None else str(value))

        container.addSubview_(widget)
        self._widgets[spec.path] = widget

        if spec.help:
            hint = NSTextField.labelWithString_(spec.help)
            hint.setFrame_(NSMakeRect(x, top - 22, FIELD_WIDTH, 16))
            hint.setFont_(NSFont.systemFontOfSize_(10))
            hint.setTextColor_(_secondary_colour())
            container.addSubview_(hint)

    def _build_tab(self, specs: list[Field]):
        from AppKit import NSView
        from Foundation import NSMakeRect

        view = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT - 100)
        )
        top = WINDOW_HEIGHT - 160
        for spec in specs:
            self._make_row(view, spec, top)
            top -= ROW_HEIGHT + (14 if spec.help else 0)
        return view

    def build(self):
        from AppKit import (
            NSBackingStoreBuffered,
            NSButton,
            NSTabView,
            NSTabViewItem,
            NSTitledWindowMask,
            NSWindow,
            NSWindowStyleMaskClosable,
        )
        from Foundation import NSMakeRect

        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WINDOW_WIDTH, WINDOW_HEIGHT),
            NSTitledWindowMask | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("SkyDict Settings")
        window.center()

        tabs = NSTabView.alloc().initWithFrame_(
            NSMakeRect(MARGIN, 60, WINDOW_WIDTH - MARGIN * 2, WINDOW_HEIGHT - 90)
        )
        for title, specs in FIELDS.items():
            item = NSTabViewItem.alloc().initWithIdentifier_(title)
            item.setLabel_(title)
            item.setView_(self._build_tab(specs))
            tabs.addTabViewItem_(item)
        window.contentView().addSubview_(tabs)

        save = NSButton.alloc().initWithFrame_(NSMakeRect(WINDOW_WIDTH - 120, 16, 100, 32))
        save.setTitle_("Save")
        save.setBezelStyle_(1)
        save.setTarget_(_ActionProxy.make(self.save))
        save.setAction_("invoke:")
        window.contentView().addSubview_(save)
        # Keep the proxy alive: AppKit targets are weak references.
        self._save_proxy = save.target()

        self._window = window
        return window

    # ------------------------------------------------------------------ saving

    def collect(self) -> tuple[Settings, str | None]:
        """Read the widgets back into a Settings object and the pending API key."""
        api_key: str | None = None

        for path, widget in self._widgets.items():
            if path.endswith("__api_key__"):
                api_key = widget.stringValue().strip() or None
                continue

            raw = self._widget_value(path, widget)
            spec = self._spec_for(path)
            set_path(self.settings, path, _coerce(raw, spec, get_path(self.settings, path)))

        return self.settings, api_key

    def _widget_value(self, path: str, widget) -> Any:
        if path in self._choice_values:
            return self._choice_values[path][widget.indexOfSelectedItem()]
        if hasattr(widget, "state") and not hasattr(widget, "stringValue"):
            return bool(widget.state())
        return widget.stringValue()

    @staticmethod
    def _spec_for(path: str) -> Field | None:
        for specs in FIELDS.values():
            for spec in specs:
                if spec.path == path:
                    return spec
        return None

    def save(self) -> None:
        settings, api_key = self.collect()
        settings.save()
        if api_key:
            set_key(settings.cloud.credential_name, api_key)
        if self.on_save is not None:
            self.on_save(settings)
        if self._window is not None:
            self._window.close()

    def show(self) -> None:
        from AppKit import NSApp

        window = self._window or self.build()
        NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)


def _coerce(raw: Any, spec: Field | None, current: Any) -> Any:
    """Turn a widget's string back into the type the settings field expects."""
    if spec is not None and spec.kind == "checkbox":
        return bool(raw)

    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            # An empty box means "unset" for optional fields, and "unchanged" otherwise.
            return None if current is None or spec is None or spec.kind != "number" else current
        if spec is not None and spec.kind == "number":
            try:
                return float(raw)
            except ValueError:
                log.warning("Ignoring non-numeric value %r for %s", raw, spec.path)
                return current
        if spec is not None and spec.kind == "device" and raw.isdigit():
            return int(raw)
    return raw


def _secondary_colour():
    from AppKit import NSColor

    return NSColor.secondaryLabelColor()


class _ActionProxy:
    """Bridges an AppKit target/action back to a Python callable.

    The Objective-C class is defined once and reused. Objective-C class names live in a
    single process-wide namespace, so defining it per call raises "overriding existing
    Objective-C class" the second time a window is opened.
    """

    _registry: list = []
    _proxy_class = None

    @classmethod
    def _get_class(cls):
        if cls._proxy_class is None:
            from Foundation import NSObject

            class _SkyDictActionProxy(NSObject):
                def invoke_(self, _sender):
                    self._callback()

            cls._proxy_class = _SkyDictActionProxy
        return cls._proxy_class

    @classmethod
    def make(cls, callback: Callable[[], None]):
        proxy = cls._get_class().alloc().init()
        proxy._callback = callback
        cls._registry.append(proxy)  # AppKit holds targets weakly
        return proxy
