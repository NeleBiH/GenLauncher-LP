"""Read/write the game's Options.ini file.

On Linux with Proton the file lives inside the Proton prefix, typically at:

    ~/.local/share/Steam/steamapps/compatdata/<APPID>/pfx/drive_c/users/
    steamuser/My Documents/Command and Conquer Generals Zero Hour Data/Options.ini

The format is simple ``Key = Value`` lines.  Values for boolean options are
`` yes`` or `` no`` (with a leading space, matching the expected format).
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("genlauncher.core")

# INI keys and their defaults
DEFAULT_OPTIONS: dict[str, str] = {
    "Resolution": " 1024 768",
    "MaxParticleCount": " 2500",
    "TextureReduction": " 1",
    "UseShadowVolumes": " yes",
    "UseShadowDecals": " yes",
    "UseCloudMap": " no",
    "ExtraAnimations": " yes",
    "ExtraGroundLighting": " yes",
    "ShowSoftWaterEdge": " yes",
    "HeatEffects": " no",
    "ShowProps": " yes",
    "DynamicLOD": " yes",
    "BuildingOcclusion": " yes",
    "UseAlternateMouse": " no",
    "ShowTrees": " yes",
    "AntiAliasing": " no",
    "Retaliation": " yes",
    "UseDoubleClickAttackMove": " no",
    "Gamma": " 50",
    "FPSLimit": " yes",
    "ScrollFactor": " 50",
    "CursorCapture": " yes",
}

# Map our Settings checkbox keys → INI keys
GFX_KEY_MAP: dict[str, str] = {
    "3d_shadows": "UseShadowVolumes",
    "2d_shadows": "UseShadowDecals",
    "show_props": "ShowProps",
    "cloud_shadows": "UseCloudMap",
    "extra_animation": "ExtraAnimations",
    "extra_ground_lighting": "ExtraGroundLighting",
    "disable_lod": "DynamicLOD",           # INVERTED
    "smooth_water": "ShowSoftWaterEdge",
    "heat_effects": "HeatEffects",
    "display_units": "BuildingOcclusion",
    "show_trees": "ShowTrees",
    "anti_aliasing": "AntiAliasing",
    "retaliation": "Retaliation",
    "use_double_click_attack": "UseDoubleClickAttackMove",
    "use_alternate_mouse": "UseAlternateMouse",
    "fps_limit": "FPSLimit",
    "cursor_capture": "CursorCapture",
}

# Keys where the checkbox meaning is inverted relative to the INI value
INVERTED_KEYS = {"disable_lod"}  # checkbox "Disable LOD" checked → DynamicLOD = no


def _find_user_data_dir(game_dir: Path | None = None, game: str = "zh") -> Path | None:
    """Locate the game's user data directory inside a Wine/Proton prefix.

    Searches in order:
    1. Steam prefix (from find_game_dirs)
    2. All Steam compatdata prefixes
    3. Wine prefix derived from game_dir (for ISO/disc installs)
    4. Default Wine prefix (~/.wine)
    5. $WINEPREFIX environment variable
    """
    import os

    folder_name = (
        "Command and Conquer Generals Zero Hour Data"
        if game == "zh"
        else "Command and Conquer Generals Data"
    )

    prefixes: list[Path] = []

    # 1. Steam install prefix
    try:
        from .steam import find_game_dirs

        for install in find_game_dirs():
            if game_dir is not None and install.game_dir.resolve() != Path(game_dir).resolve():
                continue
            if install.mode != game:
                continue
            if install.prefix:
                prefixes.append(install.prefix)
    except Exception:
        pass

    # 2. All Steam compatdata prefixes
    try:
        from .steam import find_steam_roots

        for root in find_steam_roots():
            compatdata = root / "steamapps" / "compatdata"
            if not compatdata.is_dir():
                continue
            for entry in sorted(compatdata.iterdir()):
                if entry.is_dir():
                    prefixes.append(entry / "pfx")
    except Exception:
        pass

    # 3. Derive Wine prefix from game_dir (ISO/disc install via Wine/Proton)
    #    e.g. /path/to/prefix/drive_c/Program Files/EA Games/... → /path/to/prefix
    if game_dir is not None:
        gd = Path(game_dir).resolve()
        for part_idx, part in enumerate(gd.parts):
            if part == "drive_c":
                wine_root = Path(*gd.parts[:part_idx])
                if wine_root not in prefixes:
                    prefixes.append(wine_root)
                break

    # 4. Default Wine prefix
    default_wine = Path.home() / ".wine"
    if default_wine.is_dir() and default_wine not in prefixes:
        prefixes.append(default_wine)

    # 5. $WINEPREFIX
    env_prefix = os.environ.get("WINEPREFIX")
    if env_prefix:
        wp = Path(env_prefix)
        if wp.is_dir() and wp not in prefixes:
            prefixes.append(wp)

    # Search all prefixes for the data folder
    for prefix in prefixes:
        # Try multiple user directory names (Steam uses "steamuser",
        # Wine uses the actual Linux username or a fixed name)
        users_dir = prefix / "drive_c" / "users"
        if not users_dir.is_dir():
            continue
        try:
            user_dirs = sorted(users_dir.iterdir())
        except OSError:
            continue
        for user_dir in user_dirs:
            if not user_dir.is_dir():
                continue
            for docs_name in ("My Documents", "Documents"):
                candidate = user_dir / docs_name / folder_name
                if candidate.is_dir():
                    return candidate

    return None


def find_options_ini(game_dir: Path | None = None, game: str = "zh") -> Path | None:
    """Locate the game's Options.ini file inside the Proton prefix."""
    data_dir = _find_user_data_dir(game_dir, game)
    if data_dir is None:
        return None
    candidate = data_dir / "Options.ini"
    return candidate if candidate.exists() else None


def find_skirmish_ini(game_dir: Path | None = None, game: str = "zh") -> Path | None:
    """Locate the game's Skirmish.ini file inside the Proton prefix."""
    data_dir = _find_user_data_dir(game_dir, game)
    if data_dir is None:
        return None
    candidate = data_dir / "Skirmish.ini"
    return candidate if candidate.exists() else None


def read_options(path: Path) -> dict[str, str]:
    """Parse Options.ini into a dict.

    Keys are stripped, values keep their leading space to match the expected
    format exactly (important for the game's parser).
    """
    options: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if " = " in line:
                key, _, value = line.partition(" = ")
                options[key.strip()] = value  # Keep leading space in value
    except OSError as exc:
        log.warning("Cannot read Options.ini: %s", exc)
    return options


def write_options(path: Path, options: dict[str, str]) -> None:
    """Write options dict back to Options.ini."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {value}" for key, value in options.items()]
    tmp = path.with_suffix(".ini.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    log.info("Wrote Options.ini: %s", path)


def ensure_options(path: Path) -> dict[str, str]:
    """Read Options.ini, creating it with defaults if it doesn't exist."""
    if path.exists():
        opts = read_options(path)
        # Fill in missing keys with defaults
        for key, default in DEFAULT_OPTIONS.items():
            if key not in opts:
                opts[key] = default
        return opts

    # Create with defaults
    write_options(path, DEFAULT_OPTIONS)
    return dict(DEFAULT_OPTIONS)


def apply_gfx_settings(
    options: dict[str, str],
    gfx_checks: dict[str, bool],
) -> None:
    """Apply graphics checkbox states to the options dict.

    *gfx_checks* maps our internal key names (e.g. "3d_shadows") to bool.
    Handles the DynamicLOD inversion automatically.
    """
    for our_key, checked in gfx_checks.items():
        ini_key = GFX_KEY_MAP.get(our_key)
        if ini_key is None:
            continue

        if our_key in INVERTED_KEYS:
            # "Disable LOD" checked → DynamicLOD = no
            value = " no" if checked else " yes"
        else:
            value = " yes" if checked else " no"

        options[ini_key] = value


def apply_numeric_settings(
    options: dict[str, str],
    resolution: str = "",
    camera_height: int = 0,
    particle_count: int = 2500,
    texture_slider: int = 0,
    gamma: int = 50,
    scroll_factor: int = 50,
) -> None:
    """Apply numeric settings to the options dict.

    *texture_slider* is the UI value (0-2). The INI value is inverted:
    slider 0 (low) → INI 2, slider 2 (high) → INI 0.
    """
    if resolution:
        # Format: " WIDTH HEIGHT" with leading space
        parts = resolution.strip().replace("x", " ").replace("×", " ")
        options["Resolution"] = f" {parts}"

    if camera_height > 0:
        options["CameraHeight"] = f" {camera_height}"
    elif "CameraHeight" in options:
        del options["CameraHeight"]

    options["MaxParticleCount"] = f" {particle_count}"

    # Texture slider inversion: UI 0=low → INI 2, UI 2=high → INI 0
    ini_texture = abs(texture_slider - 2)
    options["TextureReduction"] = f" {ini_texture}"

    options["Gamma"] = f" {gamma}"
    options["ScrollFactor"] = f" {scroll_factor}"


def gfx_from_options(options: dict[str, str]) -> dict[str, bool]:
    """Read graphics checkbox states from an options dict.

    Returns our internal key names mapped to booleans.
    Handles DynamicLOD inversion automatically.
    """
    result: dict[str, bool] = {}
    for our_key, ini_key in GFX_KEY_MAP.items():
        raw = options.get(ini_key, DEFAULT_OPTIONS.get(ini_key, " yes"))
        is_yes = raw.strip().lower() == "yes"

        if our_key in INVERTED_KEYS:
            result[our_key] = not is_yes  # DynamicLOD=yes → Disable LOD unchecked
        else:
            result[our_key] = is_yes

    return result


def numeric_from_options(options: dict[str, str]) -> dict[str, int | str]:
    """Extract numeric values from options dict.

    Returns dict with keys: resolution, camera_height, particle_count, texture_slider.
    texture_slider is the UI value (0-2), already inverted from INI.
    """
    result: dict[str, int | str] = {}

    # Resolution
    res = options.get("Resolution", " 1024 768").strip()
    result["resolution"] = res

    # Camera height
    try:
        result["camera_height"] = int(options.get("CameraHeight", "0").strip())
    except ValueError:
        result["camera_height"] = 0

    # Particles
    try:
        result["particle_count"] = int(options.get("MaxParticleCount", "2500").strip())
    except ValueError:
        result["particle_count"] = 2500

    # Texture (invert: INI 0=high → slider 2, INI 2=low → slider 0)
    try:
        ini_val = int(options.get("TextureReduction", "1").strip())
        result["texture_slider"] = abs(ini_val - 2)
    except ValueError:
        result["texture_slider"] = 0

    # Gamma (50-100)
    try:
        result["gamma"] = int(options.get("Gamma", "50").strip())
    except ValueError:
        result["gamma"] = 50

    # Scroll factor (0-100)
    try:
        result["scroll_factor"] = int(options.get("ScrollFactor", "50").strip())
    except ValueError:
        result["scroll_factor"] = 50

    return result


# ---------------------------------------------------------------------------
# Skirmish.ini helpers
# ---------------------------------------------------------------------------

#: Common starting cash presets (matching the in-game dropdown).
STARTING_CASH_PRESETS = [5000, 10000, 20000, 25000, 30000, 40000, 50000, 75000, 100000]


def read_skirmish(path: Path) -> dict[str, str]:
    """Parse Skirmish.ini into a dict (same format as Options.ini)."""
    return read_options(path)


def write_skirmish(path: Path, options: dict[str, str]) -> None:
    """Write skirmish dict back to Skirmish.ini."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key} = {value}" for key, value in options.items()]
    tmp = path.with_suffix(".ini.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    log.info("Wrote Skirmish.ini: %s", path)


def get_starting_cash(skirmish: dict[str, str]) -> int:
    """Read StartingCash from a skirmish dict, defaulting to 10000."""
    try:
        return int(skirmish.get("StartingCash", "10000").strip())
    except ValueError:
        return 10000


def set_starting_cash(skirmish: dict[str, str], cash: int) -> None:
    """Set StartingCash in a skirmish dict."""
    skirmish["StartingCash"] = f" {cash}"
