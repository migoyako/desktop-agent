"""HTTP adapter that hands the Pixso design to the web front end."""

import json
import base64
import queue
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

from agent_framework.companion.app import create_application_service
from agent_framework.companion.context import PromptBuild
from agent_framework.companion.models import TaskCard, WorkTask, new_id
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion import tokens as token_tools
from agent_framework.companion.web import WebAdapter
from agent_framework.llm import OfflineLLMClient

# 1x1 transparent PNG
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class StubSearch:
    def search(self, query, limit=5):
        return []


class WebAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        store = CompanionStore(root / "companion.db")
        runtime = CompanionRuntime(
            store=store,
            llm_client=OfflineLLMClient(),
            search_service=SearchService(StubSearch()),
            data_dir=root,
            session_id="main",
            offline=True,
        )
        runtime.select_workspace(str(root))
        self.runtime = runtime
        self.service = create_application_service(runtime)
        self.adapter = WebAdapter(self.service, data_dir=root).start()
        self.base = "http://" + self.adapter.address

    def tearDown(self):
        self.adapter.stop()
        self.temp.cleanup()

    def request(self, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "X-Companion-Token": self.adapter.token if token is None else token,
                "Content-Type": "application/json",
            },
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "{}")

    def wait_for(self, predicate, timeout=15):
        deadline = time.time() + timeout
        payload = None
        while time.time() < deadline:
            _, payload = self.request("/api/bootstrap")
            if predicate(payload):
                return payload
            time.sleep(0.15)
        self.fail("condition not met: {}".format(payload))

    def test_pages_and_token(self):
        for page in ("/", "/settings", "/console", "/overlay"):
            with urllib.request.urlopen(
                "{}{}?t={}".format(self.base, page, self.adapter.token), timeout=10
            ) as response:
                self.assertEqual(200, response.status)
                self.assertIn(b"<!DOCTYPE html>", response.read())
        status, payload = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        for key in ("persona", "sessions", "messages", "ui", "status", "proactive"):
            self.assertIn(key, payload)
        status, _ = self.request("/api/bootstrap", token="wrong-token")
        self.assertEqual(403, status)

    def test_character_asset_slot_is_token_gated(self):
        status, payload = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        self.assertIsNone(payload["assets"]["avatar"])
        live2d = payload["assets"]["live2d"]
        self.assertFalse(live2d["available"])
        self.assertFalse(live2d["valid"])
        self.assertIn("model3.json", live2d["reason"])
        mesh = payload["assets"]["mesh"]
        self.assertFalse(mesh["available"])
        self.assertFalse(mesh["valid"])
        self.assertIn("avatar.mavatar", mesh["reason"])
        self.assertEqual("", payload["assets"]["renderer"])
        self.assertFalse(payload["assets"]["pet"]["available"])
        status, _ = self.request("/api/asset/avatar")
        self.assertEqual(404, status)
        status, _ = self.request("/api/asset/mesh")
        self.assertEqual(404, status)
        status, _ = self.request("/api/asset/avatar", token="wrong-token")
        self.assertEqual(403, status)
        for path in (
            "/api/asset/background",
            "/api/asset/mesh",
            "/api/asset/avatar/..",
            "/api/asset/avatar%2F..%2Fcompanion.db",
        ):
            with self.subTest(path=path):
                status, _ = self.request(path)
                self.assertEqual(404, status)

    def test_avatar_is_served_after_placing_the_file(self):
        folder = self.root / "assets" / "remielle"
        folder.mkdir(parents=True)
        (folder / "avatar.png").write_bytes(PNG_1PX)
        status, payload = self.request("/api/bootstrap")
        avatar = payload["assets"]["avatar"]
        self.assertEqual("png", avatar["kind"])
        self.assertEqual(len(PNG_1PX), avatar["size"])
        self.assertEqual("/api/asset/avatar", avatar["url"])
        source = "{}/api/asset/avatar?t={}".format(self.base, self.adapter.token)
        with urllib.request.urlopen(source, timeout=10) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("image/png", response.headers["Content-Type"])
            self.assertEqual(str(len(PNG_1PX)), response.headers["Content-Length"])
            self.assertEqual(PNG_1PX, response.read())

    def test_avatar_disappears_when_the_file_is_removed(self):
        folder = self.root / "assets" / "remielle"
        folder.mkdir(parents=True)
        avatar = folder / "avatar.png"
        avatar.write_bytes(PNG_1PX)
        status, payload = self.request("/api/bootstrap")
        self.assertIsNotNone(payload["assets"]["avatar"])
        avatar.unlink()
        status, payload = self.request("/api/bootstrap")
        self.assertIsNone(payload["assets"]["avatar"])
        status, _ = self.request("/api/asset/avatar")
        self.assertEqual(404, status)

    def test_background_is_served_after_placing_the_file(self):
        folder = self.root / "assets" / "remielle"
        folder.mkdir(parents=True)
        (folder / "background.png").write_bytes(PNG_1PX)
        status, payload = self.request("/api/bootstrap")
        background = payload["assets"]["background"]
        self.assertEqual("png", background["kind"])
        self.assertEqual(len(PNG_1PX), background["size"])
        self.assertEqual("/api/asset/background", background["url"])
        source = "{}/api/asset/background?t={}".format(self.base, self.adapter.token)
        with urllib.request.urlopen(source, timeout=10) as response:
            self.assertEqual(200, response.status)
            self.assertEqual("image/png", response.headers["Content-Type"])
            self.assertEqual(PNG_1PX, response.read())
        status, _ = self.request("/api/asset/background", token="wrong-token")
        self.assertEqual(403, status)

    def test_background_switch_persists_and_broadcasts(self):
        _, payload = self.request("/api/bootstrap")
        self.assertTrue(payload["ui"]["background_enabled"])
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request("/api/ui", {"background_enabled": False})
        finally:
            self.adapter.unsubscribe(channel)
        self.assertEqual(200, status)
        self.assertFalse(payload["background_enabled"])
        message = json.loads(channel.get(timeout=2))
        self.assertEqual("ui", message["type"])
        self.assertFalse(message["ui"]["background_enabled"])
        _, bootstrap = self.request("/api/bootstrap")
        self.assertFalse(bootstrap["ui"]["background_enabled"])
        status, payload = self.request("/api/ui", {"background_enabled": True})
        self.assertTrue(payload["background_enabled"])

    def live2d_model(self):
        entry = self.root / "assets" / "remielle" / "live2d"
        (entry / "textures").mkdir(parents=True)
        (entry / "textures" / "tex.png").write_bytes(PNG_1PX)
        (entry / "model3.json").write_text(
            json.dumps(
                {
                    "FileReferences": {
                        "Moc": "model.moc3",
                        "Textures": ["textures/tex.png"],
                        "Expressions": [
                            {"Name": "calm", "File": "expressions/calm.exp3.json"}
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        (entry / "model.moc3").write_bytes(b"MOC3" + b"\x00" * 12)
        return entry

    def test_live2d_entry_is_reported_and_served_by_path_token(self):
        self.live2d_model()
        status, payload = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        live2d = payload["assets"]["live2d"]
        self.assertTrue(live2d["valid"])
        self.assertTrue(live2d["available"])
        self.assertEqual(["calm"], live2d["expressions"])
        self.assertTrue(live2d["model_url"].startswith("/api/live2d/"))
        status, _ = self.request("/api/asset/live2d")
        self.assertEqual(404, status)
        with urllib.request.urlopen(
            self.base + live2d["model_url"], timeout=10
        ) as response:
            self.assertEqual(200, response.status)
            body = json.loads(response.read().decode("utf-8"))
        self.assertEqual("model.moc3", body["FileReferences"]["Moc"])
        token = self.adapter.token
        with urllib.request.urlopen(
            self.base + "/api/live2d/{}/textures/tex.png".format(token), timeout=10
        ) as response:
            self.assertEqual(200, response.status)
            self.assertEqual(PNG_1PX, response.read())
        for path in (
            "/api/live2d/{}/../model3.json".format(token),
            "/api/live2d/{}/%2e%2e/secret.png".format(token),
            "/api/live2d/{}/textures/../../companion.db".format(token),
            "/api/live2d/{}/textures/tex.gif".format(token),
        ):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(self.base + path, timeout=10)
                self.assertEqual(404, caught.exception.code)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                self.base + "/api/live2d/wrong-token/model3.json", timeout=10
            )
        self.assertEqual(403, caught.exception.code)

    def test_nested_live2d_model_serves_its_sibling_resources(self):
        folder = self.root / "assets" / "remielle" / "live2d" / "DeepSeek"
        texture = folder / "大肥鱼.4096" / "texture_00.png"
        texture.parent.mkdir(parents=True)
        texture.write_bytes(PNG_1PX)
        (folder / "大肥鱼.moc3").write_bytes(b"MOC3" + b"\x00" * 12)
        model = folder / "大肥鱼.model3.json"
        model.write_text(
            json.dumps(
                {
                    "FileReferences": {
                        "Moc": "大肥鱼.moc3",
                        "Textures": ["大肥鱼.4096/texture_00.png"],
                    }
                }
            ),
            encoding="utf-8",
        )

        _, payload = self.request("/api/bootstrap")
        live2d = payload["assets"]["live2d"]
        self.assertTrue(live2d["available"])
        self.assertEqual("DeepSeek/大肥鱼.model3.json", live2d["model_path"])
        self.assertIn(
            quote("DeepSeek/大肥鱼.model3.json", safe="/"),
            live2d["model_url"],
        )
        _, overlay = self.request("/api/overlay")
        self.assertEqual("live2d", overlay["pet"]["renderer"])
        self.assertTrue(overlay["pet"]["available"])

        with urllib.request.urlopen(
            self.base + live2d["model_url"], timeout=10
        ) as response:
            served = json.loads(response.read().decode("utf-8"))
        self.assertEqual("大肥鱼.moc3", served["FileReferences"]["Moc"])

        token = self.adapter.token
        for relative, expected in (
            ("DeepSeek/大肥鱼.moc3", b"MOC3" + b"\x00" * 12),
            ("DeepSeek/大肥鱼.4096/texture_00.png", PNG_1PX),
        ):
            with self.subTest(relative=relative):
                path = "/api/live2d/{}/{}".format(
                    token,
                    quote(relative, safe="/"),
                )
                with urllib.request.urlopen(self.base + path, timeout=10) as response:
                    self.assertEqual(200, response.status)
                    self.assertEqual(expected, response.read())

    def mesh_archive(self):
        folder = self.root / "assets" / "remielle"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "avatar.mavatar"
        curve = lambda x0, x1: {
            "x0": x0,
            "x1": x1,
            "top": [0.0] * 24,
            "bot": [1.0] * 24,
        }
        files = {
            "avatar.json": json.dumps(
                {"format": "mesh-avatar", "version": 1, "name": "web-mesh"}
            ),
            "rig.json": json.dumps({"version": 1}),
            "built/layers.json": json.dumps(
                {
                    "size": [16, 16],
                    "layers": {"eye0_ball": [0, 0, 2, 2]},
                    "eyes": [curve(2, 4), curve(8, 10)],
                }
            ),
            "built/base.png": PNG_1PX,
            "built/hairmask.png": PNG_1PX,
            "built/eye0_ball.png": PNG_1PX,
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return path

    def test_mesh_archive_is_served_and_selected_without_live2d(self):
        archive = self.mesh_archive()
        _, payload = self.request("/api/bootstrap")
        assets = payload["assets"]
        self.assertEqual("mesh", assets["renderer"])
        self.assertTrue(assets["mesh"]["valid"])
        self.assertTrue(assets["mesh"]["available"])
        self.assertEqual("web-mesh", assets["mesh"]["name"])
        self.assertEqual("mesh", assets["pet"]["renderer"])
        self.assertTrue(assets["pet"]["available"])
        self.assertTrue(assets["pet"]["url"].startswith("/api/asset/mesh"))
        request = urllib.request.Request(
            self.base + "/api/asset/mesh",
            headers={"X-Companion-Token": self.adapter.token},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual("application/zip", response.headers.get("Content-Type"))
            self.assertEqual(archive.read_bytes(), response.read())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                urllib.request.Request(
                    self.base + "/api/asset/mesh",
                    headers={"X-Companion-Token": "wrong-token"},
                ),
                timeout=10,
            )
        self.assertEqual(403, caught.exception.code)
        _, overlay = self.request("/api/overlay")
        self.assertEqual("mesh", overlay["renderer"])
        self.assertEqual("mesh", overlay["pet"]["renderer"])
        self.assertTrue(overlay["pet"]["available"])
        # A Live2D model file takes precedence over the mesh archive.
        self.live2d_model()
        _, payload = self.request("/api/bootstrap")
        self.assertEqual("live2d", payload["assets"]["renderer"])
        self.assertEqual("live2d", payload["assets"]["pet"]["renderer"])

    def test_invalid_mesh_archive_is_not_served(self):
        folder = self.root / "assets" / "remielle"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "avatar.mavatar").write_bytes(b"not a zip")
        status, body = self.request("/api/asset/mesh")
        self.assertEqual(404, status)
        self.assertIn("归档", body["error"])
        _, payload = self.request("/api/bootstrap")
        self.assertFalse(payload["assets"]["mesh"]["available"])
        self.assertFalse(payload["assets"]["pet"]["available"])

    def test_overlay_runtime_failure_disables_the_pet(self):
        self.live2d_model()
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request(
                "/api/overlay/runtime", {"ok": False, "reason": "WebGL 不可用"}
            )
        finally:
            self.adapter.unsubscribe(channel)
        self.assertEqual(200, status)
        self.assertFalse(payload["ok"])
        message = json.loads(channel.get(timeout=2))
        self.assertEqual("ui", message["type"])
        _, bootstrap = self.request("/api/bootstrap")
        live2d = bootstrap["assets"]["live2d"]
        self.assertFalse(live2d["available"])
        self.assertIn("WebGL", live2d["reason"])
        _, overlay = self.request("/api/overlay")
        self.assertFalse(overlay["pet"]["available"])
        self.request("/api/overlay/runtime", {"ok": True})
        _, bootstrap = self.request("/api/bootstrap")
        self.assertTrue(bootstrap["assets"]["live2d"]["available"])
        status, _ = self.request("/api/overlay/runtime", {"ok": "yes"})
        self.assertEqual(400, status)

    def test_overlay_moves_gestures_and_acks_them(self):
        entry = self.live2d_model()
        (entry / "Animation").mkdir()
        for name in ("idle", "point_finger"):
            (entry / "Animation" / (name + ".motion3.json")).write_bytes(b"{}")
        manifest = json.loads((entry / "model3.json").read_text(encoding="utf-8"))
        manifest["FileReferences"]["Motions"] = {
            "idle": [{"File": "Animation/idle.motion3.json"}],
            "point_finger": [{"File": "Animation/point_finger.motion3.json"}],
        }
        manifest["Groups"] = [
            {"Target": "Motion", "Name": "idle_pool", "Ids": ["point_finger"]}
        ]
        (entry / "model3.json").write_text(json.dumps(manifest), encoding="utf-8")

        _, overlay = self.request("/api/overlay")
        self.assertEqual(["idle", "point_finger"], overlay["pet"]["motions"])
        self.assertEqual(
            {"idle_pool": ["point_finger"]}, overlay["pet"]["motion_groups"]
        )
        self.assertEqual([], overlay["motion_events"])

        self.runtime.motion_events.push(["point_finger", "hold_grass"])
        _, overlay = self.request("/api/overlay")
        events = overlay["motion_events"]
        self.assertEqual(["point_finger", "hold_grass"], [row["name"] for row in events])
        status, payload = self.request("/api/overlay/motion", {"event_id": events[0]["id"]})
        self.assertEqual(200, status)
        self.assertEqual(1, payload["removed"])
        _, overlay = self.request("/api/overlay")
        self.assertEqual(
            ["hold_grass"], [row["name"] for row in overlay["motion_events"]]
        )
        status, _ = self.request("/api/overlay/motion", {"event_id": "abc"})
        self.assertEqual(200, status)

    def test_bootstrap_carries_proactive_events_for_the_main_window(self):
        self.service.runtime.store.add_proactive_event(
            session_id="main",
            source_type="system",
            source_id="m133",
            trigger_reason="probe",
            content="记得喝水，休息一下。",
            status="delivered",
        )
        status, payload = self.request("/api/bootstrap")
        self.assertEqual(200, status)
        events = payload["proactive_events"]
        self.assertEqual(1, len(events))
        self.assertEqual("记得喝水，休息一下。", events[0]["text"])
        self.assertFalse(events[0]["read"])
        status, result = self.request(
            "/api/proactive", {"action": "read", "event_id": events[0]["event_id"]}
        )
        self.assertEqual(200, status)
        _, payload = self.request("/api/bootstrap")
        self.assertTrue(payload["proactive_events"][0]["read"])

    def test_ui_update_persists_and_broadcasts(self):
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request(
                "/api/ui", {"theme": "pearl-white", "font_scale": 150}
            )
        finally:
            self.adapter.unsubscribe(channel)
        self.assertEqual(200, status)
        self.assertEqual("pearl-white", payload["theme"])
        self.assertEqual(150, payload["font_scale"])
        message = json.loads(channel.get(timeout=2))
        self.assertEqual("ui", message["type"])
        _, bootstrap = self.request("/api/bootstrap")
        self.assertEqual("pearl-white", bootstrap["ui"]["theme"])
        self.assertEqual(150, bootstrap["ui"]["font_scale"])

    def test_console_pages_are_shaped(self):
        expected = {
            "memory": "rows",
            "recall": "rows",
            "tasks": "rows",
            "affect": "snapshot",
            "proactive": "status",
            "audit": "rows",
            "settings": "workspace",
            "skills": "skills",
            "failures": "receipts",
        }
        for page, key in expected.items():
            status, payload = self.request("/api/console/" + page)
            self.assertEqual(200, status, page)
            self.assertIn(key, payload, page)

    def test_mood_correction_accepts_chinese_label(self):
        status, payload = self.request("/api/console/affect", {"mood": "开心"})
        self.assertEqual(200, status)
        self.assertEqual("positive", payload["user"]["mood_label"])
        self.assertEqual("开心", payload["user"]["mood_text"])
        _, bootstrap = self.request("/api/bootstrap")
        self.assertEqual("开心", bootstrap["settings_summary"]["mood_text"])

    def test_task_cards_expose_actions_through_adapter(self):
        self.service.runtime.create_work_task("把今天的总结发给项目群", force=True)
        _, data = self.request("/api/bootstrap")
        task = data["tasks"][0]
        self.assertEqual("PLAN_REVIEW", task["state"])
        self.assertEqual("把今天的总结发给项目群", task["title"])
        self.assertEqual("awaiting_confirmation", task["receipt"]["status"])
        status, payload = self.request(
            "/api/console/tasks",
            {"action": "reject", "task_id": task["task_id"], "reason": "测试拒绝"},
        )
        self.assertEqual(200, status)
        self.assertTrue(payload)
        _, after = self.request("/api/bootstrap")
        self.assertEqual("NORMAL", after["tasks"][0]["state"])

    def test_console_memory_purge_removes_record(self):
        memory_id = self.service.runtime.remember_persona("测试记忆")
        _, before = self.request("/api/console/memory")
        self.assertIn(memory_id, [row["memory_id"] for row in before["rows"]])
        status, payload = self.request(
            "/api/console/memory", {"action": "purge", "memory_id": memory_id}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        _, after = self.request("/api/console/memory")
        self.assertNotIn(memory_id, [row["memory_id"] for row in after["rows"]])

    def test_turn_streams_through_the_adapter(self):
        channel = self.adapter.subscribe()
        try:
            status, payload = self.request(
                "/api/turn", {"text": "你好", "mode": "chat"}
            )
            self.assertEqual(202, status)
            self.assertTrue(payload["run_id"])
            kinds = []
            while True:
                message = json.loads(channel.get(timeout=20))
                if message["type"] == "turn":
                    kinds.append(message["event"]["kind"])
                if message["type"] == "refresh":
                    break
        finally:
            self.adapter.unsubscribe(channel)
        self.assertIn("delta", kinds)
        self.assertEqual("done", kinds[-1])
        bootstrap = self.wait_for(
            lambda data: any(item["role"] == "assistant" for item in data["messages"])
        )
        self.assertIsNone(bootstrap["run"])
        self.assertEqual("user", bootstrap["messages"][-2]["role"])
        self.assertEqual("assistant", bootstrap["messages"][-1]["role"])

    def run_turn(self, text="你好"):
        status, payload = self.request("/api/turn", {"text": text, "mode": "chat"})
        self.assertEqual(202, status)
        self.assertTrue(payload["run_id"])
        return self.wait_for(
            lambda data: data["run"] is None
            and len(data["messages"]) >= 2
        )

    def test_bridge_turn_returns_the_answer_synchronously(self):
        status, payload = self.request(
            "/api/bridge/turn", {"text": "你好", "mode": "chat"}
        )
        self.assertEqual(200, status)
        self.assertEqual("completed", payload["status"])
        self.assertIn("离线演示模式", payload["answer"])
        self.assertEqual("main", payload["session_id"])
        self.assertTrue(payload["run_id"])
        rows = self.runtime.store.list_messages("main", limit=10)
        self.assertEqual(["user", "assistant"], [row["role"] for row in rows])
        self.assertEqual(payload["answer"], rows[-1]["content"])

    def test_bridge_turn_broadcasts_the_turn_to_ui_subscribers(self):
        channel = self.adapter.subscribe()
        try:
            status, _ = self.request(
                "/api/bridge/turn", {"text": "你好", "mode": "chat"}
            )
            self.assertEqual(200, status)
            kinds = []
            while True:
                message = json.loads(channel.get(timeout=20))
                if message["type"] == "turn":
                    kinds.append(message["event"]["kind"])
                if message["type"] == "refresh":
                    break
        finally:
            self.adapter.unsubscribe(channel)
        self.assertIn("delta", kinds)
        self.assertEqual("done", kinds[-1])

    def test_bridge_turn_rejects_empty_text(self):
        status, payload = self.request("/api/bridge/turn", {"text": "   "})
        self.assertEqual(200, status)
        self.assertEqual("invalid", payload["status"])
        self.assertEqual("消息不能为空。", payload["answer"])
        self.assertEqual([], self.runtime.store.list_messages("main", limit=10))

    def test_bridge_turn_requires_token(self):
        status, _ = self.request(
            "/api/bridge/turn", {"text": "你好"}, token="wrong-token"
        )
        self.assertEqual(403, status)

    def test_bootstrap_exposes_trailing_message_actions(self):
        bootstrap = self.run_turn()
        actions = bootstrap["message_actions"]
        self.assertEqual(bootstrap["messages"][-1]["id"], actions["regenerate_id"])
        self.assertEqual(0, actions["source_id"])

    def test_regenerate_endpoint_replaces_the_latest_reply(self):
        first = self.run_turn()
        replaced = first["messages"][-1]
        status, payload = self.request(
            "/api/messages/regenerate", {"message_id": replaced["id"]}
        )
        self.assertEqual(202, status)
        self.assertTrue(payload["run_id"])
        after = self.wait_for(
            lambda data: data["run"] is None
            and len(data["messages"]) == 2
            and data["messages"][-1]["id"] != replaced["id"]
        )
        self.assertEqual("user", after["messages"][-2]["role"])
        self.assertEqual("assistant", after["messages"][-1]["role"])

    def test_regenerate_refuses_a_user_message(self):
        bootstrap = self.run_turn()
        status, payload = self.request(
            "/api/messages/regenerate", {"message_id": bootstrap["messages"][0]["id"]}
        )
        self.assertEqual(409, status)
        self.assertIn("回复", payload["error"])

    def test_side_effect_turn_is_refused_over_http(self):
        bootstrap = self.run_turn()
        user = bootstrap["messages"][0]
        assistant = bootstrap["messages"][-1]
        self.service.runtime.store.create_work_task(
            WorkTask(
                task_id=new_id("task"),
                session_id="main",
                state="PLAN_REVIEW",
                card=TaskCard(
                    title="整理周报",
                    reason="用户要求",
                    spoken_summary="整理周报",
                    goal="整理周报",
                    executor="local",
                    project="",
                ),
                source_message_id=user["id"],
            )
        )
        status, payload = self.request(
            "/api/messages/regenerate", {"message_id": assistant["id"]}
        )
        self.assertEqual(409, status)
        self.assertIn("工作任务", payload["error"])
        _, after = self.request("/api/bootstrap")
        self.assertEqual(0, after["message_actions"]["regenerate_id"])

    def test_remember_action_saves_the_reply_once(self):
        bootstrap = self.run_turn()
        message_id = bootstrap["messages"][-1]["id"]
        status, payload = self.request(
            "/api/console/memory", {"action": "remember", "message_id": message_id}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["created"])
        self.assertIn(payload["memory_id"], [row["memory_id"] for row in payload["rows"]])
        status, again = self.request(
            "/api/console/memory", {"action": "remember", "message_id": message_id}
        )
        self.assertEqual(200, status)
        self.assertFalse(again["created"])
        self.assertEqual(payload["memory_id"], again["memory_id"])

    def test_event_stream_frames_every_sse_write(self):
        """Chromium drops an event stream whose first write is not a chunk, so the
        preamble has to be framed exactly like the events that follow it."""
        preamble = b"retry: 1000\n\n"
        framed = ("%X\r\n" % len(preamble)).encode("ascii") + preamble + b"\r\n"
        host, port = self.adapter.address.rsplit(":", 1)
        sock = socket.create_connection((host, int(port)), timeout=10)
        try:
            sock.sendall(
                (
                    "GET /api/events?t=%s HTTP/1.1\r\n"
                    "Host: %s\r\n"
                    "Accept: text/event-stream\r\n"
                    "Connection: close\r\n\r\n"
                    % (quote(self.adapter.token), self.adapter.address)
                ).encode("ascii")
            )
            head = b""
            deadline = time.time() + 10
            while framed not in head and time.time() < deadline:
                more = sock.recv(4096)
                if not more:
                    break
                head += more
        finally:
            sock.close()

        self.assertIn(b"Transfer-Encoding: chunked", head)
        self.assertIn(framed, head)
        self.assertEqual(head.index(b"\r\n\r\n") + 4, head.index(framed))

    def test_bootstrap_run_carries_the_collapsed_step_timeline(self):
        release = threading.Event()

        def slow_stream(text, mode, generation_id="", cancel_event=None):
            yield {"kind": "delta", "text": "一"}
            yield {"kind": "delta", "text": "二"}
            yield {
                "kind": "tool",
                "tool_name": "search_notes",
                "state": "running",
            }
            yield {
                "kind": "tool",
                "tool_name": "search_notes",
                "state": "done",
                "summary": "2 条",
            }
            release.wait(10)
            self.adapter.runtime.store.add_assistant("一二")
            yield {
                "kind": "done",
                "result": {
                    "answer": "一二",
                    "proposal": None,
                    "receipt": None,
                    "task": None,
                    "task_receipt": None,
                },
            }

        self.adapter.runtime.stream_message = slow_stream
        status, payload = self.request("/api/turn", {"text": "时间线", "mode": "chat"})
        self.assertEqual(202, status)
        try:
            data = self.wait_for(
                lambda item: item["run"] and len(item["run"]["steps"]) >= 4
            )
            steps = data["run"]["steps"]
            self.assertEqual(
                [(step["stage"], step["detail"]) for step in steps],
                [
                    ("thinking", ""),
                    ("answering", ""),
                    ("tool", "search_notes"),
                    ("tool", "search_notes：2 条"),
                ],
            )
            self.assertEqual(
                sorted(steps[0]), ["at", "detail", "sequence", "stage", "tool"]
            )
            self.assertTrue(all(step["at"] for step in steps))
            self.assertEqual([step["sequence"] for step in steps], [0, 1, 3, 4])
        finally:
            release.set()
        self.wait_for(lambda data: data["run"] is None)

    def test_bootstrap_context_carries_the_gauge_numbers(self):
        _, before = self.request("/api/bootstrap")
        self.assertIsNone(before["context"])

        self.adapter.runtime.last_context = PromptBuild(
            system="",
            history=[],
            stable_hash="",
            layers={},
            budget_chars=4000,
            used_chars=1500,
            history_chars=1500,
            compressed=True,
            dropped_count=0,
            checkpoint_through=0,
        )
        bootstrap = self.wait_for(lambda data: data["context"])

        context = bootstrap["context"]
        # The bands are the last prompt as assembled, the width is the window in
        # force now, so saving a new 上下文窗口 moves the free band at once.
        self.assertEqual(1500, context["context_used"])
        self.assertEqual(self.adapter.runtime.max_context_chars, context["context_limit"])
        self.assertTrue(context["compressed"])
        self.assertNotIn("percent", context)
        self.assertEqual(
            context["context_limit"],
            sum(item["chars"] for item in context["segments"]),
        )
        for item in context["segments"]:
            self.assertEqual(token_tools.chars_to_tokens(item["chars"]), item["tokens"], item["key"])
        keys = [item["key"] for item in context["segments"]]
        self.assertEqual(keys[-1], "free")
        self.assertIn("history", keys)
        lines = "\n".join(
            line
            for section in bootstrap["inspector"]
            if section["title"] == "上下文"
            for line in section["lines"]
        )
        self.assertIn(
            "用量：1500 / {} 字符".format(context["context_limit"]), lines
        )

    def test_context_gauge_stays_hidden_until_a_prompt_is_built(self):
        status, _ = self.request("/api/turn", {"text": "你好", "mode": "chat"})
        self.assertEqual(202, status)
        self.wait_for(
            lambda data: any(item["role"] == "assistant" for item in data["messages"])
        )
        self.assertIsNone(self.request("/api/bootstrap")[1]["context"])

    def test_overlay_payload_carries_font_scale(self):
        self.adapter.ui_state.set_font_scale(150)
        payload = self.adapter.overlay()
        self.assertEqual(150, payload["font_scale"])
        self.assertEqual("charcoal-pink", payload["theme"])

    def test_activate_endpoint_calls_shell_and_requires_token(self):
        called = []
        self.adapter.set_activate_handler(lambda: called.append(True))
        status, payload = self.request("/api/activate", {})
        self.assertEqual(200, status)
        self.assertTrue(payload["shown"])
        self.assertEqual([True], called)
        status, _ = self.request("/api/activate", {}, token="wrong-token")
        self.assertEqual(403, status)

    def test_activate_without_shell_returns_page_url(self):
        status, payload = self.request("/api/activate", {})
        self.assertEqual(200, status)
        self.assertFalse(payload["shown"])
        self.assertEqual(self.adapter.url, payload["url"])

    def test_second_launch_activates_running_adapter(self):
        from agent_framework.companion.activation import (
            activate,
            clear_endpoint,
            write_endpoint,
        )

        called = []
        self.adapter.set_activate_handler(lambda: called.append(True))
        write_endpoint(self.temp.name, self.adapter.port, self.adapter.token)
        try:
            self.assertTrue(activate(self.temp.name, timeout=2))
        finally:
            clear_endpoint(self.temp.name)
        self.assertEqual([True], called)

    def test_activation_ignores_stale_endpoint(self):
        import socket

        from agent_framework.companion.activation import (
            activate,
            clear_endpoint,
            write_endpoint,
        )

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        write_endpoint(self.temp.name, port, "stale-token")
        try:
            self.assertFalse(activate(self.temp.name, timeout=0.3))
        finally:
            clear_endpoint(self.temp.name)

    def test_assets_and_traversal(self):
        with urllib.request.urlopen(
            self.base + "/assets/tokens.css?t=" + self.adapter.token, timeout=10
        ) as response:
            self.assertEqual(200, response.status)
            self.assertIn(b"--accent", response.read())
        status, _ = self.request("/assets/../server.py")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
