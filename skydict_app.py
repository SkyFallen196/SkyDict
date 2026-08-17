"""Entry point for the bundled .app.

py2app needs a plain script to launch, separate from the console entry point, because
inside a bundle there is no terminal to log to and nothing to parse arguments from.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys


def _setup_logging() -> None:
    """Log to a file — a bundled app has no console to print to.

    Kept beside the config so everything SkyDict writes is in one place, and rotated so
    a long-running app cannot fill the disk.
    """
    from skydict.config import config_dir

    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        directory / "skydict.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    _setup_logging()
    log = logging.getLogger("skydict.app")

    try:
        from skydict.ui.menubar import SkyDictApp

        SkyDictApp().run()
    except Exception:
        # A bundled app dies silently otherwise, leaving nothing to diagnose.
        log.exception("SkyDict failed to start")
        raise


if __name__ == "__main__":
    sys.exit(main())
