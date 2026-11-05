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
    AssetImportError,
    character_assets_check,
    import_avatar,
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
        return self.root / "live2d"

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
        self.assertEqual("model3.json", entry.model_path)
        view = public_view(scan_assets(self.data, "remielle"))["live2d"]
        self.assertTrue(view["valid"])
        self.assertEqual("", view["reason"])
        self.assertEqual(["calm"], view["expressions"])
        self.assertEqual([], view["motions"])
        self.assertEqual({}, view["motion_groups"])
        self.assertEqual("model3.json", view["model_path"])
        self.assertIsInstance(view["stamp"], int)

    def test_nested_cubism_model_is_discovered_from_its_own_directory(self):
        folder = self.root / "live2d" / "DeepSeek"
        texture = folder / "character.4096" / "texture_00.png"
        texture.parent.mkdir(parents=True)
        texture.write_bytes(PNG)
        (folder / "character.moc3").write_bytes(b"MOC3" + b"\x00" * 12)
        (folder / "character.model3.json").write_text(
            json.dumps(
                {
                    "FileReferences": {
                        "Moc": "character.moc3",
                        "Textures": ["character.4096/texture_00.png"],
                    }
                }
            ),
            encoding="utf-8",
        )

        entry = scan_assets(self.data, "remielle").live2d

        self.assertTrue(entry.valid)
        self.assertEqual("DeepSeek/character.model3.json", entry.model_path)
        self.assertEqual(
            entry.model_path,
            public_view(scan_assets(self.data, "remielle"))["live2d"]["model_path"],
        )

    def test_root_model_remains_exclusive_when_present(self):
        self.live2d()
        nested = self.root / "live2d" / "nested"
        nested.mkdir()
        (nested / "other.model3.json").write_text("{}", encoding="utf-8")

        entry = scan_assets(self.data, "remielle").live2d

        self.assertTrue(entry.valid)
        self.assertEqual("model3.json", entry.model_path)

    def test_live2d_motion_groups_need_a_real_clip_file(self):
        self.live2d(
            payload={
                "FileReferences": {
                    "Moc": "model.moc3",
                    "Textures": ["textures/texture_00.png"],
                    "Motions": {
                        "idle": [{"File": "Animation/IDLE.motion3.json"}],
                        "point_finger": [{"File": "Animation/point.motion3.json"}],
                        "wrong_suffix": [{"File": "Animation/clip.motion3"}],
                        "missing_file": [{"File": "Animation/gone.motion3.json"}],
                        "empty": [],
                        "bad": "not-a-list",
                        "escaping": [{"File": "../outside.motion3.json"}],
                    },
                },
                "Groups": [
                    {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeROpen"]},
                    {
                        "Target": "Motion",
                        "Name": "idle_pool",
                        "Ids": ["point_finger", "missing_file", "point_finger"],
                    },
                    {"Target": "Motion", "Name": "action_pool", "Ids": ["point_finger"]},
                    {"Target": "Motion", "Name": "nothing", "Ids": []},
                    {"Name": "no_target", "Ids": ["idle"]},
                ],
            }
        )
        self.write("live2d/Animation/IDLE.motion3.json", b"{}")
        self.write("live2d/Animation/point.motion3.json", b"{}")
        self.write("live2d/Animation/clip.motion3", b"{}")
        self.write("outside.motion3.json", b"{}")
        entry = scan_assets(self.data, "remielle").live2d
        self.assertTrue(entry.valid)
        self.assertEqual(("idle", "point_finger"), entry.motions)
        self.assertEqual(
            {"idle_pool": ("point_finger",), "action_pool": ("point_finger",)},
            dict(entry.motion_groups),
        )
        view = public_view(scan_assets(self.data, "remielle"))["live2d"]
        self.assertEqual(["idle", "point_finger"], view["motions"])
        self.assertEqual(
            {"idle_pool": ["point_finger"], "action_pool": ["point_finger"]},
            view["motion_groups"],
        )

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


class AvatarImportTests(unittest.TestCase):
    """M15 slice 4: the one write this module is allowed to make."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.root = self.data / "assets" / "remielle"
        self.source = self.root.parent.parent / "Downloads"
        self.source.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_written_into_the_slot_and_the_source_is_left_alone(self):
        original = self.source / "我的照片.png"
        original.write_bytes(PNG)

        result = import_avatar(self.data, "remielle", "我的照片.png", PNG)

        self.assertEqual("png", result["asset"].kind)
        self.assertEqual([], result["removed"])
        self.assertEqual((self.root / "avatar.png").read_bytes(), PNG)
        self.assertEqual(original.read_bytes(), PNG)
        self.assertEqual([original.name], [p.name for p in self.source.iterdir()])
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_the_stale_sibling_kind_is_removed(self):
        self.root.mkdir(parents=True)
        (self.root / "avatar.webp").write_bytes(WEBP)
        (self.root / "background.png").write_bytes(PNG)
        (self.root / "avatar.mavatar").write_bytes(b"keep me")

        result = import_avatar(self.data, "remielle", "photo.jpg", JPEG)

        self.assertEqual(["avatar.webp"], result["removed"])
        self.assertTrue((self.root / "avatar.jpg").is_file())
        # Only the three avatar.* names are ever touched.
        self.assertTrue((self.root / "background.png").is_file())
        self.assertTrue((self.root / "avatar.mavatar").is_file())
        self.assertEqual("jpg", scan_assets(self.data, "remielle").avatar.kind)

    def test_reimport_replaces_the_same_kind(self):
        import_avatar(self.data, "remielle", "a.png", PNG)
        other = PNG + b"\x00" * 8

        result = import_avatar(self.data, "remielle", "b.png", other)

        self.assertEqual([], result["removed"])
        self.assertEqual(other, (self.root / "avatar.png").read_bytes())
        self.assertEqual(["avatar.png"], [p.name for p in self.root.iterdir()])

    def test_bad_input_is_refused_before_anything_is_written(self):
        cases = [
            ("avatar.gif", PNG, "只支持"),
            ("avatar.png", JPEG, "扩展名不符"),
            ("avatar.jpg", PNG, "扩展名不符"),
            ("avatar.png", b"", "没有读到"),
            ("avatar.png", PNG + b"\x00" * MAX_AVATAR_BYTES, "超过"),
            ("photo.txt", PNG, "只支持"),
        ]
        for filename, payload, needle in cases:
            with self.subTest(filename=filename):
                with self.assertRaises(AssetImportError) as raised:
                    import_avatar(self.data, "remielle", filename, payload)
                self.assertIn(needle, str(raised.exception))
        self.assertFalse(self.root.exists())

    def test_a_foreign_persona_id_cannot_pick_a_slot(self):
        for persona_id in ("", "..", "../remielle", "Remielle", "a/b", "remielle/x"):
            with self.subTest(persona_id=persona_id):
                with self.assertRaises(AssetImportError):
                    import_avatar(self.data, persona_id, "photo.png", PNG)

    def test_a_symlinked_slot_is_refused(self):
        target = self.data / "elsewhere"
        target.mkdir()
        self.root.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target, self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation needs extra privileges")
        with self.assertRaises(AssetImportError):
            import_avatar(self.data, "remielle", "photo.png", PNG)
        self.assertEqual([], list(target.iterdir()))

    def test_the_viewed_avatar_changes_after_import(self):
        before = public_view(scan_assets(self.data, "remielle"))["avatar"]
        import_avatar(self.data, "remielle", "photo.webp", WEBP)

        after = public_view(scan_assets(self.data, "remielle"))["avatar"]

        self.assertIsNone(before)
        self.assertEqual("webp", after["kind"])
        self.assertEqual(len(WEBP), after["size"])


if __name__ == "__main__":
    unittest.main()
