"""Application entry point for the PySide6 GUI."""

from __future__ import annotations

import fcntl
import logging
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ..core.config import GENLAUNCHER_SUPPORT, Paths, Settings
from ..core.link_overrides import LinkOverrides
from .info_dialog import InfoDialog
from .resources import apply_app_icon
from .theme import generate_qss

log = logging.getLogger("genlauncher.ui")


def _acquire_single_instance_lock() -> object | None:
    """Prevent multiple launcher instances via a lock file.

    The lock lives in the per-user runtime directory rather than /tmp, which is
    shared: on a multi-user machine two people running the launcher would
    otherwise lock each other out, and either could truncate the other's file.

    Returns the open file handle (must stay alive) or None if already running.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path(tempfile.gettempdir())
    lock_path = base / "genlauncher-linux.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # "a" not "w": opening must not truncate a live instance's lock file.
        fh = open(lock_path, "a")
    except OSError:
        return None
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return fh


def _install_exception_hook():
    """Install a global exception handler that shows a dialog instead of crashing."""
    import traceback

    _original = sys.excepthook

    def handler(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical("Unhandled exception:\n%s", msg)
        try:
            app = QApplication.instance()
            if app:
                InfoDialog.critical(
                    None,
                    "GenLauncher — Error",
                    f"An unexpected error occurred:\n\n{exc_value}\n\n"
                    f"Details have been written to {Paths().log_file}\n\n"
                    f"Report: {GENLAUNCHER_SUPPORT}",
                )
        except Exception:
            pass
        _original(exc_type, exc_value, exc_tb)

    sys.excepthook = handler


def _select_game_if_needed(settings: Settings) -> None:
    """Settle which title to manage before the catalog is fetched.

    The catalog URL, the store and the launch target are all keyed off
    ``settings.game``, so this has to happen before the splash starts loading.
    With only one title installed there is nothing to ask.
    """
    from pathlib import Path as _Path

    from ..core import steam as steam_mod
    from .game_selection_window import GameSelectionWindow, distinct_modes

    installs = steam_mod.find_game_dirs()
    modes = distinct_modes(installs)
    log.debug("game installs found: %s", [(i.mode, str(i.game_dir)) for i in installs])

    if len(modes) < 2:
        if len(modes) == 1 and settings.game not in modes:
            # Nothing to choose, but the stored setting may point at a title
            # that was never installed or has since been removed.
            log.info("game setting was %r; only %r is installed", settings.game, modes[0])
            settings.game = modes[0]
            settings.save()
        return

    dialog = GameSelectionWindow(installs, current=settings.game)
    dialog.exec()
    chosen = dialog.selected_mode
    if chosen == settings.game:
        return

    # A pinned path to the *other* title would win over the new setting in
    # _resolve_install, so drop it unless it really is the chosen game.
    if settings.game_dir and steam_mod.detect_mode(
        _Path(settings.game_dir).expanduser()
    ) != chosen:
        log.info("clearing game_dir %s: it is not a %s install", settings.game_dir, chosen)
        settings.game_dir = ""

    settings.game = chosen
    settings.save()
    log.info("managing %r from now on", chosen)


def run_gui(argv: list[str] | None = None) -> int:
    """Launch the GenLauncher GUI."""
    from logging.handlers import RotatingFileHandler

    log_dir = Paths().data_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "genlauncher.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8",
            ),
        ],
    )
    logging.getLogger("genlauncher").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _install_exception_hook()

    lock = _acquire_single_instance_lock()
    if lock is None:
        # Already running — show message and exit
        tmp_app = QApplication(argv or sys.argv)
        apply_app_icon(tmp_app)
        InfoDialog.warning(
            None,
            "GenLauncher",
            "GenLauncher is already running.",
        )
        return 1

    app = QApplication(argv or sys.argv)
    app.setApplicationName("GenLauncher-LP")
    # Without an organisation name the WM_CLASS on Wayland/X11 is just the
    # binary name, so the task manager never matches our icon.
    app.setApplicationDisplayName("GenLauncher-LP")
    app.setOrganizationName("GenLauncher-LP")
    apply_app_icon(app)
    app.setStyleSheet(generate_qss())

    paths = Paths()
    paths.ensure()
    settings = Settings.load(paths)

    if not settings.setup_done:
        from .setup_wizard import SetupWizard

        wizard = SetupWizard(settings)
        if wizard.exec() != SetupWizard.DialogCode.Accepted:
            return 0

    _select_game_if_needed(settings)

    from .main_window import MainWindow

    main_window = MainWindow(
        paths=paths,
        settings=settings,
        catalog_result=None,
    )
    main_window.show()

    # Catalog loads in the background; MainWindow handles the result.
    main_window.start_catalog_load()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run_gui(sys.argv[1:]))
