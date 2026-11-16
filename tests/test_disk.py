from genlauncher.core.disk import folder_size, format_size, installed_size
from genlauncher.core.models import ModificationVersion
from genlauncher.core.store import InstalledMod


def test_folder_size_counts_nested_files_and_skips_symlinks(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.big").write_bytes(b"x" * 100)
    (tmp_path / "two.ini").write_bytes(b"x" * 20)
    (tmp_path / "link.big").symlink_to(tmp_path / "a" / "one.big")
    (tmp_path / "dirlink").symlink_to(tmp_path / "a")
    assert folder_size(tmp_path) == 120


def test_folder_size_of_missing_folder_is_zero(tmp_path):
    assert folder_size(tmp_path / "missing") == 0


def test_installed_size_sums_all_versions(tmp_path):
    mod = InstalledMod(name="Test Mod")
    for version, size in (("1.0", 10), ("2.0", 30)):
        mv = ModificationVersion(name="Test Mod", version=version)
        folder = mv.folder_name(tmp_path)
        folder.mkdir(parents=True)
        (folder / "data.big").write_bytes(b"x" * size)
        mod.versions[version] = mv
    assert installed_size(mod, tmp_path) == 40


def test_format_size():
    assert format_size(512) == "512 B"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024**3) == "5.0 GB"
    assert format_size(3000 * 1024**3) == "3000.0 GB"
