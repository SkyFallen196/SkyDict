# SkyDict

Dictation App. Free, Simple, Open-source

A SuperWhisper-style dictation tool for macOS: hold a hotkey, speak, and the recognised
text lands in whatever you were typing into. Speech recognition runs either through any
OpenAI-compatible endpoint (Groq, OpenAI, a self-hosted vLLM) or fully offline on a local
Hugging Face model such as GigaAM v3.

> **Status:** stage 4 — builds into a standalone `SkyDict.app`. LLM post-processing
> ("modes") is the remaining piece.

## Install

Requires macOS 11+ on Apple Silicon.

Build the app:

```bash
./build.sh --install
```

That produces `SkyDict.app`, signs it and copies it to `/Applications`. Launch it and a
🎙 appears in the menubar.

To work on the code instead, install it in a Python 3.10+ environment:

```bash
conda activate SkyDict
pip install -e ".[dev,macos]"
```

## Configure

Store the API key for the cloud backend in the macOS Keychain:

```bash
skydict set-key groq
```

Write out the settings file so it can be edited by hand:

```bash
skydict init-config
```

Settings live in `~/Library/Application Support/SkyDict/config.json`. The defaults point
at Groq's `whisper-large-v3-turbo` for the cloud backend and `gigaam-v3-e2e-rnnt` for the
local one. Because the cloud backend takes its base URL from settings, pointing `base_url`
at `https://api.openai.com/v1` or a local server works without any code change.

API keys are never written to that file. They are read from the Keychain, or from
`SKYDICT_<NAME>_API_KEY` in the environment when that is set.

## Use

Run it as a menubar app — status icon, backend and trigger switching, settings window
and history all in one place:

```bash
skydict menubar
```

Or headless, without the menubar. Hold **right Option**, speak, release — the text is
pasted where your cursor is:

```bash
skydict listen
```

`--mode toggle` switches to press-once-to-start, press-again-to-stop; `--mode hold_vad`
also ends the recording on silence. `--trigger left_option|fn|right_command|…` picks a
different key.

```bash
skydict history                     # recent dictations
skydict history --search молоко     # find one
skydict permissions                 # check what macOS has granted
skydict permissions --request       # trigger the system prompt
skydict devices                     # list microphones
skydict check                       # verify the configured backend is reachable
skydict config                      # show current settings

skydict record --seconds 5          # record for 5 seconds, print the text
skydict record --vad                # record until you stop talking
skydict record                      # record until Ctrl+C

skydict transcribe -f audio.wav     # transcribe an existing 16-bit PCM WAV
```

Any command takes `--backend cloud|local` and `--model NAME` to override the configured
choice for a single run:

```bash
skydict transcribe -f audio.wav --backend local --model gigaam-v3-e2e-rnnt
```

The first local run downloads the ONNX weights from Hugging Face into **`~/SkyDict_models`**
— out in the open rather than buried in `~/Library`, so it is obvious what has been
downloaded and trivial to reclaim the space by deleting the folder. `skydict check
--backend local` downloads ahead of time, and `skydict config` reports the path and how
much is on disk.

The default is the int8 build (~230 MB, noticeably faster on CPU); set
`local.quantization` to `null` in the config for the full-precision one (~1 GB).

Set `SKYDICT_MODELS_DIR` to put models elsewhere. An `HF_HOME` already set in your
environment wins over both — if you have pointed your whole machine at another disk,
SkyDict follows.

## Permissions

**Microphone** — prompted automatically on first recording.

**Accessibility** — needed to watch for the hotkey and to paste. Without it SkyDict falls
back to leaving the text on the clipboard, and says so.

Enable **SkyDict** in System Settings › Privacy & Security › Accessibility, then restart
it. Running from a terminal instead of the built app, enable the *terminal app* there:
macOS attributes the permission to whatever launched the process, and a bare script has
no identity of its own.

The build signs the bundle ad-hoc, which macOS requires before it will load the bundled
libraries at all. Be aware that an ad-hoc signature changes with every build: macOS
identifies the app by the hash of its contents, so **after each rebuild the Accessibility
grant has to be renewed** — toggle SkyDict off and on in that list.

To stop that, sign with a self-signed certificate instead. Create one once in Keychain
Access (Certificate Assistant › Create a Certificate, type "Code Signing"), then build
with its name:

```bash
SIGN_IDENTITY="My SkyDict Cert" ./build.sh
```

The identity stays the same across builds, and so does the permission.

## Backends

**Cloud** — any OpenAI-compatible `/v1/audio/transcriptions` endpoint. Retries with
exponential backoff on 429 and 5xx, honouring the server's `Retry-After`.

**Local** — [onnx-asr](https://github.com/istupakov/onnx-asr), which needs only numpy and
onnxruntime: no torch, no ffmpeg. It serves GigaAM, Whisper, Parakeet, Vosk and T-one.
The GigaAM `e2e` variants return punctuated, normalised Russian text directly.

## Development

```bash
pytest              # 152 fast tests, no network, microphone or permissions
pytest -m slow      # 6 more against the real GigaAM and Silero weights
ruff check .
```

The fast suite fakes `sounddevice`, `Quartz` and the ONNX sessions, so it needs no
hardware and no granted permissions. AppKit and rumps are *not* faked — they work
headlessly, and stubbing them hid two real bugs (a lazily-created submenu and a
process-global Objective-C class name). Every test also runs against a temporary
Application Support directory, so none can overwrite your own config or history.
`tests/data/sample_ru.wav` is Russian speech generated with the macOS speech synthesiser;
regenerate it with:

```bash
say -v Milena -o /tmp/sample.aiff "Привет! Это тестовая запись для проверки распознавания речи в приложении SkyDict." && afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/sample.aiff tests/data/sample_ru.wav
```

## License

MIT
