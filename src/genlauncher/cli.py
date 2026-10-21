"""Command line interface.

Everything here is a thin wrapper over the core, which means the GUI and the
CLI cannot drift apart in behaviour.

    genlauncher detect              find the game and report problems
    genlauncher list                list catalog mods
    genlauncher info NAME           show one mod, with version and download link
    genlauncher install NAME [...]  download and unpack
    genlauncher status              what is installed, what has updates
    genlauncher link NAME [...]     link a selection into the game folder
    genlauncher unlink              remove every link and restore the game
    genlauncher cleanup             crash recovery
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from .core import steam as steam_mod
from .core.catalog import CatalogClient, CatalogError
from .core.config import GAME_FILES, Paths, Settings
from .core.downloader import DownloadError, Downloader
from .core.installer import InstallError, Installer
from .core.linker import LinkStrategy, Linker
from .core.models import ModificationVersion, ModType
from .core.store import ModStore

log = logging.getLogger("genlauncher")


# ---- helpers -------------------------------------------------------------


def _paths() -> Paths:
    paths = Paths()
    paths.ensure()
    return paths


def _settings(paths: Paths | None = None) -> Settings:
    return Settings.load(paths or _paths())


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _pick_install(explicit: str | None):
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_dir():
            raise FileNotFoundError(f"no such game directory: {candidate}")
        from .core.models import GameInstall

        mode = steam_mod.detect_mode(candidate) or "zh"
        return GameInstall(game_dir=candidate, mode=mode)

    installs = steam_mod.find_game_dirs()
    if not installs:
        raise FileNotFoundError(
            "no C&C Generals / Zero Hour install found. "
            "Pass --game-dir to point at it explicitly."
        )
    if len(installs) > 1:
        print("several installs found, using the first one:", file=sys.stderr)
        for i in installs:
            print(f"  {i.game_dir}", file=sys.stderr)
    return installs[0]


def _linker(args, paths: Paths, install) -> Linker:
    settings = _settings(paths)
    return Linker(
        install.game_dir,
        paths.journal_file,
        paths.mods_dir,
        strategy=LinkStrategy.parse(args.strategy or settings.link_strategy),
        game_files=set(GAME_FILES),
    )


def _print_mv(mv: ModificationVersion, marker: str = " ") -> None:
    flag = "*" if mv.installed else marker
    print(f"{flag} {mv.name:<34} {mv.version:<26} {mv.mod_type.value}")


# ---- commands ------------------------------------------------------------


def cmd_detect(args) -> int:
    installs = steam_mod.find_game_dirs() if not args.game_dir else [_pick_install(args.game_dir)]
    if not installs:
        return _fail("no install found; use --game-dir")

    for install in installs:
        print(steam_mod.describe(install))
        problems = steam_mod.verify_install(install)
        print()
        if problems:
            print("problems:")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("no problems found")
        print()

    root = steam_mod.find_steam_root()
    builds = steam_mod.list_proton_builds(root)
    if builds:
        print("proton builds available:")
        for build in builds:
            print(f"  {build.name}")
    return 0


def cmd_list(args) -> int:
    paths = _paths()
    store = ModStore(paths.data_dir)
    installed = {im.key for im in store.all()}

    try:
        with CatalogClient(cache_dir=paths.cache_dir) as client:
            result = client.load_root(args.game)
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)

            for entry in result.catalog.mod_datas:
                marker = "*" if entry.mod_name.casefold() in installed else " "
                print(f"{marker} {entry.mod_name}")
            print()
            print(f"launcher version: {result.catalog.launcher_version or '(unknown)'}")
            link = result.catalog.download_link
            if link:
                print(f"launcher download: {link}")
    except CatalogError as exc:
        return _fail(str(exc))
    return 0


def cmd_info(args) -> int:
    paths = _paths()
    try:
        with CatalogClient(cache_dir=paths.cache_dir) as client:
            mv = _find_mod(client, args.name, args.game)
    except CatalogError as exc:
        return _fail(str(exc))

    if mv is None:
        return _fail(f"mod not found in the catalog: {args.name}")

    print(f"name        : {mv.name}")
    print(f"version     : {mv.version}")
    print(f"type        : {mv.mod_type.value}")
    print(f"download    : {mv.simple_download_link or '(none)'}")
    print(f"depends on  : {mv.dependence_name or '(none)'}")
    print(f"news        : {mv.news_link or '(none)'}")
    print(f"support     : {mv.support_link or '(none)'}")
    if mv.exception_names:
        print(f"exceptions  : {', '.join(mv.exception_names)}")
    if mv.additional_file_names:
        print(f"additional  : {', '.join(mv.additional_file_names)}")
    if mv.executable_file_name:
        print(f"exe         : {mv.executable_file_name}")

    store = ModStore(paths.data_dir)
    known = store.find_version(mv.name, mv.version)
    print(f"installed   : {'yes' if known and known.installed else 'no'}")
    return 0


def _find_mod(client: CatalogClient, name: str, game: str) -> ModificationVersion | None:
    result = client.load_root(game)
    wanted = name.casefold()
    for entry in result.catalog.mod_datas:
        if entry.mod_name.casefold() != wanted:
            continue
        if entry.mod_link:
            return client.load_mod_manifest(entry.mod_link)
        return ModificationVersion(
            name=entry.mod_name,
            version=result.catalog.launcher_version or "",
            mod_type=ModType.MOD,
            dependence_name="",
            simple_download_link=result.catalog.download_link or "",
        )
    return None


def cmd_install(args) -> int:
    paths = _paths()
    store = ModStore(paths.data_dir)
    installer = Installer(paths.mods_dir)

    with CatalogClient(cache_dir=paths.cache_dir) as client:
        targets: list[ModificationVersion] = []
        for name in args.names:
            mv = _find_mod(client, name, args.game)
            if mv is None:
                return _fail(f"mod not found in the catalog: {name}")
            targets.append(mv)

    for mv in targets:
        if not mv.simple_download_link:
            print(f"skipping {mv.name}: no download link in the catalog")
            continue
        try:
            print(f"installing {mv.name} {mv.version}")
            with Downloader() as downloader:
                archive = downloader.download_to_cache(
                    mv.simple_download_link, paths.cache_dir
                )
            folder = installer.install(
                mv, archive, progress=lambda msg: print(f"  {msg}")
            )
        except (DownloadError, InstallError) as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            continue

        mv.installed = True
        store.upsert(mv, paths.mods_dir)
        print(f"  installed to {folder}")
    return 0


def cmd_status(args) -> int:
    paths = _paths()
    store = ModStore(paths.data_dir)

    print(f"config      : {paths.config_dir}")
    print(f"data        : {paths.data_dir}")
    print(f"mod store   : {paths.mods_dir}")
    print()

    mods = store.all()
    if not mods:
        print("nothing installed yet")
        return 0

    print("installed:")
    for im in mods:
        print(f"  {im.name}")
        for mv in im.sorted_versions():
            print(f"    {mv.version}  {'(on disk)' if mv.is_installed(paths.mods_dir) else '(missing)'}")
    print()

    if not args.offline:
        try:
            with CatalogClient(cache_dir=paths.cache_dir) as client:
                root = client.load_root(args.game)
                catalog_names = {e.mod_name.casefold() for e in root.catalog.mod_datas}
                installed_names = {im.key for im in mods}
                in_catalog = installed_names & catalog_names
            if in_catalog:
                print("installed mods available in catalog (run 'genlauncher info <name>' to check versions):")
                for name in sorted(in_catalog):
                    print(f"  {name}")
        except CatalogError as exc:
            print(f"could not check catalog: {exc}", file=sys.stderr)
    return 0


def cmd_adopt(args) -> int:
    """Import mod files already sitting in the game folder into the store.

    This is the path for installs that were done by hand, by the Windows
    launcher, or by an unpacked archive: the files are already in the right place,
    so we copy them into the store instead of downloading them again.
    """
    paths = _paths()
    try:
        install = _pick_install(args.game_dir)
    except FileNotFoundError as exc:
        return _fail(str(exc))

    store = ModStore(paths.data_dir)
    installer = Installer(paths.mods_dir)

    source = Path(args.folder).expanduser() if args.folder else install.game_dir
    if not source.is_dir():
        return _fail(f"not a directory: {source}")

    mv = ModificationVersion(
        name=args.name,
        version=args.version,
        mod_type=ModType(args.type),
        dependence_name=args.for_mod or "",
        installed=True,
        manual=True,
    )

    # Mods prefix their own archives with '!' (!TEOD_*.big, !npm_*.big). Defaulting
    # to that avoids sweeping up the game's own archives, which are not mod content.
    patterns = args.pattern or ["!*.big"]
    files = sorted({p for pattern in patterns for p in source.glob(pattern) if p.is_file()})
    if not files:
        return _fail(f"no files matching {patterns} in {source}")

    if not args.yes:
        total = sum(p.stat().st_size for p in files)
        print(f"about to copy {len(files)} file(s), {total / 1048576:.0f} MB, from {source}")
        for p in files[:15]:
            print(f"  {p.name}")
        if len(files) > 15:
            print(f"  ... and {len(files) - 15} more")
        if input("continue? [y/N] ").strip().casefold() != "y":
            print("cancelled")
            return 1

    # Collect into a scratch dir first: install_directory() owns (and clears) its
    # own staging path, so the two must not be the same directory.
    scratch = Path(tempfile.mkdtemp(prefix="genlauncher-adopt-"))
    try:
        for p in files:
            # Copy rather than move: the game folder must stay playable.
            shutil.copy2(p, scratch / p.name)
        folder = installer.install_directory(mv, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    store.upsert(mv, paths.mods_dir)
    print(f"adopted {len(files)} file(s) into {folder}")
    print("the game folder was left untouched; use 'link' to switch to it")
    return 0


def cmd_link(args) -> int:
    paths = _paths()
    store = ModStore(paths.data_dir)
    try:
        install = _pick_install(args.game_dir)
    except FileNotFoundError as exc:
        return _fail(str(exc))

    linker = _linker(args, paths, install)

    # A leftover journal means a previous session died; say so rather than
    # silently overwriting it.
    if linker.has_pending_links:
        print("previous links found, cleaning them up first")

    versions: list[ModificationVersion] = []
    for name in args.names:
        im = store.get(name)
        if im is None:
            return _fail(f"not installed: {name}")
        if args.version:
            mv = im.versions.get(args.version)
            if mv is None:
                return _fail(f"{name} has no version {args.version}")
            versions.append(mv)
        else:
            latest = im.latest
            if latest is None:
                return _fail(f"{name} has no installed versions")
            versions.append(latest)

    report = linker.link(versions, link_empty_bigs=args.link_empty_bigs)
    print(f"linked {len(report.linked)} item(s) into {install.game_dir}")
    for skipped in report.skipped:
        print(f"  skipped: {skipped}")
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_unlink(args) -> int:
    paths = _paths()
    try:
        install = _pick_install(args.game_dir)
    except FileNotFoundError as exc:
        return _fail(str(exc))

    report = _linker(args, paths, install).unlink()
    print(f"removed {len(report.replaced_originals)} parked file(s) from {install.game_dir}")
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_cleanup(args) -> int:
    paths = _paths()
    try:
        install = _pick_install(args.game_dir)
    except FileNotFoundError as exc:
        return _fail(str(exc))

    linker = _linker(args, paths, install)
    report = linker.cleanup()
    print(f"restored {len(report.replaced_originals)} file(s) in {install.game_dir}")
    for error in report.errors:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if report.ok else 1


def cmd_verify(args) -> int:
    """Prove the game folder is in its original state: no links, no parked files."""
    paths = _paths()
    try:
        install = _pick_install(args.game_dir)
    except FileNotFoundError as exc:
        return _fail(str(exc))

    linker = _linker(args, paths, install)
    leftovers = [
        rel
        for rel, kind in linker.snapshot().items()
        if kind.startswith("link ") or rel.endswith((".GLR", ".GOF", ".GLTC"))
    ]
    if not linker.has_pending_links and not leftovers:
        print("clean: no launcher links or parked files in", install.game_dir)
        return 0

    print("not clean:", file=sys.stderr)
    if linker.has_pending_links:
        print(f"  journal present: {paths.journal_file}", file=sys.stderr)
    for rel in leftovers[:50]:
        print(f"  {rel}", file=sys.stderr)
    if len(leftovers) > 50:
        print(f"  ... and {len(leftovers) - 50} more", file=sys.stderr)
    return 1


def cmd_gui(args) -> int:
    try:
        from .ui.app import run_gui
    except ImportError:
        return _fail("PySide6 is required for the GUI. Install with: pip install genlauncher-linux[gui]")
    raise SystemExit(run_gui())


def cmd_paths(args) -> int:
    paths = _paths()
    useful = {
        "config": paths.config_dir,
        "settings": paths.settings_file,
        "data": paths.data_dir,
        "mod store": paths.mods_dir,
        "cache": paths.cache_dir,
        "images": paths.images_dir,
        "link journal": paths.journal_file,
        "state": paths.state_dir,
        "log": paths.log_file,
    }
    print(json.dumps({k: str(v) for k, v in useful.items()}, indent=2))
    return 0


# ---- argument parsing ----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genlauncher",
        description="Launcher for C&C Generals and Zero Hour mods on Linux.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument(
        "--game",
        default="zh",
        choices=("zh", "generals"),
        help="which game's catalog to use (default: zh)",
    )
    parser.add_argument("--game-dir", help="path to the game folder (skip autodetection)")
    parser.add_argument(
        "--strategy",
        choices=("symlink", "hardlink", "copy"),
        help="how to link mod files into the game folder (default: symlink)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="find the game install and report problems").set_defaults(
        func=cmd_detect
    )
    sub.add_parser("paths", help="show the XDG paths in use").set_defaults(func=cmd_paths)

    p_list = sub.add_parser("list", help="list mods in the catalog")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", help="show one mod")
    p_info.add_argument("name")
    p_info.set_defaults(func=cmd_info)

    p_install = sub.add_parser("install", help="download and unpack mods")
    p_install.add_argument("names", nargs="+")
    p_install.set_defaults(func=cmd_install)

    p_status = sub.add_parser("status", help="show installed mods and updates")
    p_status.add_argument("--offline", action="store_true", help="do not check for updates")
    p_status.set_defaults(func=cmd_status)

    p_adopt = sub.add_parser(
        "adopt", help="import mod files already in the game folder into the store"
    )
    p_adopt.add_argument("name", help="mod name to record")
    p_adopt.add_argument("--version", default="manual", help="version label")
    p_adopt.add_argument(
        "--type",
        default="Mod",
        choices=("Mod", "Patch", "Addon", "Executable", "Advertising"),
        help="what kind of entry this is",
    )
    p_adopt.add_argument("--for-mod", help="dependence name, for a patch or addon")
    p_adopt.add_argument("--folder", help="source folder (default: the game folder)")
    p_adopt.add_argument(
        "--pattern",
        action="append",
        help="glob to pick up, repeatable (default: !*.big)",
    )
    p_adopt.add_argument("-y", "--yes", action="store_true", help="do not ask")
    p_adopt.set_defaults(func=cmd_adopt)

    p_link = sub.add_parser("link", help="link a mod selection into the game folder")
    p_link.add_argument("names", nargs="+")
    p_link.add_argument("--version", help="link this version instead of the newest")
    p_link.add_argument(
        "--link-empty-bigs",
        action="store_true",
        help="also link 24-byte placeholder .big files",
    )
    p_link.set_defaults(func=cmd_link)

    sub.add_parser("unlink", help="remove every link and restore the game").set_defaults(
        func=cmd_unlink
    )
    sub.add_parser("cleanup", help="recover from an interrupted session").set_defaults(
        func=cmd_cleanup
    )
    sub.add_parser("verify", help="check the game folder has no launcher leftovers").set_defaults(
        func=cmd_verify
    )
    sub.add_parser("gui", help="launch the graphical interface").set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())