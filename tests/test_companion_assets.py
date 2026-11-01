"""Read-only character assets: slots, whitelists and the doctor verdict."""

import base64
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_framework.companion.assets import (
    MAX_AVATAR_BYTES,
    MAX_BACKGROUND_BYTES,
    character_assets_check,
    public_view,
    scan_assets,
)

# 1x1 transparent PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 " + b"\x00" * 16


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.root = self.data / "assets" / "remielle"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def live2d(self, moc="model.moc3", payload=None):
        self.write("live2d/textures/texture_00.png", PNG)
        self.write(
            "live2d/model3.json",
            json.dumps(
                payload
                or {
                    "FileReferences": {
                        "Moc": moc,
                        "Textures": ["textures/texture_00.png"],
                        "Expressions": [
                            {"Name": "calm", "File": "expressions/calm.exp3.json"},
                        ],
                    }
                }
            ).encode("utf-8"),
        )
        if moc:
            self.write("live2d/" + moc, b"MOC3" + b"\x00" * 12)

    def test_missing_directory_means_no_assets(self):
        assets = scan_assets(self.data, "remielle")
        self.assertIsNone(assets.avatar)
        self.assertIsNone(assets.live2d)
        self.assertEqual(
            {"avatar": None, "background": None, "live2d": None, "mesh": None},
            public_view(assets),
        )

    def test_avatar_kind_size_and_stamp(self):
        self.write("avatar.png", PNG)
        assets = scan_assets(self.data, "remielle")
        self.assertEqual("png", assets.avatar.kind)
        self.assertEqual(len(PNG), assets.avatar.size)
        view = public_view(assets)["avatar"]
        self.assertEqual({"kind", "size", "stamp"}, set(view))
        self.assertIsInstance(view["stamp"], int)

    def test_avatar_priority_is_png_webp_jpg(self):
        self.write("avatar.jpg", JPEG)
        self.assertEqual("jpg", scan_assets(self.data, "remielle").avatar.kind)
        self.write("avatar.webp", WEBP)
        self.assertEqual("webp", scan_assets(self.data, "remielle").avatar.kind)
        self.write("avatar.png", PNG)
        self.assertEqual("png", scan_assets(self.data, "remielle").avatar.kind)

    def test_corrupt_signature_is_not_an_avatar(self):
        self.write("avatar.png", b"not a png at all")
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)
        self.write("avatar.jpg", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)

    def test_oversized_avatar_is_rejected(self):
        path = self.root / "avatar.png"
        path.parent.mkdir(parents=True)
        with path.open("wb") as handle:
            handle.write(PNG)
            handle.write(b"\x00" * (MAX_AVATAR_BYTES + 1 - len(PNG)))
        self.assertGreater(path.stat().st_size, MAX_AVATAR_BYTES)
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)

    def test_unknown_avatar_extension_is_ignored(self):
        self.write("avatar.gif", b"GIF89a" + b"\x00" * 16)
        self.write("avatar.bmp", b"BM" + b"\x00" * 16)
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)

    def test_invalid_persona_id_is_refused(self):
        for persona_id in ("", "..", "../remielle", "Remielle", "a/b"):
            with self.subTest(persona_id=persona_id):
                self.assertIsNone(scan_assets(self.data, persona_id).avatar)

    def test_symlinked_avatar_directory_is_ignored(self):
        target = self.data / "elsewhere"
        target.mkdir()
        (target / "avatar.png").write_bytes(PNG)
        self.root.parent.mkdir(parents=True)
        try:
            os.symlink(target, self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation needs extra privileges")
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)

    def test_live2d_entry_edge_cases(self):
        self.assertIsNone(scan_assets(self.data, "remielle").live2d)
        self.write("live2d/model3.json", b"{not json")
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("JSON", entry.reason)
        self.write("live2d/model3.json", b"[]")
        self.assertFalse(scan_assets(self.data, "remielle").live2d.valid)
        self.write("live2d/model3.json", b"{}")
        self.assertFalse(scan_assets(self.data, "remielle").live2d.valid)
        self.live2d(moc="missing.moc3")
        (self.root / "live2d" / "missing.moc3").unlink()
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("moc3", entry.reason)

    def test_live2d_entry_escaping_the_directory_is_refused(self):
        self.write("../secret.moc3", b"moc3")
        self.write("live2d/model3.json", b'{"FileReferences":{"Moc":"../../secret.moc3"}}')
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("越界", entry.reason)

    def test_valid_live2d_entry(self):
        self.live2d()
        entry = scan_assets(self.data, "remielle").live2d
        self.assertTrue(entry.valid)
        self.assertEqual(("calm",), entry.expressions)
        view = public_view(scan_assets(self.data, "remielle"))["live2d"]
        self.assertTrue(view["valid"])
        self.assertEqual("", view["reason"])
        self.assertEqual(["calm"], view["expressions"])
        self.assertIsInstance(view["stamp"], int)

    def test_live2d_corrupt_moc_and_missing_texture_are_invalid(self):
        self.live2d()
        (self.root / "live2d" / "model.moc3").write_bytes(b"nope")
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("文件头", entry.reason)
        self.live2d()
        (self.root / "live2d" / "textures" / "texture_00.png").unlink()
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("贴图", entry.reason)

    def test_live2d_requires_textures_but_tolerates_missing_expression_files(self):
        self.write("live2d/model.moc3", b"MOC3" + b"\x00" * 12)
        self.write(
            "live2d/model3.json",
            json.dumps(
                {
                    "FileReferences": {
                        "Moc": "model.moc3",
                        "Expressions": [{"Name": "warm"}, {"Name": "warm"}],
                    }
                }
            ).encode("utf-8"),
        )
        entry = scan_assets(self.data, "remielle").live2d
        self.assertFalse(entry.valid)
        self.assertIn("Textures", entry.reason)
        self.write("live2d/textures/texture_00.png", PNG)
        self.write(
            "live2d/model3.json",
            json.dumps(
                {
                    "FileReferences": {
                        "Moc": "model.moc3",
                        "Textures": ["textures/texture_00.png"],
                        "Expressions": [
                            {"Name": "warm", "File": "expressions/missing.exp3.json"},
                            {"Name": "warm"},
                            {"Name": "low"},
                        ],
                    }
                }
            ).encode("utf-8"),
        )
        entry = scan_assets(self.data, "remielle").live2d
        self.assertTrue(entry.valid)
        self.assertEqual(("warm", "low"), entry.expressions)

    def test_background_priority_and_independence(self):
        self.write("background.jpg", JPEG)
        self.assertEqual("jpg", scan_assets(self.data, "remielle").background.kind)
        self.assertIsNone(scan_assets(self.data, "remielle").avatar)
        self.write("background.webp", WEBP)
        self.assertEqual("webp", scan_assets(self.data, "remielle").background.kind)
        self.write("background.png", PNG)
        assets = scan_assets(self.data, "remielle")
        self.assertEqual("png", assets.background.kind)
        view = public_view(assets)["background"]
        self.assertEqual({"kind", "size", "stamp"}, set(view))
        self.assertEqual(len(PNG), view["size"])

    def test_corrupt_or_oversized_background_is_rejected(self):
        self.write("background.png", b"not a png at all")
        self.assertIsNone(scan_assets(self.data, "remielle").background)
        self.write("background.jpg", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        self.assertIsNone(scan_assets(self.data, "remielle").background)
        path = self.root / "background.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(PNG)
            handle.write(b"\x00" * (MAX_BACKGROUND_BYTES + 1 - len(PNG)))
        self.assertIsNone(scan_assets(self.data, "remielle").background)

    def test_background_ignores_unknown_extensions(self):
        self.write("background.gif", b"GIF89a" + b"\x00" * 16)
        self.assertIsNone(scan_assets(self.data, "remielle").background)
        self.write("wallpaper.png", PNG)
        self.assertIsNone(scan_assets(self.data, "remielle").background)

    @staticmethod
    def eye_curve(x0, x1):
        return {"x0": x0, "x1": x1, "top": [0.0] * 24, "bot": [1.0] * 24}

    def mesh(self, overrides=None, drop=(), compression=zipfile.ZIP_STORED, extra=None):
        files = {
            "avatar.json": json.dumps(
                {"format": "mesh-avatar", "version": 1, "name": "test-avatar"}
            ).encode("utf-8"),
            "rig.json": json.dumps({"version": 1}).encode("utf-8"),
            "built/layers.json": json.dumps(
                {
                    "size": [16, 16],
                    "layers": {
                        "eye0_ball": [0, 0, 2, 2],
                        "eye0_lash": [0, 0, 2, 2],
                    },
                    "eyes": [self.eye_curve(2, 4), self.eye_curve(8, 10)],
                }
            ).encode("utf-8"),
            "built/base.png": PNG,
            "built/hairmask.png": PNG,
            "built/eye0_ball.png": PNG,
            "built/eye0_lash.png": PNG,
        }
        files.update(overrides or {})
        for name in drop:
            files.pop(name, None)
        files.update(extra or {})
        target = self.root / "avatar.mavatar"
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", compression) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return target

    def test_valid_mesh_archive(self):
        self.mesh()
        entry = scan_assets(self.data, "remielle").mesh
        self.assertTrue(entry.valid)
        self.assertEqual("test-avatar", entry.name)
        self.assertEqual("", entry.reason)
        view = public_view(scan_assets(self.data, "remielle"))["mesh"]
        self.assertTrue(view["valid"])
        self.assertEqual("test-avatar", view["name"])
        self.assertIn("smile", view["expressions"])
        self.assertIsInstance(view["stamp"], int)
        self.assertEqual(
            "ok", character_assets_check(self.data, "remielle", True)[0]
        )

    def test_mesh_requires_the_core_files(self):
        for name in (
            "avatar.json",
            "rig.json",
            "built/layers.json",
            "built/base.png",
            "built/hairmask.png",
            "built/eye0_ball.png",
        ):
            with self.subTest(name=name):
                self.mesh(drop=(name,))
                entry = scan_assets(self.data, "remielle").mesh
                self.assertFalse(entry.valid)
                self.assertIn("缺少", entry.reason)

    def test_mesh_rejects_a_bad_archive_or_manifest(self):
        self.write("avatar.mavatar", b"not a zip at all")
        self.assertIn(
            "归档", scan_assets(self.data, "remielle").mesh.reason
        )
        cases = (
            ({"avatar.json": b"[]"}, "JSON"),
            ({"avatar.json": json.dumps({"format": "other", "version": 1, "name": "x"}).encode()}, "format"),
            ({"avatar.json": json.dumps({"format": "mesh-avatar", "version": 2, "name": "x"}).encode()}, "版本"),
            ({"avatar.json": json.dumps({"format": "mesh-avatar", "version": 1, "name": " "}).encode()}, "name"),
            ({"rig.json": json.dumps({"version": 2}).encode()}, "rig.json"),
            ({"built/base.png": b"not a png"}, "PNG"),
            ({"built/layers.json": json.dumps({"size": [16, 16], "layers": {}}).encode()}, "图层"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                self.mesh(overrides=overrides)
                entry = scan_assets(self.data, "remielle").mesh
                self.assertFalse(entry.valid)
                self.assertIn(reason, entry.reason)

    def test_mesh_rejects_unsafe_or_nonstored_entries(self):
        self.mesh(extra={"../secret.png": PNG})
        self.assertIn("不安全", scan_assets(self.data, "remielle").mesh.reason)
        self.mesh(compression=zipfile.ZIP_DEFLATED)
        self.assertIn("stored", scan_assets(self.data, "remielle").mesh.reason)

    def test_mesh_does_not_silently_replace_a_broken_live2d_model(self):
        self.mesh()
        self.assertEqual(
            "ok", character_assets_check(self.data, "remielle", True)[0]
        )
        self.write("live2d/model3.json", b"{not json")
        level, detail = character_assets_check(self.data, "remielle", True)
        self.assertEqual("fail", level)
        self.assertIn("invalid:", detail)
        self.assertEqual(
            "warn", character_assets_check(self.data, "remielle", False)[0]
        )

    def test_doctor_fails_only_when_the_overlay_is_on(self):
        level, detail = character_assets_check(self.data, "remielle", True)
        self.assertEqual("fail", level)
        self.assertIn("live2d/model3.json", detail)
        level, _ = character_assets_check(self.data, "remielle", False)
        self.assertEqual("warn", level)
        self.write("live2d/model3.json", b"{not json")
        self.assertEqual(
            "fail", character_assets_check(self.data, "remielle", True)[0]
        )
        self.live2d()
        self.assertEqual(
            "ok", character_assets_check(self.data, "remielle", True)[0]
        )
        self.assertEqual(
            "ok", character_assets_check(self.data, "remielle", False)[0]
        )
        self.assertIn("avatar=none", character_assets_check(
            self.data, "remielle", True
        )[1])


if __name__ == "__main__":
    unittest.main()
