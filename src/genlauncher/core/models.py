"""Data model for modifications.

Mirrors Rewritten's ModificationReposVersion / ModificationVersion split:
a *ReposVersion* is what the catalog advertises, a *ModificationVersion* is what we
have on disk. They merge into one object per (name, version) pair.

Version strings in the wild are free text ("1.87 Public Build 2.0", "8.9", "32.2"),
so comparison is digit-based to stay compatible with the catalog format.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import ADDONS_DIR, EXECUTABLES_DIR, PATCHES_DIR, TEMP_SUFFIX

# The umbrella name "Original Game" is used as the
# DependenceName for patches/addons/exes that apply to the unmodded game.
ORIGINAL_GAME_ALIAS = "Original Game"


class ModType(enum.Enum):
    MOD = "Mod"
    ADDON = "Addon"
    PATCH = "Patch"
    EXECUTABLE = "Executable"
    ADVERTISING = "Advertising"

    @classmethod
    def parse(cls, raw: Any, default: "ModType | None" = None) -> "ModType":
        """Tolerate any casing/spelling; real manifests are not consistent."""
        if isinstance(raw, cls):
            return raw
        if raw:
            key = str(raw).strip().casefold()
            for member in cls:
                if member.value.casefold() == key:
                    return member
        if default is not None:
            return default
        raise ValueError(f"unknown ModificationType: {raw!r}")


def version_key(version: str) -> tuple[int, ...]:
    """Comparable key for a free-text version string.

    Extracts digit runs so "1.87 Public Build 2.0" -> (1, 87, 2, 0) and
    "1.10" > "1.9" (lexical/string comparison gets this wrong).
    """
    digits: list[int] = []
    current = ""
    for ch in version or "":
        if ch.isdigit():
            current += ch
        elif current:
            digits.append(int(current))
            current = ""
    if current:
        digits.append(int(current))
    return tuple(digits)


def legacy_version_key(version: str) -> int:
    """Version sort algorithm matching the catalog format.

    It concatenates *all* digits into one int, which makes "1.87 Public Build 2.0"
    (18720) rank above "2.0" (20). version_key() above is the corrected ordering;
    this exists so we can log when the two disagree.
    """
    digits = "".join(c for c in (version or "") if c.isdigit())
    if not digits:
        return -1
    while len(digits) > 18:  # keep int() from blowing up on silly versions
        digits = digits[:-1]
    return int(digits)


@dataclass
class ColorsInfo:
    """Per-mod theme override shipped in the manifest. All fields optional."""

    border_color: str = ""
    inactive_border: str = ""
    active_color: str = ""
    dark_fill_color: str = ""
    dark_background: str = ""
    light_background: str = ""
    default_text_color: str = ""
    button_selection_color: str = ""
    listbox_selection_color_1: str = ""
    listbox_selection_color_2: str = ""
    background_image_link: str = ""

    @classmethod
    def from_dict(cls, raw: Any) -> "ColorsInfo | None":
        if not isinstance(raw, dict):
            return None
        # Manifest keys are PascalCase; accept snake_case and any casing.
        norm = {str(k).replace("_", "").casefold(): v for k, v in raw.items()}
        get = lambda *names: next(  # noqa: E731
            (str(norm[n.replace("_", "").casefold()]) for n in names
             if n.replace("_", "").casefold() in norm and norm[n.replace("_", "").casefold()] is not None),
            "",
        )
        return cls(
            border_color=get("GenLauncherBorderColor", "border_color"),
            inactive_border=get("GenLauncherInactiveBorder", "inactive_border"),
            active_color=get("GenLauncherActiveColor", "active_color"),
            dark_fill_color=get("GenLauncherDarkFillColor", "dark_fill_color"),
            dark_background=get("GenLauncherDarkBackGround", "dark_background"),
            light_background=get("GenLauncherLightBackGround", "light_background"),
            default_text_color=get("GenLauncherDefaultTextColor", "default_text_color"),
            button_selection_color=get("GenLauncherButtonSelectionColor", "button_selection_color"),
            listbox_selection_color_1=get("GenLauncherListBoxSelectionColor1", "listbox_selection_color_1"),
            listbox_selection_color_2=get("GenLauncherListBoxSelectionColor2", "listbox_selection_color_2"),
            background_image_link=get("GenLauncherBackgroundImageLink", "background_image_link"),
        )

    def is_empty(self) -> bool:
        return not any(self.__dict__.values())


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass
class ModificationVersion:
    """One installable version of a mod/patch/addon/exe.

    `installed` and `source` are ours, not the catalog's.
    """

    name: str = ""
    version: str = ""
    mod_type: ModType = ModType.MOD
    dependence_name: str = ""
    simple_download_link: str = ""
    ui_image_source_link: str = ""
    discord_link: str = ""
    moddb_link: str = ""
    news_link: str = ""
    support_link: str = ""
    network_info: str = ""
    deprecated: bool = False
    executable_file_name: str = ""
    replaces_original_game_file: bool = False
    replaces_original_wb_file: bool = False
    exception_names: list[str] = field(default_factory=list)
    additional_file_names: list[str] = field(default_factory=list)
    colors: ColorsInfo | None = None

    # S3 storage fields (for multi-file download from MinIO/S3).
    s3_host_link: str = ""
    s3_bucket_name: str = ""
    s3_folder_name: str = ""
    s3_host_public_key: str = ""
    s3_host_secret_key: str = ""

    # Local-only state.
    installed: bool = False
    installed_at: float = 0.0
    manual: bool = False

    @property
    def uses_s3(self) -> bool:
        """True when this version should be downloaded via S3 multi-file."""
        return bool(self.s3_host_link and self.s3_folder_name)

    # ---- identity -------------------------------------------------------

    @property
    def key(self) -> str:
        """Case-insensitive identity, matching the original's comparisons."""
        return f"{self.name.casefold()}|{self.version.casefold()}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ModificationVersion) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    @property
    def sort_key(self) -> tuple:
        return (version_key(self.version), self.version)

    def __lt__(self, other: "ModificationVersion") -> bool:
        return self.sort_key < other.sort_key

    # ---- paths ----------------------------------------------------------

    def folder_name(self, mods_root: Path) -> Path:
        """Install path, structurally identical to the original's GetFolderName().

        Mod:        <root>/<name>/<version>
        Patch:      <root>/<dep>/Patches/<name>/<version>
        Addon:      <root>/<dep>/Addons/<name>/<version>
        Executable: <root>/<dep>/Executables/<name>/<version>
        """
        root = Path(mods_root)
        sub = {
            ModType.PATCH: PATCHES_DIR,
            ModType.ADDON: ADDONS_DIR,
            ModType.EXECUTABLE: EXECUTABLES_DIR,
        }.get(self.mod_type)
        if sub is None:
            return root / self.name / self.version
        dep = self.dependence_name or ORIGINAL_GAME_ALIAS
        return root / dep / sub / self.name / self.version

    def is_installed(self, mods_root: Path) -> bool:
        return self.folder_name(mods_root).is_dir()

    # ---- parsing --------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict, default_type: ModType | None = None) -> "ModificationVersion":
        """Parse a manifest. Unknown keys are ignored, missing keys get defaults."""
        def get(*names, default=""):
            for n in names:
                if n in raw and raw[n] is not None:
                    return raw[n]
            return default

        def get_list(*names) -> list[str]:
            value = get(*names, default=None)
            if value in (None, ""):
                return []
            if isinstance(value, str):
                return [value]
            return [str(v) for v in value if v is not None]

        def get_bool(*names, default=False) -> bool:
            value = get(*names, default=None)
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            return str(value).strip().casefold() in {"1", "true", "yes", "on"}

        exc = get_list("ExceptionNames", "exception_names")
        add = get_list("AdditionalFileNames", "additional_file_names")

        return cls(
            name=_as_str(get("Name", "name")),
            version=_as_str(get("Version", "version")),
            mod_type=ModType.parse(
                get("ModificationType", "modification_type", "Type"), default_type
            ),
            dependence_name=_as_str(get("DependenceName", "dependence_name", "DependencyName")),
            simple_download_link=_as_str(
                get("SimpleDownloadLink", "simple_download_link", "DownloadLink")
            ),
            ui_image_source_link=_as_str(get("UIImageSourceLink", "ui_image_source_link")),
            discord_link=_as_str(get("DiscordLink", "discord_link")),
            moddb_link=_as_str(get("ModDBLink", "moddb_link")),
            news_link=_as_str(get("NewsLink", "news_link")),
            support_link=_as_str(get("SupportLink", "support_link")),
            network_info=_as_str(get("NetworkInfo", "network_info")),
            deprecated=get_bool("Deprecated", "deprecated"),
            executable_file_name=_as_str(
                get("ExecutableFileName", "executable_file_name")
            ),
            replaces_original_game_file=get_bool(
                "ReplacesOriginalGameFile", "replaces_original_game_file"
            ),
            replaces_original_wb_file=get_bool(
                "ReplacesOriginalWBFile", "replaces_original_wb_file"
            ),
            exception_names=exc,
            additional_file_names=add,
            colors=ColorsInfo.from_dict(get("ColorsInformation", "colors_information")),
            s3_host_link=_as_str(get("S3HostLink", "s3_host_link")),
            s3_bucket_name=_as_str(get("S3BucketName", "s3_bucket_name")),
            s3_folder_name=_as_str(get("S3FolderName", "s3_folder_name")),
            s3_host_public_key=_as_str(get("S3HostPublicKey", "s3_host_public_key")),
            s3_host_secret_key=_as_str(get("S3HostSecretKey", "s3_host_secret_key")),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "mod_type": self.mod_type.value,
            "dependence_name": self.dependence_name,
            "simple_download_link": self.simple_download_link,
            "ui_image_source_link": self.ui_image_source_link,
            "discord_link": self.discord_link,
            "moddb_link": self.moddb_link,
            "news_link": self.news_link,
            "support_link": self.support_link,
            "network_info": self.network_info,
            "deprecated": self.deprecated,
            "executable_file_name": self.executable_file_name,
            "replaces_original_game_file": self.replaces_original_game_file,
            "replaces_original_wb_file": self.replaces_original_wb_file,
            "exception_names": self.exception_names,
            "additional_file_names": self.additional_file_names,
            "s3_host_link": self.s3_host_link,
            "s3_bucket_name": self.s3_bucket_name,
            "s3_folder_name": self.s3_folder_name,
            "s3_host_public_key": self.s3_host_public_key,
            "s3_host_secret_key": self.s3_host_secret_key,
            "installed": self.installed,
            "installed_at": self.installed_at,
            "manual": self.manual,
        }


@dataclass
class RawModEntry:
    """One entry of the root catalog: pointers to per-mod manifests."""

    mod_name: str = ""
    mod_link: str = ""
    mod_patches: list[str] = field(default_factory=list)
    mod_addons: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "RawModEntry":
        def get(*names, default=""):
            for n in names:
                if n in raw and raw[n] is not None:
                    return raw[n]
            return default

        def get_list(*names) -> list[str]:
            value = get(*names, default=None)
            if value in (None, ""):
                return []
            if isinstance(value, str):
                return [value]
            return [str(v) for v in value if v]

        return cls(
            mod_name=_as_str(get("ModName", "mod_name")),
            mod_link=_as_str(get("ModLink", "mod_link")),
            mod_patches=get_list("ModPatches", "mod_patches"),
            mod_addons=get_list("ModAddons", "mod_addons"),
        )


@dataclass
class RawExeEntry:
    """Executable entry. Empty dependency_name means "applies to the base game"."""

    mod_name: str = ""
    mod_link: str = ""
    dependence_name: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "RawExeEntry":
        def get(*names, default=""):
            for n in names:
                if n in raw and raw[n] is not None:
                    return raw[n]
            return default

        return cls(
            mod_name=_as_str(get("ModName", "mod_name")),
            mod_link=_as_str(get("ModLink", "mod_link")),
            dependence_name=_as_str(
                get("DependencyName", "Dependency_Name", "DependenceName", "dependence_name")
            ),
        )


@dataclass
class Catalog:
    """Parsed root manifest."""

    launcher_version: str = ""
    download_link: str = ""
    vulkan_repos_data: str = ""
    mod_datas: list[RawModEntry] = field(default_factory=list)
    executables: list[RawExeEntry] = field(default_factory=list)
    original_game_patches: list[str] = field(default_factory=list)
    original_game_addons: list[str] = field(default_factory=list)
    global_addons_data: list[str] = field(default_factory=list)
    advertising: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "Catalog":
        if not isinstance(raw, dict):
            return cls()
        norm = {str(k).casefold(): v for k, v in raw.items()}

        def scalar(key: str) -> str:
            value = norm.get(key.casefold())
            return "" if value is None else str(value)

        def listing(key: str) -> list[str]:
            value = norm.get(key.casefold())
            if value in (None, ""):
                return []
            if isinstance(value, str):
                return [value]
            return [str(v) for v in value if v]

        def records(key: str) -> list[dict]:
            value = norm.get(key.casefold()) or []
            if isinstance(value, dict):
                value = [value]
            return [v for v in value if isinstance(v, dict)]

        return cls(
            launcher_version=scalar("LauncherVersion"),
            download_link=scalar("DownloadLink"),
            vulkan_repos_data=scalar("VulkanReposData"),
            mod_datas=[RawModEntry.from_dict(r) for r in records("modDatas")],
            executables=[RawExeEntry.from_dict(r) for r in records("executables")],
            original_game_patches=listing("originalGamePatches"),
            original_game_addons=listing("originalGameAddons"),
            global_addons_data=listing("globalAddonsData"),
            advertising=records("AdvData"),
        )

    def mod_names(self) -> list[str]:
        return [e.mod_name for e in self.mod_datas]

    def find_mod(self, name: str) -> RawModEntry | None:
        target = name.casefold()
        for entry in self.mod_datas:
            if entry.mod_name.casefold() == target:
                return entry
        return None


@dataclass
class GameInstall:
    """A located game installation plus the Proton bits needed to run it."""

    game_dir: Path
    appid: str = ""
    steam_root: Path | None = None
    library: Path | None = None
    mode: str = "zh"  # "zh" | "generals"

    @property
    def game_exe(self) -> Path:
        return self.game_dir / "Generals.exe"

    @property
    def compat_data(self) -> Path | None:
        if not self.library or not self.appid:
            return None
        return self.library / "steamapps" / "compatdata" / self.appid

    @property
    def prefix(self) -> Path | None:
        cd = self.compat_data
        return cd / "pfx" if cd else None


def purge_temp_folders(root: Path) -> list[Path]:
    """Delete leftover *TEMP_SUFFIX folders, e.g. after a crashed install."""
    removed: list[Path] = []
    if not root.is_dir():
        return removed
    for path in sorted(root.rglob(f"*{TEMP_SUFFIX}*"), reverse=True):
        if path.is_dir():
            import shutil

            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed