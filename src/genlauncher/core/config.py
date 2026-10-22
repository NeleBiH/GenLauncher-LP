"""XDG paths and user settings.

Kept free of Qt and of any network access so the core stays testable headless.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "genlauncher"

# Folder names inside the mod store, matching the Rewritten launcher layout.
ADDONS_DIR = "Addons"
PATCHES_DIR = "Patches"
EXECUTABLES_DIR = "Executables"

# Suffixes used to move files aside instead of deleting them.
REPLACE_SUFFIX = ".GLR"  # game file parked during a link session
ORIGINAL_SUFFIX = ".GOF"  # backup taken before the CameraHeight BIG patch
TEMP_SUFFIX = ".GLTC"  # temporary copy of a version folder

# The Steam client dumps Zero Hour common files into this subfolder of the game
# directory. Referenced by name only, never as an absolute path.
STEAM_COMMON_SUBDIR = "ZH_Generals"

GAME_EXE_CANDIDATES = ("generals.exe",)
WORLD_BUILDER_EXE_CANDIDATES = ("worldbuilder.exe", "worldbuildernt27.exe")

# Support link for error dialogs.
GENLAUNCHER_SUPPORT = "https://github.com/NeleBiH/GenLauncher-LP/issues"

# Extensions treated as "custom" files: a mod overwrites them instead of
# symlinking, so they get parked with REPLACE_SUFFIX rather than shadowed by a link.
CUSTOM_FILE_EXTENSIONS = frozenset(
    {".w3d", ".dds", ".tga", ".ini", ".scb", ".wnd", ".csf", ".str"}
)

# Archives that ship with the game. The linker must never park these: they are not
# mod content, so renaming them would break the vanilla install.
# Known vanilla game archives that the linker must not touch.
GAME_FILES = frozenset(
    {
        # Zero Hour
        "audiochinesezh.big", "audioenglishzh.big", "audiofrenchzh.big",
        "audiogermanzh.big", "audioitalianzh.big", "audiokoreanzh.big",
        "audiopolishzh.big", "audiospanishzh.big", "audiozh.big",
        "brazilianzh.big", "chinesezh.big", "englishzh.big", "frenchzh.big",
        "germanzh.big", "italianzh.big", "koreanzh.big", "polishzh.big",
        "spanishzh.big", "genseczh.big", "inizh.big", "mapszh.big",
        "music.big", "musiczh.big", "patchzh.big", "shaderszh.big",
        "speechbrazilianzh.big", "speechchinesezh.big", "speechenglishzh.big",
        "speechfrenchzh.big", "speechgermanzh.big", "speechitalianzh.big",
        "speechkoreanzh.big", "speechpolishzh.big", "speechspanishzh.big",
        "speechzh.big", "terrainzh.big", "textureszh.big",
        "w3denglishzh.big", "w3dgermanzh.big", "w3dchinesezh.big", "w3dgerman2zh.big",
        "w3ditalianzh.big", "w3dkoreanzh.big", "w3dpolishzh.big",
        "w3dspanishzh.big", "w3dzh.big", "windowzh.big",
        "patchdata.big", "patchini.big", "patchwindow.big",
        # Generals
        "audio.big", "audiobrazilian.big", "audiochinese.big", "audioenglish.big",
        "audiofrench.big", "audiogerman.big", "audiogerman2.big", "audioitalian.big",
        "audiokorean.big", "audiopolish.big", "audiospanish.big",
        "brazilian.big", "chinese.big", "english.big", "french.big",
        "german.big", "german2.big", "italian.big", "korean.big", "polish.big",
        "spanish.big", "gensec.big", "ini.big", "maps.big", "patch.big",
        "shaders.big", "speech.big", "speechbrazilian.big", "speechchinese.big",
        "speechenglish.big", "speechfrench.big", "speechgerman.big",
        "speechgerman2.big", "speechitalian.big", "speechkorean.big",
        "speechpolish.big", "speechspanish.big", "terrain.big", "textures.big",
        "w3d.big", "w3dchinese.big", "w3dgerman2.big", "w3ditalian.big",
        "w3dkorean.big", "w3dpolish.big", "w3dspanish.big", "window.big",
    }
)


def _xdg(env_var: str, default: Path) -> Path:
    raw = os.environ.get(env_var)
    if raw and raw.strip():
        p = Path(raw.strip())
        # Spec says trailing slashes are insignificant; normalise so we never
        # build paths like "~/.config/genlauncher//mods".
        return p if p.is_absolute() else Path.home() / p
    return default


class Paths:
    """Resolved XDG locations. Computed once, overridable for tests."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = env if env is not None else os.environ
        self._config = _xdg("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME
        self._data = _xdg("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_NAME
        self._state = _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME

    @property
    def config_dir(self) -> Path:
        return self._config

    @property
    def settings_file(self) -> Path:
        return self._config / "settings.json"

    @property
    def data_dir(self) -> Path:
        return self._data

    @property
    def mods_dir(self) -> Path:
        """Root of the mod store, laid out like the original's GLM/ folder."""
        return self._data / "mods"

    @property
    def cache_dir(self) -> Path:
        return self._data / "cache"

    @property
    def images_dir(self) -> Path:
        """Mod banner images (500x100), cached by name/version."""
        return self._data / "images"

    @property
    def journal_file(self) -> Path:
        """Records every link we create so a crash can be cleaned up later."""
        return self._data / "links.json"

    @property
    def state_dir(self) -> Path:
        return self._state

    @property
    def log_file(self) -> Path:
        return self._state / "genlauncher.log"

    def ensure(self) -> None:
        for d in (
            self.config_dir,
            self.data_dir,
            self.mods_dir,
            self.cache_dir,
            self.images_dir,
            self.state_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    """User preferences. Every field must survive a load/save round trip."""

    game: str = "zh"  # "zh" | "generals"
    game_dir: str = ""  # explicit override; empty means autodetect
    appid: str = ""  # Steam app id override
    proton: str = ""  # empty = whatever Steam uses for the game
    link_strategy: str = "symlink"  # symlink | hardlink | copy
    launch_mode: str = "steam"  # steam | proton
    extra_args: str = ""
    windowed: bool = True
    quickstart: bool = True
    camera_height: int = 0
    # Executable selection. Empty means "let the launcher decide", which keeps
    # a plain Steam launch untouched; a stored value forces the Proton path so
    # the chosen file really is the one that runs.
    game_exe: str = ""
    world_builder_exe: str = ""
    custom_game_exes: list[dict] = field(default_factory=list)
    custom_world_builder_exes: list[dict] = field(default_factory=list)
    check_mod_files: bool = False
    ask_before_check: bool = True
    hide_launcher: bool = False
    auto_delete_old_versions: bool = False
    use_vulkan: bool = True  # True=DXVK (default Proton), False=force wined3d
    community_patch_tag: str = ""  # e.g. "weekly-2026-10-03"; empty = not patched
    catalog_ttl: int = 900  # seconds
    last_selected_mod: str = ""  # remember last selected mod name
    mod_ratings: dict[str, int] = field(default_factory=dict)  # mod_name → 0-5 stars
    setup_done: bool = False  # first-run wizard completed

    _paths: Paths = field(default_factory=Paths, repr=False, compare=False)

    @property
    def paths(self) -> Paths:
        return self._paths

    @classmethod
    def load(cls, paths: Paths | None = None) -> "Settings":
        paths = paths or Paths()
        s = cls(_paths=paths)
        try:
            raw = json.loads(paths.settings_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return s
        if not isinstance(raw, dict):
            return s
        known = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k in known and not k.startswith("_"):
                setattr(s, k, v)
        return s

    def save(self) -> None:
        self._paths.ensure()
        payload = {
            k: v for k, v in asdict(self).items() if not k.startswith("_")
        }
        tmp = self._paths.settings_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._paths.settings_file)

    def set(self, key: str, value: Any) -> None:
        if key not in {f.name for f in fields(self)} or key.startswith("_"):
            raise KeyError(f"unknown setting: {key}")
        setattr(self, key, value)