"""M16 gesture slice: the model vocabulary, [[action:]] markers and the queue."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from agent_framework.companion.motion import (
    ACTION_POOL_GROUP,
    MAX_ACTIONS_PER_TURN,
    MotionQueue,
    gesture_prompt,
    plan_actions,
    vocabulary,
)
from agent_framework.companion.models import AgentMode, SearchResult
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.llm import LLMClient, LLMResponse, StreamEvent

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def marker(name, kind="action"):
    return {"kind": kind, "value": name}


class VocabularyTests(unittest.TestCase):
    def test_declared_action_pool_is_preferred(self):
        names = vocabulary(
            {
                "motions": ["idle", "hand_to_chest", "point_finger", "hold_grass"],
                "expressions": ["warm"],
                "motion_groups": {
                    ACTION_POOL_GROUP: ["hold_grass", "point_finger"],
                    "idle_pool": ["point_finger"],
                },
            }
        )
        self.assertEqual(["hold_grass", "point_finger", "warm"], names)

    def test_without_a_declared_pool_every_non_idle_group_is_offered(self):
        names = vocabulary(
            {
                "motions": ["idle", "hand_to_chest", "point_finger"],
                "expressions": ["warm", "warm", "low"],
            }
        )
        self.assertEqual(["hand_to_chest", "point_finger", "warm", "low"], names)

    def test_empty_or_bogus_payload_has_no_vocabulary(self):
        self.assertEqual([], vocabulary({}))
        self.assertEqual([], vocabulary(None))
        self.assertEqual([], vocabulary({"motions": "idle", "expressions": None}))

    def test_prompt_lists_only_the_known_ids(self):
        text = gesture_prompt(["point_finger", "hold_grass"])
        self.assertIn("point_finger", text)
        self.assertIn("hold_grass", text)
        self.assertIn(str(MAX_ACTIONS_PER_TURN), text)


class PlanActionsTests(unittest.TestCase):
    KNOWN = ["hold_grass", "point_finger", "warm"]

    def test_unknown_marker_is_dropped_not_replaced(self):
        self.assertEqual(
            ["point_finger"],
            plan_actions([marker("fly_to_moon"), marker("point_finger")], self.KNOWN),
        )

    def test_lookup_is_case_insensitive_and_keeps_the_model_spelling(self):
        self.assertEqual(["Point_Finger"], plan_actions([marker("POINT_FINGER")], ["Point_Finger"]))

    def test_repeats_collapse_and_the_cap_holds(self):
        chosen = plan_actions(
            [marker("warm"), marker("warm"), marker("hold_grass"), marker("point_finger")],
            self.KNOWN,
        )
        self.assertEqual(["warm", "hold_grass", "point_finger"], chosen)
        self.assertEqual(
            MAX_ACTIONS_PER_TURN,
            len(plan_actions([marker(name) for name in self.KNOWN] + [marker("warm")], self.KNOWN)),
        )

    def test_other_marker_kinds_and_junk_are_ignored(self):
        self.assertEqual(
            [],
            plan_actions(
                [{"kind": "mood", "value": "warm"}, marker("hold_grass", kind=""), None, marker("")],
                ["warm"],
            ),
        )


class MotionQueueTests(unittest.TestCase):
    def test_ids_are_monotonic_and_pending_is_capped(self):
        queue = MotionQueue()
        rows = queue.push(["warm", "point_finger"])
        self.assertEqual([1, 2], [row["id"] for row in rows])
        self.assertEqual(1, len(queue.pending(limit=1)))
        self.assertEqual(["warm", "point_finger"], [row["name"] for row in queue.pending()])

    def test_ack_removes_only_that_event(self):
        queue = MotionQueue()
        rows = queue.push(["warm", "point_finger"])
        self.assertEqual(1, queue.mark_read(rows[0]["id"]))
        self.assertEqual(["point_finger"], [row["name"] for row in queue.pending()])
        self.assertEqual(0, queue.mark_read(rows[0]["id"]))

    def test_bad_ack_and_overflow(self):
        queue = MotionQueue(limit=2)
        self.assertEqual(0, queue.mark_read(None))
        self.assertEqual(0, queue.mark_read("abc"))
        queue.push(["a", "b", "c"])
        self.assertEqual(["b", "c"], [row["name"] for row in queue.pending(limit=5)])
        queue.clear()
        self.assertEqual([], queue.pending())


class StubSearch:
    def search(self, query, limit=5):
        return [
            SearchResult(title="t", url="https://example.com/a", snippet="s")
        ]


class ScriptedLLM(LLMClient):
    """Streams one answer; chunks split the marker to prove the tail is read."""

    def __init__(self, chunks, content):
        self.chunks = chunks
        self.content = content
        self.messages = []

    def stream_chat(self, messages, tools=None):
        self.messages.append(messages)
        for chunk in self.chunks:
            yield StreamEvent(kind="delta", text=chunk)
        yield StreamEvent(kind="done", response=LLMResponse(content=self.content))


class ChatOnlyLLM(LLMClient):
    def __init__(self, content):
        self.content = content
        self.messages = []

    def chat(self, messages, tools=None):
        self.messages.append(messages)
        return LLMResponse(content=self.content)


class RuntimeMotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.data = root / "data"
        self.store = CompanionStore(self.data / "companion.db")

    def tearDown(self):
        self.temp.cleanup()

    def runtime(self, llm):
        runtime = CompanionRuntime(
            self.store,
            llm,
            SearchService(StubSearch()),
            self.data,
            offline=False,
        )
        runtime.select_workspace(str(self.workspace))
        return runtime

    def model(self, groups=True):
        entry = self.data / "assets" / "remielle" / "live2d"
        (entry / "textures").mkdir(parents=True, exist_ok=True)
        (entry / "textures" / "tex.png").write_bytes(PNG)
        (entry / "model.moc3").write_bytes(b"MOC3" + b"\x00" * 12)
        (entry / "Animation").mkdir(parents=True, exist_ok=True)
        for name in ("idle", "point_finger", "hold_grass", "hand_to_chest"):
            (entry / "Animation" / (name + ".motion3.json")).write_bytes(b"{}")
        payload = {
            "FileReferences": {
                "Moc": "model.moc3",
                "Textures": ["textures/tex.png"],
                "Expressions": [{"Name": "warm", "File": "Exp3/warm.exp3.json"}],
                "Motions": {
                    "idle": [{"File": "Animation/idle.motion3.json"}],
                    "point_finger": [
                        {"File": "Animation/point_finger.motion3.json"}
                    ],
                    "hold_grass": [
                        {"File": "Animation/hold_grass.motion3.json"}
                    ],
                    "hand_to_chest": [
                        {"File": "Animation/hand_to_chest.motion3.json"}
                    ],
                },
            }
        }
        if groups:
            payload["Groups"] = [
                {"Target": "Motion", "Name": "idle_pool", "Ids": ["point_finger"]},
                {
                    "Target": "Motion",
                    "Name": ACTION_POOL_GROUP,
                    "Ids": ["hold_grass", "point_finger", "gone_group"],
                },
            ]
        (entry / "model3.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return entry

    def system_text(self, messages):
        parts = []
        for item in messages:
            if str(getattr(item, "role", "") or "") != "system":
                continue
            parts.append(str(getattr(item, "content", "") or ""))
        return "\n".join(parts)

    def test_vocabulary_comes_from_the_manifest_and_skips_the_dead_clip(self):
        self.model()
        runtime = self.runtime(ScriptedLLM(["x"], "x"))
        names = runtime.motion_vocabulary()
        self.assertIn("point_finger", names)
        self.assertIn("hold_grass", names)
        self.assertNotIn("hand_to_chest", names)
        self.assertNotIn("idle", names)

    def test_without_declared_groups_all_non_idle_clips_are_offered(self):
        self.model(groups=False)
        names = self.runtime(ScriptedLLM(["x"], "x")).motion_vocabulary()
        self.assertIn("hand_to_chest", names)

    def test_no_model_means_no_vocabulary_and_no_prompt_block(self):
        llm = ScriptedLLM(["你好"], "你好")
        runtime = self.runtime(llm)
        self.assertEqual([], runtime.motion_vocabulary())
        list(runtime.stream_message("你好", AgentMode.CHAT))
        self.assertNotIn("[[action:", self.system_text(llm.messages[0]))

    def test_streaming_markers_become_one_shot_events(self):
        self.model()
        llm = ScriptedLLM(
            ["看这里[[a", "ction:point_finger]]然后[[action:hold_grass]]", "结束"],
            "看这里[[action:point_finger]]然后[[action:hold_grass]]结束",
        )
        runtime = self.runtime(llm)
        events = list(runtime.stream_message("做个动作", AgentMode.CHAT))
        self.assertEqual(
            "看这里然后结束",
            "".join(item["text"] for item in events if item["kind"] == "delta"),
        )
        pending = runtime.pending_motion_events()
        self.assertEqual(["point_finger", "hold_grass"], [row["name"] for row in pending])
        # One-shot: the overlay acking the first event does not replay it.
        runtime.mark_motion_read(pending[0]["id"])
        self.assertEqual(
            ["hold_grass"], [row["name"] for row in runtime.pending_motion_events()]
        )

    def test_non_streaming_answer_is_treated_the_same(self):
        self.model()
        llm = ChatOnlyLLM("好的[[action:hold_grass]]")
        runtime = self.runtime(llm)
        result = runtime.handle_message("给我一根狗尾草", AgentMode.CHAT)
        self.assertEqual("好的", result["answer"])
        self.assertEqual(
            ["hold_grass"], [row["name"] for row in runtime.pending_motion_events()]
        )

    def test_hallucinated_id_is_a_silent_no_op(self):
        self.model()
        llm = ChatOnlyLLM("跳舞[[action:dance]])")
        runtime = self.runtime(llm)
        runtime.handle_message("跳个舞", AgentMode.CHAT)
        self.assertEqual([], runtime.pending_motion_events())

    def test_the_prompt_only_lists_ids_the_model_ships(self):
        self.model()
        llm = ChatOnlyLLM("好")
        runtime = self.runtime(llm)
        runtime.handle_message("你好", AgentMode.CHAT)
        text = self.system_text(llm.messages[0])
        self.assertIn("[[action:", text)
        self.assertIn("point_finger", text)
        self.assertNotIn("hand_to_chest", text)


if __name__ == "__main__":
    unittest.main()
