"""Settings-window logic: field binding and coercion, with no AppKit involved."""

from __future__ import annotations

import sys

import pytest

from skydict.config import Settings
from skydict.ui.settings_window import FIELDS, Field, SettingsWindow, _coerce, get_path, set_path


def test_every_field_binds_to_a_real_setting():
    """A typo in a field path would silently do nothing at runtime."""
    settings = Settings()
    for specs in FIELDS.values():
        for spec in specs:
            if "__" in spec.path:  # pseudo-fields like the API key
                continue
            get_path(settings, spec.path)  # raises AttributeError if the path is wrong


def test_choice_values_are_valid_for_their_setting():
    settings = Settings()
    for specs in FIELDS.values():
        for spec in specs:
            if spec.kind != "choice" or "__" in spec.path:
                continue
            for value, _label in spec.choices:
                set_path(settings, spec.path, value or None)
                Settings.model_validate(settings.model_dump())


def test_get_and_set_walk_nested_paths():
    settings = Settings()

    set_path(settings, "cloud.model", "whisper-large-v3")
    set_path(settings, "vad.silence_duration", 2.0)

    assert get_path(settings, "cloud.model") == "whisper-large-v3"
    assert get_path(settings, "vad.silence_duration") == 2.0


def test_the_window_edits_a_copy_not_the_live_settings():
    """Cancelling by closing the window must leave the running config untouched."""
    original = Settings()

    window = SettingsWindow(original)
    window.settings.cloud.model = "changed"

    assert original.cloud.model != "changed"


@pytest.mark.parametrize(
    ("raw", "kind", "current", "expected"),
    [
        ("2.5", "number", 1.5, 2.5),
        ("not a number", "number", 1.5, 1.5),  # keeps the old value
        ("", "number", 1.5, 1.5),
        ("ru", "text", "en", "ru"),
        ("", "text", "en", None),  # blank clears an optional field
        ("2", "device", None, 2),
        ("", "device", None, None),
        (True, "checkbox", False, True),
    ],
)
def test_coercion(raw, kind, current, expected):
    spec = Field("some.path", "Label", kind)

    assert _coerce(raw, spec, current) == expected


def test_numbers_survive_a_round_trip_through_the_form():
    settings = Settings()
    spec = Field("vad.silence_duration", "Silence", "number")

    value = _coerce("3.0", spec, get_path(settings, spec.path))
    set_path(settings, spec.path, value)

    assert Settings.model_validate(settings.model_dump()).vad.silence_duration == 3.0


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS AppKit")
class TestAgainstAppKit:
    """Builds the real window. No event loop is needed, and stubbing AppKit would have
    hidden that PyObjC objects reject the arbitrary Python attributes an earlier version
    tried to hang the popup values on."""

    @pytest.fixture
    def window(self):
        window = SettingsWindow(Settings())
        window.build()
        return window

    def test_every_field_becomes_a_widget(self, window):
        expected = sum(len(specs) for specs in FIELDS.values())

        assert len(window._widgets) == expected

    def test_text_fields_round_trip(self, window):
        window._widgets["cloud.model"].setStringValue_("whisper-large-v3")

        settings, _ = window.collect()

        assert settings.cloud.model == "whisper-large-v3"

    def test_numbers_round_trip_as_floats(self, window):
        window._widgets["vad.silence_duration"].setStringValue_("2.5")

        settings, _ = window.collect()

        assert settings.vad.silence_duration == 2.5

    def test_popups_round_trip_by_value_not_label(self, window):
        popup = window._widgets["trigger_mode"]
        popup.selectItemAtIndex_(window._choice_values["trigger_mode"].index("hold_vad"))

        settings, _ = window.collect()

        assert settings.trigger_mode == "hold_vad"

    def test_untouched_fields_keep_their_values(self, window):
        settings, _ = window.collect()

        assert settings.local.quantization == "int8"
        assert settings.cloud.base_url == Settings().cloud.base_url

    def test_collected_settings_still_validate(self, window):
        settings, _ = window.collect()

        Settings.model_validate(settings.model_dump())

    def test_api_key_is_returned_separately_and_not_stored_in_settings(self, window):
        window._widgets["cloud.__api_key__"].setStringValue_("  secret-key  ")

        settings, api_key = window.collect()

        assert api_key == "secret-key"
        assert "secret-key" not in settings.model_dump_json()

    def test_blank_api_key_means_unchanged(self, window):
        settings, api_key = window.collect()

        assert api_key is None

    def test_the_window_can_be_opened_more_than_once(self):
        """Objective-C class names are process-global; defining the action proxy class
        per window raised "overriding existing Objective-C class" on the second open."""
        SettingsWindow(Settings()).build()
        SettingsWindow(Settings()).build()
        SettingsWindow(Settings()).build()

    def test_controls_are_laid_out_in_evenly_spaced_rows(self, window):
        """Hand-computed frames drifted because each tab has its own coordinate space,
        leaving labels sitting off their controls. The grid must produce even rows."""
        window._window.contentView().layoutSubtreeIfNeeded()

        general = ["trigger_mode", "backend", "audio.input_device"]
        tops = [window._widgets[path].frame().origin.y for path in general]

        assert all(y > 0 for y in tops[:-1]), "rows collapsed to the origin"
        gaps = {round(tops[i] - tops[i + 1]) for i in range(len(tops) - 1)}
        assert len(gaps) == 1, f"uneven row spacing: {gaps}"

    def test_controls_share_a_width_instead_of_hugging_their_text(self, window):
        window._window.contentView().layoutSubtreeIfNeeded()

        widths = {
            round(window._widgets[path].frame().size.width)
            for path in ("trigger_mode", "backend", "audio.input_device")
        }

        assert len(widths) == 1
        assert widths.pop() >= 300

    def test_every_tab_lays_its_controls_out(self, window):
        tabs = [
            view
            for view in window._window.contentView().subviews()
            if view.__class__.__name__ == "NSTabView"
        ][0]

        for index in range(len(FIELDS)):
            tabs.selectTabViewItemAtIndex_(index)
            window._window.contentView().layoutSubtreeIfNeeded()

            assert all(w.frame().size.width > 0 for w in window._widgets.values())

    def test_the_window_is_not_freed_when_closed(self, window):
        """Cocoa frees a window on close by default, dangling the second open."""
        assert not window._window.isReleasedWhenClosed()

    def test_closing_restores_the_menubar_activation_policy(self, window, monkeypatch):
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory

        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        window.show()
        assert NSApp.activationPolicy() != NSApplicationActivationPolicyAccessory

        window._delegate.windowWillClose_(None)

        assert NSApp.activationPolicy() == NSApplicationActivationPolicyAccessory

    def test_a_bad_value_keeps_the_window_open_and_saves_nothing(self, monkeypatch):
        """Saving -5 used to write a config that could never be loaded again, so the app
        refused to start with no clue why."""
        saved: list[Settings] = []
        alerts: list[str] = []
        window = SettingsWindow(Settings(), on_save=saved.append)
        window.build()
        monkeypatch.setattr(
            window, "_show_validation_error", lambda exc: alerts.append(str(exc))
        )
        window.show()

        window._widgets["vad.silence_duration"].setStringValue_("-5")
        window.save()

        assert alerts, "the user was told nothing"
        assert saved == [], "an invalid config was handed to the app"
        assert window._window.isVisible(), "the window closed on a rejected form"

    def test_saving_keeps_the_window_open_for_more_edits(self):
        saved: list[Settings] = []
        window = SettingsWindow(Settings(), on_save=saved.append)
        window.build()
        window.show()

        window._widgets["vad.silence_duration"].setStringValue_("2.5")
        window.save()

        assert saved and saved[0].vad.silence_duration == 2.5
        assert window._window.isVisible(), "Save should not close the window"
        assert window._status_label.stringValue() == "Saved"

    def test_several_saves_in_a_row_all_apply(self):
        saved: list[Settings] = []
        window = SettingsWindow(Settings(), on_save=saved.append)
        window.build()

        window._widgets["vad.silence_duration"].setStringValue_("2.5")
        window.save()
        window._widgets["cloud.model"].setStringValue_("whisper-1")
        window.save()

        assert len(saved) == 2
        assert saved[-1].vad.silence_duration == 2.5
        assert saved[-1].cloud.model == "whisper-1"

    def test_close_closes_the_window(self):
        window = SettingsWindow(Settings())
        window.build()
        window.show()

        window.close()

        assert not window._window.isVisible()

    def test_a_rejected_save_shows_no_success_message(self, monkeypatch):
        window = SettingsWindow(Settings())
        window.build()
        monkeypatch.setattr(window, "_show_validation_error", lambda exc: None)

        window._widgets["vad.silence_duration"].setStringValue_("-5")
        window.save()

        assert window._status_label.stringValue() == ""

    def test_the_save_button_reaches_the_callback(self):
        saved: list[Settings] = []
        window = SettingsWindow(Settings(), on_save=saved.append)
        window.build()
        window._widgets["cloud.model"].setStringValue_("through-the-button")

        # Invoke the proxy exactly as AppKit would on a click.
        window._save_proxy.invoke_(None)

        assert saved and saved[0].cloud.model == "through-the-button"


def test_api_key_field_is_not_a_settings_path():
    """The key belongs in the Keychain, never in the settings file."""
    key_fields = [s for specs in FIELDS.values() for s in specs if s.kind == "secret"]

    assert key_fields
    for spec in key_fields:
        assert "__" in spec.path
