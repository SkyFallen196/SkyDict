from __future__ import annotations

from skydict.macos import permissions


def test_bundle_is_detected_from_the_py2app_environment(monkeypatch):
    monkeypatch.setenv("RESOURCEPATH", "/Applications/SkyDict.app/Contents/Resources")

    assert permissions.running_in_bundle()


def test_bundle_is_detected_from_the_executable_path(monkeypatch):
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    monkeypatch.setattr(
        permissions.sys, "executable", "/Applications/SkyDict.app/Contents/MacOS/python"
    )

    assert permissions.running_in_bundle()


def test_a_plain_interpreter_is_not_a_bundle(monkeypatch):
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    monkeypatch.setattr(permissions.sys, "executable", "/opt/anaconda3/envs/SkyDict/bin/python")

    assert not permissions.running_in_bundle()


def test_bundled_message_names_the_app(monkeypatch):
    """In a bundle the Accessibility entry is SkyDict itself, not the terminal."""
    monkeypatch.setenv("RESOURCEPATH", "/Applications/SkyDict.app/Contents/Resources")

    message = permissions._accessibility_message("paste")

    assert "Enable SkyDict in the list" in message
    assert "terminal" not in message


def test_unbundled_message_explains_the_terminal_quirk(monkeypatch):
    monkeypatch.delenv("RESOURCEPATH", raising=False)
    monkeypatch.setattr(permissions.sys, "executable", "/opt/anaconda3/envs/SkyDict/bin/python")

    message = permissions._accessibility_message("paste")

    assert "terminal app itself" in message
    assert "/opt/anaconda3/envs/SkyDict/bin/python" in message
