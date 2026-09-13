from PIL import Image

from guardian.assets.icon import build_image, ensure_ico, get_ico_path


def test_existing_foreign_icon_is_replaced_and_current_icon_is_stable(tmp_path):
    path = tmp_path / "guardian.ico"
    Image.new("RGBA", (256, 256), "red").save(path, format="ICO")
    ensure_ico(path)
    icon = Image.open(path)
    assert icon.convert("RGBA").tobytes() == build_image(256).tobytes()
    assert icon.ico.sizes() == {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)}
    timestamp = path.stat().st_mtime_ns
    ensure_ico(path)
    assert path.stat().st_mtime_ns == timestamp


def test_runtime_uses_separate_g1_icon_cache():
    assert get_ico_path().name == "guardian-g1.ico"
