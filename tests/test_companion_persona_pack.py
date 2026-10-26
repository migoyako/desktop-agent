"""Persona pack import and optional worldbook tests."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.persona import (
    MAX_FILE_BYTES,
    PersonaError,
    import_persona_pack,
    load_persona,
    parse_worldbook,
    preview_persona_pack,
    select_worldbook,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_PERSONA = REPO_ROOT / "data" / "personas" / "remielle"


class PersonaImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.source = self.root / "incoming" / "pack"

    def tearDown(self):
        self.temp.cleanup()

    def write_pack(
        self,
        persona_id="demo",
        name="演示人设",
        version="1.0.0",
        body="温和、克制。",
    ):
        self.source.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": persona_id,
            "name": name,
            "version": version,
            "source": {
                "work": "本地测试",
                "owner": "Local",
                "canon_cutoff": "v1",
            },
            "license": {
                "character_ip": "本地原创测试资料",
                "derivative_policy": "仅测试",
            },
        }
        (self.source / "persona.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.source / "persona.md").write_text(
            "# 演示\n\n{}".format(body),
            encoding="utf-8",
        )
        (self.source / "facts.md").write_text(
            "- 一条稳定事实。",
            encoding="utf-8",
        )
        (self.source / "worldbook.md").write_text(
            "## 黑墙\n\n黑墙是旧时代的前线。\n\n"
            "## 舞会\n\n旧时代的舞会需要舞伴。",
            encoding="utf-8",
        )
        return metadata

    def test_preview_reports_source_license_size_and_warnings(self):
        self.write_pack()
        (self.source / "cover.png").write_bytes(b"not a persona asset")

        preview = preview_persona_pack(self.source, self.data)

        self.assertEqual(preview["id"], "demo")
        self.assertEqual(preview["name"], "演示人设")
        self.assertEqual(preview["version"], "1.0.0")
        self.assertEqual(preview["source"]["owner"], "Local")
        self.assertEqual(preview["license"]["character_ip"], "本地原创测试资料")
        self.assertEqual(
            set(preview["files"]),
            {"persona.json", "persona.md", "facts.md", "worldbook.md"},
        )
        self.assertGreater(preview["total_bytes"], 0)
        self.assertEqual(preview["worldbook_entries"], 2)
        self.assertFalse(preview["target_exists"])
        self.assertTrue(
            any("忽略" in item for item in preview["warnings"])
        )

    def test_import_copies_only_persona_text_files(self):
        self.write_pack()
        (self.source / "cover.png").write_bytes(b"not a persona asset")

        imported = import_persona_pack(self.source, self.data)

        self.assertEqual(imported.name, "演示人设")
        target = self.data / "personas" / "demo"
        self.assertEqual(
            {child.name for child in target.iterdir()},
            {"persona.json", "persona.md", "facts.md", "worldbook.md"},
        )
        self.assertEqual(
            len(load_persona(self.data, "demo").worldbook_entries),
            2,
        )
        self.assertTrue(
            preview_persona_pack(self.source, self.data)["target_exists"]
        )

    def test_import_requires_explicit_overwrite(self):
        self.write_pack(body="第一版。")
        import_persona_pack(self.source, self.data)

        self.write_pack(body="第二版。")
        with self.assertRaises(PersonaError):
            import_persona_pack(self.source, self.data)
        self.assertIn("第一版", load_persona(self.data, "demo").prompt_text)

        import_persona_pack(self.source, self.data, overwrite=True)
        self.assertIn("第二版", load_persona(self.data, "demo").prompt_text)

    def test_invalid_metadata_is_rejected(self):
        self.write_pack(persona_id="../escape")
        with self.assertRaises(PersonaError):
            preview_persona_pack(self.source, self.data)

        self.write_pack(name="")
        with self.assertRaises(PersonaError):
            preview_persona_pack(self.source, self.data)

    def test_oversized_file_is_rejected(self):
        self.write_pack()
        (self.source / "facts.md").write_text(
            "x" * (MAX_FILE_BYTES + 1),
            encoding="utf-8",
        )
        with self.assertRaises(PersonaError):
            preview_persona_pack(self.source, self.data)

    def test_malformed_json_is_rejected(self):
        self.write_pack()
        (self.source / "persona.json").write_text(
            "{not json",
            encoding="utf-8",
        )
        with self.assertRaises(PersonaError):
            preview_persona_pack(self.source, self.data)


class WorldbookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"

    def tearDown(self):
        self.temp.cleanup()

    def test_parse_worldbook_splits_entries(self):
        entries = parse_worldbook(
            "# 世界书\n\n## 黑墙\n\n黑墙是前线。\n\n## 舞会\n\n旧时代舞会。"
        )

        self.assertEqual([entry.title for entry in entries], ["黑墙", "舞会"])
        self.assertEqual(entries[0].content, "黑墙是前线。")

    def test_select_worldbook_only_returns_matching_entries(self):
        entries = parse_worldbook(
            "## 黑墙\n\n黑墙是旧时代的前线。\n\n"
            "## 舞会\n\n旧时代的舞会需要舞伴。"
        )

        matched = select_worldbook(entries, "黑墙是什么")
        self.assertEqual([entry.title for entry in matched], ["黑墙"])
        self.assertEqual(select_worldbook(entries, "今天天气如何"), [])

    def test_worldbook_is_not_part_of_stable_hash(self):
        target = self.data / "personas" / "remielle"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(BUNDLED_PERSONA, target)
        before = load_persona(self.data, "remielle")
        with (target / "worldbook.md").open("a", encoding="utf-8") as handle:
            handle.write("\n\n## 测试条目\n\n只影响召回，不影响稳定前缀。\n")
        after = load_persona(self.data, "remielle")

        self.assertEqual(before.stable_hash, after.stable_hash)
        self.assertEqual(
            len(after.worldbook_entries),
            len(before.worldbook_entries) + 1,
        )


if __name__ == "__main__":
    unittest.main()
