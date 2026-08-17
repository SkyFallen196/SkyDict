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

WINDOW_WIDTH = 540
WINDOW_HEIGHT = 380
MARGIN = 20
#: Minimum width for the control column. NSGridView sizes rows itself; this only stops
#: a form of short values from collapsing into a narrow strip.
FIELD_WIDTH = 300


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
        self._previous_policy: int | None = None
        self._save_proxy = None
        self._delegate = None

    # ------------------------------------------------------------------ building

    def _device_choices(self) -> list[tuple[str, str]]:
        choices = [("", "System default")]
        try:
            for device in list_devices():
                choices.append((str(device["index"]), device["name"]))
        except Exception as exc:  # a missing audio stack must not block the window
            log.warning("Could not list input devices: %s", exc)
        return choices

    def _make_control(self, spec: Field):
        """Build the input widget for one field, already populated from settings."""
        from AppKit import NSButton, NSPopUpButton, NSSecureTextField, NSSwitchButton, NSTextField
        from Foundation import NSMakeRect

        if spec.kind in {"choice", "device"}:
            choices = self._device_choices() if spec.kind == "device" else spec.choices
            widget = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 0, FIELD_WIDTH, 25), False
            )
            for _value, title in choices:
                widget.addItemWithTitle_(title)
            values = [value for value, _ in choices]
            self._choice_values[spec.path] = values

            current = get_path(self.settings, spec.path) if "__" not in spec.path else None
            current = "" if current is None else str(current)
            if current in values:
                widget.selectItemAtIndex_(values.index(current))
            return widget

        if spec.kind == "checkbox":
            widget = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, FIELD_WIDTH, 22))
            widget.setButtonType_(NSSwitchButton)
            widget.setTitle_("")
            widget.setState_(bool(get_path(self.settings, spec.path)))
            return widget

        factory = NSSecureTextField if spec.kind == "secret" else NSTextField
        widget = factory.alloc().initWithFrame_(NSMakeRect(0, 0, FIELD_WIDTH, 22))
        if spec.path.endswith("__api_key__"):
            stored = get_key(self.settings.cloud.credential_name)
            widget.setStringValue_(stored or "")
            widget.setPlaceholderString_("Not set")
        else:
            value = get_path(self.settings, spec.path)
            widget.setStringValue_("" if value is None else str(value))
        return widget

    def _build_tab(self, specs: list[Field]):
        """Lay a tab out with NSGridView.

        Hand-computed frames were the earlier approach and they drifted: the coordinates
        were derived from the window, but each tab view has its own smaller coordinate
        space, so rows crept upward and labels sat off their controls. A grid sizes and
        aligns its own rows, and stays right when fonts or accessibility sizes change.
        """
        from AppKit import (
            NSFont,
            NSGridCell,
            NSGridCellPlacementFill,
            NSGridCellPlacementTrailing,
            NSGridRowAlignmentFirstBaseline,
            NSGridView,
            NSTextField,
            NSView,
        )
        from Foundation import NSMakeRect

        rows: list[list] = []
        for spec in specs:
            label = NSTextField.labelWithString_(spec.label)
            control = self._make_control(spec)
            # Without this the grid shrinks every control to fit its current text.
            control.widthAnchor().constraintGreaterThanOrEqualToConstant_(
                FIELD_WIDTH
            ).setActive_(True)
            self._widgets[spec.path] = control
            rows.append([label, control])

            if spec.help:
                hint = NSTextField.labelWithString_(spec.help)
                hint.setFont_(NSFont.systemFontOfSize_(11))
                hint.setTextColor_(_secondary_colour())
                # Empty first cell keeps the hint under its control, not under the label.
                rows.append([NSGridCell.emptyContentView(), hint])

        grid = NSGridView.gridViewWithViews_(rows)
        grid.setTranslatesAutoresizingMaskIntoConstraints_(False)
        grid.setRowSpacing_(10)
        grid.setColumnSpacing_(12)
        grid.columnAtIndex_(0).setXPlacement_(NSGridCellPlacementTrailing)
        grid.columnAtIndex_(1).setXPlacement_(NSGridCellPlacementFill)
        grid.setRowAlignment_(NSGridRowAlignmentFirstBaseline)

        container = NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, WINDOW_WIDTH - MARGIN * 2, WINDOW_HEIGHT - 120)
        )
        container.addSubview_(grid)
        _pin_to_top(grid, container)
        return container

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
        # Cocoa frees a window on close by default, which would leave a dangling
        # reference the second time Settings is opened.
        window.setReleasedWhenClosed_(False)
        self._delegate = _WindowCloseDelegate.make(self._restore_activation_policy)
        window.setDelegate_(self._delegate)

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
        """Bring the window up, borrowing a regular app's focus behaviour.

        A menubar app runs with the accessory activation policy, which has no Dock icon
        and cannot take keyboard focus — its windows come up unfocused and behind, so the
        form looks dead. Switching to the regular policy while the window is open fixes
        that; :meth:`_restore_activation_policy` puts it back on close.
        """
        from AppKit import NSApp, NSApplicationActivationPolicyRegular

        window = self._window or self.build()

        self._previous_policy = NSApp.activationPolicy()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()

    def _restore_activation_policy(self) -> None:
        from AppKit import NSApp

        if self._previous_policy is not None:
            NSApp.setActivationPolicy_(self._previous_policy)
            self._previous_policy = None


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


def _pin_to_top(view, container, inset: float = MARGIN) -> None:
    """Anchor a view to the top of its container, leaving it free to size itself."""
    container.addConstraints_(
        [
            view.topAnchor().constraintEqualToAnchor_constant_(
                container.topAnchor(), inset
            ),
            view.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
                container.leadingAnchor(), inset
            ),
            view.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                container.trailingAnchor(), -inset
            ),
            view.centerXAnchor().constraintEqualToAnchor_(container.centerXAnchor()),
        ]
    )


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


class _WindowCloseDelegate:
    """Runs a callback when the window closes. Same one-time class trick as above."""

    _registry: list = []
    _delegate_class = None

    @classmethod
    def _get_class(cls):
        if cls._delegate_class is None:
            from Foundation import NSObject

            class _SkyDictWindowDelegate(NSObject):
                def windowWillClose_(self, _notification):
                    self._callback()

            cls._delegate_class = _SkyDictWindowDelegate
        return cls._delegate_class

    @classmethod
    def make(cls, callback: Callable[[], None]):
        delegate = cls._get_class().alloc().init()
        delegate._callback = callback
        cls._registry.append(delegate)  # NSWindow holds its delegate weakly
        return delegate
