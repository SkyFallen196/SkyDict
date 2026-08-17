"""Command line interface — the stage-1 way to drive and verify the pipeline."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional

import typer

from .audio.recorder import list_devices
from .config import SAMPLE_RATE, BackendName, Settings, TriggerMode, config_path
from .controller import DictationController
from .history import History
from .macos.hotkey import DEFAULT_TRIGGER
from .macos.permissions import (
    PermissionError_,
    check_listen_access,
    check_microphone,
    check_post_access,
    host_process_name,
    open_settings_pane,
    request_accessibility,
    require_listen_access,
    require_microphone,
)
from .pipeline import DictationSession, State, TooShortError
from .secrets import MissingCredentialError, get_key, set_key
from .stt.base import SttError, read_wav
from .stt.registry import build_backend

app = typer.Typer(help="SkyDict — dictation for macOS.", no_args_is_help=True)


def _configure_logging(verbose: bool) -> None:
    # --verbose means "more detail about SkyDict", not a firehose of httpcore and
    # urllib3 internals, so the root logger stays at WARNING either way.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("skydict").setLevel(logging.DEBUG if verbose else logging.INFO)


def _report_missing_credential(exc: MissingCredentialError) -> None:
    """Print the missing-key error along with the offline way out, then exit."""
    typer.secho(str(exc), fg=typer.colors.RED, err=True)
    typer.secho(
        "Or run it offline with no key at all by adding: --backend local",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(1) from exc


def _load_settings(backend: BackendName | None, model: str | None) -> Settings:
    # strict: a hand-edited config should report its own mistake here rather than being
    # silently replaced by defaults, which would look like the edit did nothing.
    try:
        settings = Settings.load(strict=True)
    except Exception as exc:
        typer.secho(f"Invalid settings in {config_path()}:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if backend:
        settings.backend = backend
    if model:
        if settings.backend == "cloud":
            settings.cloud.model = model
        else:
            settings.local.model = model
    return settings


@app.command()
def devices() -> None:
    """List available audio input devices."""
    found = list_devices()
    if not found:
        typer.secho("No input devices found.", fg=typer.colors.RED)
        raise typer.Exit(1)
    for device in found:
        marker = "*" if device["is_default"] else " "
        typer.echo(f"{marker} [{device['index']}] {device['name']}  ({device['channels']} ch)")
    typer.echo("\n* = system default")


@app.command()
def transcribe(
    file: Path = typer.Option(..., "--file", "-f", exists=True, help="16-bit PCM WAV file."),
    backend: Optional[BackendName] = typer.Option(None, "--backend", "-b"),
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Transcribe an existing WAV file."""
    _configure_logging(verbose)
    settings = _load_settings(backend, model)

    try:
        audio, sample_rate = read_wav(str(file))
        session = DictationSession(settings)
        result = session.transcribe_audio(audio, sample_rate)
    except MissingCredentialError as exc:
        _report_missing_credential(exc)
    except SttError as exc:
        typer.secho(f"Transcription failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(result.text)
    if verbose:
        typer.secho(
            f"[{result.transcript.backend}/{result.transcript.model}] "
            f"{result.audio_duration:.1f}s of audio",
            fg=typer.colors.BLUE,
            err=True,
        )


@app.command()
def record(
    seconds: Optional[float] = typer.Option(
        None, "--seconds", "-s", help="Fixed recording length."
    ),
    vad: bool = typer.Option(False, "--vad", help="Stop automatically after silence."),
    backend: Optional[BackendName] = typer.Option(None, "--backend", "-b"),
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Record from the microphone and print the transcription.

    Without --seconds or --vad, recording runs until you press Ctrl+C.
    """
    _configure_logging(verbose)
    settings = _load_settings(backend, model)

    if not seconds and not vad:
        typer.secho("Recording... press Ctrl+C to stop.", fg=typer.colors.YELLOW, err=True)

    session = DictationSession(settings)
    session.add_listener(
        lambda state, _: typer.secho(f"[{state.value}]", fg=typer.colors.BLUE, err=True)
        if state is not State.IDLE
        else None
    )

    try:
        session.warmup(use_vad=vad)
        result = session.record_once(seconds=seconds, use_vad=vad)
    except KeyboardInterrupt:
        # Ctrl+C during a hold-style recording means "stop and transcribe", not "abort".
        try:
            result = session.stop_and_transcribe()
        except (SttError, TooShortError, RuntimeError) as exc:
            typer.secho(f"\n{exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    except TooShortError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1) from exc
    except MissingCredentialError as exc:
        _report_missing_credential(exc)
    except SttError as exc:
        typer.secho(f"Transcription failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(result.text)


@app.command("set-key")
def set_key_command(
    name: str = typer.Argument(..., help="Credential name, e.g. 'groq'."),
) -> None:
    """Store an API key in the macOS Keychain."""
    value = typer.prompt(f"API key for '{name}'", hide_input=True)
    if not value.strip():
        typer.secho("Empty key, nothing stored.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    set_key(name, value.strip())
    typer.secho(f"Stored key for '{name}' in the Keychain.", fg=typer.colors.GREEN)


@app.command("check")
def check(
    backend: Optional[BackendName] = typer.Option(None, "--backend", "-b"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Verify that the configured backend is reachable and ready."""
    _configure_logging(verbose)
    settings = _load_settings(backend, None)
    stt = build_backend(settings)

    try:
        if hasattr(stt, "check_connection"):
            typer.secho(stt.check_connection(), fg=typer.colors.GREEN)
        else:
            typer.secho(
                f"Loading '{stt.model}' (first run downloads it)...", fg=typer.colors.YELLOW
            )
            stt.warmup()
            typer.secho(f"Local model '{stt.model}' is ready.", fg=typer.colors.GREEN)
    except MissingCredentialError as exc:
        _report_missing_credential(exc)
    except SttError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@app.command()
def listen(
    trigger: str = typer.Option(DEFAULT_TRIGGER, "--trigger", "-t", help="Modifier key to hold."),
    mode: Optional[TriggerMode] = typer.Option(None, "--mode", help="hold, toggle or hold_vad."),
    backend: Optional[BackendName] = typer.Option(None, "--backend", "-b"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Listen for the global hotkey and dictate into the focused app."""
    _configure_logging(verbose)
    settings = _load_settings(backend, None)
    if mode:
        settings.trigger_mode = mode

    try:
        require_microphone()
        require_listen_access()
    except PermissionError_ as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        if exc.pane:
            typer.echo("\nOpening System Settings...", err=True)
            open_settings_pane(exc.pane)
        raise typer.Exit(1) from exc

    controller = DictationController(
        settings,
        trigger=trigger,
        on_result=lambda r: typer.secho(r.text, fg=typer.colors.GREEN),
        on_error=lambda e: typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True),
    )

    delivery = controller.inserter.mode

    # Load the model and resolve credentials before arming the hotkey, so a
    # misconfiguration is reported now instead of eating the first dictation.
    try:
        controller.session.warmup(use_vad=settings.trigger_mode == "hold_vad")
    except MissingCredentialError as exc:
        _report_missing_credential(exc)
    except SttError as exc:
        typer.secho(f"Backend is not ready: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if delivery == "clipboard_only" and settings.insert_mode == "paste":
        typer.secho(
            "Accessibility permission is missing, so text will be copied to the "
            "clipboard instead of pasted.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    typer.secho(
        f"Ready. Trigger: {trigger} ({settings.trigger_mode}), "
        f"backend: {settings.backend}, delivery: {delivery}.",
        fg=typer.colors.BLUE,
        err=True,
    )
    typer.secho("Press Ctrl+C to quit.", fg=typer.colors.BLUE, err=True)

    try:
        with controller:
            threading.Event().wait()
    except KeyboardInterrupt:
        typer.secho("\nStopped.", fg=typer.colors.BLUE, err=True)


@app.command()
def menubar(
    backend: Optional[BackendName] = typer.Option(None, "--backend", "-b"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run SkyDict as a menubar app."""
    _configure_logging(verbose)
    settings = _load_settings(backend, None)

    from .ui.menubar import SkyDictApp

    SkyDictApp(settings).run()


@app.command()
def history(
    count: int = typer.Option(10, "--count", "-n", help="How many entries to show."),
    search: Optional[str] = typer.Option(None, "--search", "-s"),
    clear: bool = typer.Option(False, "--clear", help="Delete all entries."),
) -> None:
    """Show recent dictations."""
    store = History()
    if clear:
        store.clear()
        typer.secho("History cleared.", fg=typer.colors.GREEN)
        return

    entries = store.search(search, count) if search else store.recent(count)
    if not entries:
        typer.secho("Nothing recorded yet.", fg=typer.colors.YELLOW)
        return

    for entry in entries:
        stamp = entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
        typer.secho(f"{stamp}  [{entry.backend}]", fg=typer.colors.BLUE, nl=False)
        typer.echo(f"  {entry.text}")


@app.command()
def permissions(
    request: bool = typer.Option(False, "--request", help="Ask macOS for event access."),
) -> None:
    """Show the macOS permissions SkyDict needs and whether they are granted."""
    if request:
        request_accessibility()

    def mark(ok: bool) -> str:
        return typer.style("granted", fg=typer.colors.GREEN) if ok else typer.style(
            "MISSING", fg=typer.colors.RED
        )

    mic = check_microphone()
    typer.echo(f"Microphone:          {mark(mic == 'authorized')}  ({mic})")
    typer.echo(f"Hotkey (listen):     {mark(check_listen_access())}")
    typer.echo(f"Paste (post events): {mark(check_post_access())}")
    typer.echo(f"\nInterpreter: {host_process_name()}")

    if not check_listen_access() or not check_post_access():
        typer.echo(
            "\nGrant Accessibility in System Settings › Privacy & Security › Accessibility.\n"
            "From a terminal, enable the terminal app itself — macOS attributes the\n"
            "permission to the app that launched the process, not to the interpreter.\n"
            "Run `skydict permissions --request` to trigger the system prompt."
        )


@app.command("config")
def show_config() -> None:
    """Show the current settings and where they live."""
    settings = Settings.load()
    path = config_path()
    suffix = "" if path.exists() else "  (not created yet, using defaults)"
    typer.echo(f"Config file: {path}{suffix}")
    typer.echo(f"Sample rate: {SAMPLE_RATE} Hz")
    typer.echo(settings.model_dump_json(indent=2))

    key_status = "set" if get_key(settings.cloud.credential_name) else "MISSING"
    typer.echo(f"\nCloud credential '{settings.cloud.credential_name}': {key_status}")


@app.command("init-config")
def init_config() -> None:
    """Write the default settings file so it can be edited by hand."""
    path = Settings.load().save()
    typer.secho(f"Wrote {path}", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
