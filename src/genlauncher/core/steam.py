"""Steam and Proton discovery.

Autodetects game installations by looking for executables in every Steam library.
Case sensitivity matters: the real install has `Generals.exe` with a capital G,
so every lookup in this module is case-insensitive.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import GAME_EXE_CANDIDATES, WORLD_BUILDER_EXE_CANDIDATES
from .models import GameInstall

log = logging.getLogger(__name__)

STEAM_ROOTS = (
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.steam/root",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",  # Flatpak
)

# Files that only exist in one of the two games, used to tell them apart.
ZH_MARKERS = ("windowzh.big", "inzh.big", "genseczh.big", "patchzh.big")
GENERALS_MARKERS = ("window.big", "ini.big", "gensec.big", "patch.big")

REQUIRED_SHARED = ("generals.exe", "binkw32.dll")

# Vendor app ids for the C&C titles, used only to *label* a detection, never to
# assume a folder name.
KNOWN_APPIDS = {
    "2229850": "Command & Conquer: Red Alert 2",
    "2229870": "Command & Conquer: Generals",
    "2732960": "Command & Conquer Generals: Zero Hour",
}


class SteamError(RuntimeError):
    pass


@dataclass
class ProtonBuild:
    name: str
    path: Path

    @property
    def proton_script(self) -> Path:
        return self.path / "proton"

    def is_valid(self) -> bool:
        return self.proton_script.is_file()


def find_steam_root() -> Path | None:
    """First existing Steam installation."""
    for candidate in STEAM_ROOTS:
        path = Path(candidate).expanduser()
        if (path / "steamapps").is_dir():
            return path
    return None


def find_steam_roots() -> list[Path]:
    """Every distinct Steam installation.

    Deduplicated by resolved path: on a normal Linux install ~/.steam/steam and
    ~/.steam/root are symlinks into ~/.local/share/Steam, and reporting the same
    install three times would show the user duplicate games.
    """
    out: list[Path] = []
    seen: set[Path] = set()
    for candidate in STEAM_ROOTS:
        path = Path(candidate).expanduser()
        if not (path / "steamapps").is_dir():
            continue
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def parse_library_folders(steam_root: Path) -> list[Path]:
    """All library folders, from libraryfolders.vdf with a sane fallback."""
    steam_root = Path(steam_root)
    libraries: list[Path] = [steam_root]

    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        # Entries look like:  "path"  "/mnt/games/SteamLibrary"
        found = re.findall(r'"path"\s+"([^"]+)"', text)
        for raw in found:
            path = Path(raw.replace("\\\\", "\\"))
            if (path / "steamapps").is_dir() and path not in libraries:
                libraries.append(path)
    return libraries


def parse_appmanifest(acf: Path) -> dict:
    """Pull the few fields we need out of an appmanifest_*.acf."""
    try:
        text = acf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    def field(name: str) -> str:
        m = re.search(rf'"{name}"\s+"([^"]*)"', text)
        return m.group(1) if m else ""

    return {
        "appid": field("appid"),
        "name": field("name"),
        "installdir": field("installdir"),
        "stateflags": field("StateFlags"),
    }


def find_game_dirs() -> list[GameInstall]:
    """Every install that looks like Generals or Zero Hour, deduplicated."""
    found: list[GameInstall] = []
    seen: set[Path] = set()

    for root in find_steam_roots():
        for library in parse_library_folders(root):
            steamapps = library / "steamapps"
            if not steamapps.is_dir():
                continue

            # 1. Trust the manifests.
            for acf in sorted(steamapps.glob("appmanifest_*.acf")):
                info = parse_appmanifest(acf)
                install_dir = info.get("installdir", "")
                if not install_dir:
                    continue
                candidate = steamapps / "common" / install_dir
                if not candidate.is_dir():
                    continue
                mode = detect_mode(candidate)
                if mode is None or _already_seen(candidate, seen):
                    continue
                seen.add(candidate.resolve())
                found.append(
                    GameInstall(
                        game_dir=candidate,
                        appid=info.get("appid", ""),
                        steam_root=root,
                        library=library,
                        mode=mode,
                    )
                )

            # 2. Fall back to scanning common/ for orphaned copies.
            common = steamapps / "common"
            if common.is_dir():
                for candidate in sorted(p for p in common.iterdir() if p.is_dir()):
                    mode = detect_mode(candidate)
                    if mode is None or _already_seen(candidate, seen):
                        continue
                    seen.add(candidate.resolve())
                    found.append(
                        GameInstall(
                            game_dir=candidate,
                            appid="",
                            steam_root=root,
                            library=library,
                            mode=mode,
                        )
                    )
    return found


def pick_install(
    installs: list[GameInstall], game: str
) -> GameInstall | None:
    """The install that belongs to ``game``.

    Both titles can be installed at once, so the caller has to say which one it
    means. Falls back to the first install when nothing matches: a stale or
    wrong setting must never end up as "no install found" at launch time.
    """
    if not installs:
        return None
    matching = [i for i in installs if i.mode == game]
    return (matching or installs)[0]


def _already_seen(candidate: Path, seen: set[Path]) -> bool:
    try:
        return candidate.resolve() in seen
    except OSError:
        return False


def detect_mode(game_dir: Path) -> str | None:
    """'zh', 'generals', or None if this is not a C&C Generals install."""
    game_dir = Path(game_dir)
    if not game_dir.is_dir():
        return None

    names = {p.name.casefold() for p in game_dir.iterdir()}
    has_exe = any(casefold_present(names, c) for c in GAME_EXE_CANDIDATES)
    if not has_exe:
        return None

    if any(name in names for name in ZH_MARKERS):
        return "zh"
    if any(name in names for name in GENERALS_MARKERS):
        return "generals"

    # Has the executable but no marker .big files: likely a partial install.
    return None


def casefold_present(names: set[str], wanted: str) -> bool:
    return wanted.casefold() in names


def resolve_in_folder(folder: Path, filename: str) -> Path | None:
    """Find `filename` inside `folder`, ignoring case.

    Case-insensitive file lookup — necessary on Linux where
    the filesystem is case-sensitive.
    """
    folder = Path(folder)
    direct = folder / filename
    if direct.exists():
        return direct
    wanted = filename.casefold()
    try:
        for entry in folder.iterdir():
            if entry.name.casefold() == wanted:
                return entry
    except OSError:
        return None
    return None


def resolve_game_exe(game_dir: Path, mode: str = "") -> Path | None:
    """Find the best game executable for *mode*.

    When *mode* is given, the search order comes from
    ``executables.BUILT_IN_GAME_CLIENTS[mode]`` so that ZH mode prefers
    ``generalszh.exe`` (community-patched) while Generals mode prefers
    ``generals.exe``.  Without a mode, falls back to the generic
    ``GAME_EXE_CANDIDATES`` list.
    """
    if mode:
        from .executables import BUILT_IN_GAME_CLIENTS
        candidates = BUILT_IN_GAME_CLIENTS.get(mode, GAME_EXE_CANDIDATES)
    else:
        candidates = GAME_EXE_CANDIDATES
    for candidate in candidates:
        found = resolve_in_folder(game_dir, candidate)
        if found:
            return found
    return None


def resolve_world_builder_exe(game_dir: Path) -> Path | None:
    for candidate in WORLD_BUILDER_EXE_CANDIDATES:
        found = resolve_in_folder(game_dir, candidate)
        if found:
            return found
    return None


def find_compat_data(install: GameInstall) -> Path | None:
    """The Proton prefix directory, if it exists."""
    cd = install.compat_data
    return cd if cd and cd.is_dir() else None


def list_proton_builds(steam_root: Path | None = None) -> list[ProtonBuild]:
    """Proton builds Steam can use: official ones plus anything in compatibilitytools.d."""
    roots = [steam_root] if steam_root else find_steam_roots()
    builds: list[ProtonBuild] = []

    for root in roots:
        root = Path(root)
        common = root / "steamapps" / "common"
        if common.is_dir():
            for path in sorted(p for p in common.iterdir() if p.is_dir()):
                name = path.name
                if "proton" in name.casefold():
                    builds.append(ProtonBuild(name=name, path=path))

        tools = root / "compatibilitytools.d"
        if tools.is_dir():
            for path in sorted(p for p in tools.iterdir() if p.is_dir()):
                builds.append(ProtonBuild(name=path.name, path=path))

    return [b for b in builds if b.is_valid()]


def steam_executable() -> str | None:
    """Locate a usable Steam client binary."""
    for candidate in ("steam", "steam.sh"):
        found = shutil.which(candidate)
        if found:
            return found
    for root in find_steam_roots():
        script = root / "steam.sh"
        if script.is_file():
            return str(script)
    return None


def verify_install(install: GameInstall) -> list[str]:
    """Sanity-check an install. Returns a list of human-readable problems."""
    problems: list[str] = []
    game_dir = install.game_dir

    if not game_dir.is_dir():
        problems.append(f"game directory does not exist: {game_dir}")
        return problems

    if resolve_game_exe(game_dir) is None:
        problems.append("Generals.exe not found (checked case-insensitively)")

    if resolve_in_folder(game_dir, "BINKW32.DLL") is None:
        problems.append("BINKW32.DLL not found - this does not look like a C&C install")

    # Writable? We need to create links inside it.
    probe = game_dir / ".genlauncher-write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        problems.append(f"game directory is not writable: {exc}")

    if not supports_symlinks(game_dir):
        problems.append(
            "game directory does not support symlinks (NTFS/FAT or a flatpak/snap sandbox). "
            "Switch the link strategy to 'copy' in settings."
        )

    if install.appid and not find_compat_data(install):
        problems.append(
            "no Proton prefix yet - launch the game once from Steam, or use direct Proton launch"
        )

    return problems


def supports_symlinks(folder: Path) -> bool:
    folder = Path(folder)
    target = folder / ".genlauncher-symlink-test"
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        target.symlink_to(folder)
        ok = target.is_symlink()
    except OSError:
        return False
    finally:
        try:
            target.unlink()
        except OSError:
            pass
    return ok


def supports_hardlinks(folder: Path, other: Path | None = None) -> bool:
    folder = Path(folder)
    src = folder / ".genlauncher-hardlink-src"
    dst = folder / ".genlauncher-hardlink-dst"
    for p in (src, dst):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        src.write_text("x")
        os.link(src, dst)
        return True
    except OSError:
        return False
    finally:
        for p in (src, dst):
            try:
                p.unlink()
            except OSError:
                pass


def describe(install: GameInstall) -> str:
    lines = [
        f"game mode   : {install.mode}",
        f"game dir    : {install.game_dir}",
        f"appid       : {install.appid or '(not found in manifests)'}",
        f"steam root  : {install.steam_root or '(none)'}",
        f"library     : {install.library or '(none)'}",
    ]
    exe = resolve_game_exe(install.game_dir)
    lines.append(f"game exe    : {exe.name if exe else '(not found)'}")
    wb = resolve_world_builder_exe(install.game_dir)
    lines.append(f"worldbuilder: {wb.name if wb else '(not found)'}")
    prefix = install.prefix
    lines.append(f"proton pfx  : {prefix if prefix and prefix.is_dir() else '(none yet)'}")
    return "\n".join(lines)