"""M15 slice 3: the context budget bar has one derivation and one renderer.

The bar is the only place the bands of a prompt window are drawn, so these
checks pin the arithmetic (the bands must tile the window), the transport (the
numbers reach both surfaces unchanged) and the shared component (one renderer,
never a second copy of the markup).
"""

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from agent_framework.companion import settings as app_settings
from agent_framework.companion import tokens as token_tools
from agent_framework.companion.app import create_application_service
from agent_framework.companion.app.projection import (
    _SEGMENT_LABELS,
    build_context_view,
    build_inspector_sections,
)
from agent_framework.companion.context import PromptBuild
from agent_framework.companion.runtime import CompanionRuntime
from agent_framework.companion.search import SearchService
from agent_framework.companion.store import CompanionStore
from agent_framework.companion.web import WebAdapter
from agent_framework.llm import OfflineLLMClient

ASSETS = (
    Path(__file__).resolve().parent.parent / "agent_framework" / "companion" / "web" / "assets"
)


class StubSearch:
    def search(self, query, limit=5):
        return []


def asset(name):
    return (ASSETS / name).read_text(encoding="utf-8")


def build_prompt(used, budget, history, layers, compressed=False):
    return PromptBuild(
        system="",
        history=[],
        stable_hash="",
        layers=dict(layers),
        budget_chars=budget,
        used_chars=used,
        history_chars=history,
        compressed=compressed,
        dropped_count=0,
        checkpoint_through=0,
    )


def stub_runtime(prompt, max_tokens=1000, window=0):
    return SimpleNamespace(
        last_context=prompt,
        settings={"max_tokens": max_tokens},
        max_context_chars=window,
    )


def band(view, key):
    for item in view.segments:
        if item.key == key:
            return item
    return None


def make_runtime(root):
    store = CompanionStore(root / "companion.db")
    runtime = CompanionRuntime(
        store=store,
        llm_client=OfflineLLMClient(),
        search_service=SearchService(StubSearch()),
        data_dir=root,
        session_id="main",
        offline=True,
        settings=app_settings.defaults(),
        config=None,
    )
    return store, runtime


class BudgetBandTests(unittest.TestCase):
    def setUp(self):
        self.prompt = build_prompt(
            used=1000,
            budget=8000,
            history=600,
            layers={"stable": 100, "session": 150, "runtime": 50, "memory": 100},
        )

    def test_occupied_bands_add_up_to_the_prompt(self):
        view = build_context_view(stub_runtime(self.prompt))

        self.assertEqual(
            view.context_used,
            sum(
                item.chars
                for item in view.segments
                if item.key in ("system", "recall", "docs", "history")
            ),
        )

    def test_the_bands_tile_the_window(self):
        view = build_context_view(stub_runtime(self.prompt))

        self.assertEqual(
            view.context_limit,
            sum(item.chars for item in view.segments),
        )
        self.assertEqual("free", view.segments[-1].key)

    def test_tokens_use_the_shared_conversion(self):
        view = build_context_view(stub_runtime(self.prompt))

        for item in view.segments:
            with self.subTest(band=item.key):
                self.assertEqual(token_tools.chars_to_tokens(item.chars), item.tokens)

    def test_every_band_is_named_in_words(self):
        view = build_context_view(stub_runtime(self.prompt))

        self.assertEqual(
            ["系统提示", "记忆召回", "历史对话", "回复预留", "空闲"],
            [item.label for item in view.segments],
        )
        self.assertEqual(["system", "recall", "history", "reply", "free"],
                         [item.key for item in view.segments])

    def test_the_unitemised_remainder_stays_in_the_system_band(self):
        # A system prompt is the layer sizes joined by separators, so the itemised
        # layers come out a few characters short of the prompt that was sent.
        prompt = build_prompt(
            used=1004,
            budget=8000,
            history=550,
            layers={"stable": 100, "session": 200, "runtime": 50, "memory": 100},
        )
        view = build_context_view(stub_runtime(prompt))

        self.assertEqual(354, band(view, "system").chars)
        self.assertEqual(100, band(view, "recall").chars)
        self.assertEqual(550, band(view, "history").chars)

    def test_a_document_hit_earns_its_own_named_band(self):
        prompt = build_prompt(
            used=1000,
            budget=8000,
            history=500,
            layers={
                "stable": 100,
                "session": 150,
                "runtime": 50,
                "memory": 100,
                "docs": 120,
            },
        )
        view = build_context_view(stub_runtime(prompt))

        self.assertEqual(
            ["系统提示", "记忆召回", "文档检索", "历史对话", "回复预留", "空闲"],
            [item.label for item in view.segments],
        )
        self.assertEqual(
            ["system", "recall", "docs", "history", "reply", "free"],
            [item.key for item in view.segments],
        )
        self.assertEqual(
            view.context_used,
            sum(
                item.chars
                for item in view.segments
                if item.key in ("system", "recall", "docs", "history")
            ),
        )

    def test_the_docs_band_does_not_inflate_the_system_remainder(self):
        # Same prompt as the case above with one 100-character layer named: the
        # remainder drops by exactly what the new band was charged.
        prompt = build_prompt(
            used=1004,
            budget=8000,
            history=550,
            layers={
                "stable": 100,
                "session": 200,
                "runtime": 50,
                "memory": 100,
                "docs": 100,
            },
        )
        view = build_context_view(stub_runtime(prompt))

        self.assertEqual(100, band(view, "docs").chars)
        self.assertEqual(254, band(view, "system").chars)

    def test_a_docs_layer_cannot_charge_more_than_the_prompt_used(self):
        prompt = build_prompt(
            used=600,
            budget=8000,
            history=200,
            layers={
                "stable": 100,
                "session": 150,
                "runtime": 50,
                "memory": 100,
                "docs": 400,
            },
        )
        view = build_context_view(stub_runtime(prompt))

        self.assertEqual(300, band(view, "docs").chars)
        self.assertIsNone(band(view, "system"))
        self.assertEqual(
            view.context_used,
            sum(
                item.chars
                for item in view.segments
                if item.key in ("system", "recall", "docs", "history")
            ),
        )

    def test_empty_bands_are_not_drawn(self):
        prompt = build_prompt(used=600, budget=8000, history=600, layers={})
        view = build_context_view(stub_runtime(prompt))

        self.assertIsNone(band(view, "recall"))
        self.assertIsNone(band(view, "system"))
        self.assertEqual(
            ["history", "reply", "free"], [item.key for item in view.segments]
        )

    def test_reservation_uses_the_saved_max_tokens(self):
        view = build_context_view(stub_runtime(self.prompt, max_tokens=1500))

        self.assertEqual(3000, band(view, "reply").chars)
        self.assertEqual(1500, band(view, "reply").tokens)

    def test_reservation_cannot_claim_space_the_prompt_already_uses(self):
        view = build_context_view(stub_runtime(self.prompt, max_tokens=9000, window=4000))

        self.assertEqual(4000, view.context_limit)
        self.assertEqual(3000, band(view, "reply").chars)
        self.assertEqual(0, band(view, "free").chars)

    def test_a_prompt_over_an_older_window_never_goes_negative(self):
        view = build_context_view(stub_runtime(self.prompt, max_tokens=4000, window=700))

        self.assertEqual(700, view.context_limit)
        self.assertEqual(300, band(view, "system").chars)
        self.assertIsNone(band(view, "reply"))
        self.assertEqual(0, band(view, "free").chars)
        self.assertEqual(100, view.percent)

    def test_no_assembled_prompt_still_means_no_bar(self):
        self.assertIsNone(build_context_view(SimpleNamespace(last_context=None)))
        self.assertIsNone(
            build_context_view(
                stub_runtime(build_prompt(used=10, budget=0, history=10, layers={}))
            )
        )

    def test_the_inspector_reports_usage_without_repeating_the_bands(self):
        runtime = stub_runtime(self.prompt)
        for name in (
            "desktop_status",
            "pending_proposals",
            "work_status",
            "latest_receipts",
            "skills_status",
        ):
            setattr(runtime, name, None)
        sections = {item.title: item.lines for item in build_inspector_sections(runtime)}

        self.assertEqual(
            ("用量：1000 / 8000 字符", "压缩：未压缩"), sections["上下文"]
        )


class BudgetLiveWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store, self.runtime = make_runtime(self.root)

    def test_saving_a_new_window_moves_the_free_band_without_a_new_turn(self):
        self.runtime.apply_settings({"context_tokens": 12000, "max_tokens": 2000})
        self.runtime.last_context = build_prompt(
            used=5000, budget=24000, history=4000, layers={"memory": 500}
        )
        before = build_context_view(self.runtime)
        self.assertEqual(24000, before.context_limit)
        self.assertEqual(4000, band(before, "reply").chars)

        self.runtime.apply_settings({"context_tokens": 8000, "max_tokens": 2000})
        after = build_context_view(self.runtime)

        self.assertEqual(16000, after.context_limit)
        self.assertEqual(before.context_used, after.context_used)
        self.assertEqual(15000, band(before, "free").chars)
        self.assertEqual(7000, band(after, "free").chars)

    def test_the_budget_falls_back_to_the_prompt_when_the_runtime_is_blind(self):
        self.runtime.last_context = build_prompt(
            used=100, budget=3000, history=100, layers={}
        )
        del self.runtime.max_context_chars

        view = build_context_view(self.runtime)

        self.assertEqual(3000, view.context_limit)


class BudgetHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        store, runtime = make_runtime(self.root)
        self.runtime = runtime
        service = create_application_service(runtime)
        self.adapter = WebAdapter(service, data_dir=self.root).start()
        self.addCleanup(self.adapter.stop)
        self.base = "http://" + self.adapter.address

    def get(self, path):
        request = urllib.request.Request(
            self.base + path, headers={"X-Companion-Token": self.adapter.token}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_settings_carries_the_bands_and_the_conversion_rate(self):
        self.runtime.last_context = build_prompt(
            used=1200, budget=8000, history=700, layers={"memory": 200}
        )
        payload = self.get("/api/settings")

        self.assertEqual(
            token_tools.CHARS_PER_TOKEN, payload["runtime"]["chars_per_token"]
        )
        context = payload["context"]
        self.assertEqual(1200, context["context_used"])
        self.assertEqual(
            context["context_limit"],
            sum(item["chars"] for item in context["segments"]),
        )
        self.assertNotIn("percent", json.dumps(context))

    def test_bootstrap_carries_the_same_bands_the_gauge_reads(self):
        self.assertIsNone(self.get("/api/bootstrap")["context"])

        self.runtime.last_context = build_prompt(
            used=1200,
            budget=8000,
            history=700,
            layers={"memory": 200, "docs": 150},
        )
        context = self.get("/api/bootstrap")["context"]

        self.assertEqual(
            ["system", "recall", "docs", "history", "reply", "free"],
            [item["key"] for item in context["segments"]],
        )

    def test_an_untouched_session_reports_no_bands_but_keeps_the_rate(self):
        payload = self.get("/api/settings")

        self.assertIsNone(payload["context"])
        self.assertEqual(
            token_tools.CHARS_PER_TOKEN, payload["runtime"]["chars_per_token"]
        )


class BudgetComponentContractTests(unittest.TestCase):
    def test_both_chrome_windows_load_the_shared_renderer_first(self):
        for page, script in (("index.html", "app.js"), ("settings.html", "settings.js")):
            html = asset(page)
            with self.subTest(page=page):
                self.assertLess(
                    html.index("/assets/context_bar.js"),
                    html.index("/assets/" + script),
                )

    def test_the_pages_delegate_instead_of_keeping_a_second_bar(self):
        builder = asset("context_bar.js")
        self.assertEqual(1, builder.count('class="ctxbar-seg"'))
        for name in ("app.js", "settings.js"):
            with self.subTest(script=name):
                script = asset(name)
                self.assertIn("CompanionContextBar.markup", script)
                self.assertNotIn("ctxbar-seg", script)

    def test_band_names_are_escaped_before_they_reach_the_dom(self):
        builder = asset("context_bar.js")

        self.assertIn("escapeHtml(item.label)", builder)
        self.assertNotIn(">${item.label}<", builder)

    def test_the_bands_are_text_as_well_as_colour(self):
        sheet = asset("tokens.css")

        for key in ("system", "recall", "docs", "history", "reply", "free"):
            self.assertIn("--ctx-{}:".format(key), sheet, key)
        self.assertIn(".ctxbar-seg[data-key=\"system\"]", sheet)
        self.assertIn(".ctxbar-name", sheet)
        self.assertIn(".ctxbar-value", sheet)
        self.assertIn(".ctxbar-raw", sheet)

    def test_the_renderer_and_the_projection_name_the_same_bands(self):
        script = asset("context_bar.js")
        start = script.index("const LABELS = {")
        block = script[start: script.index("};", start)]

        for key, label in _SEGMENT_LABELS.items():
            with self.subTest(band=key):
                self.assertIn('{}: "{}"'.format(key, label), block)
        # A band the projection can emit but the renderer never learned would be
        # drawn as an unnamed grey sliver.
        self.assertEqual(len(_SEGMENT_LABELS), block.count(': "'))

        sheet = asset("tokens.css")
        for key in _SEGMENT_LABELS:
            with self.subTest(band=key):
                self.assertIn("--ctx-{}:".format(key), sheet)
                if key == "free":
                    continue  # the unfilled track is already --ctx-free
                self.assertIn('.ctxbar-seg[data-key="{}"]'.format(key), sheet)
                self.assertIn(
                    '.ctxbar-item[data-key="{}"] .ctxbar-dot'.format(key), sheet
                )

    def test_the_bar_sizes_from_tokens_only(self):
        sheet = asset("tokens.css")
        track = sheet[sheet.index(".ctxbar-track {"): sheet.index(".ctxbar-seg {")]

        self.assertIn("min-height: var(--ctx-track)", track)
        self.assertIn("--ctx-track: calc(var(--fs-ui)", sheet)
        self.assertIn("flex: 0 0 var(--band)", sheet[sheet.index(".ctxbar-seg {"):])

    def test_the_settings_panel_says_why_a_bar_is_missing(self):
        script = asset("settings.js")

        self.assertIn("CompanionContextBar.reservation", script)
        self.assertIn("还没有发出过一轮对话", script)

    def test_the_gauge_opens_the_panel_through_the_drawer_aware_path(self):
        script = asset("app.js")
        opener = script[script.index("function openInspector(tab) {"):]
        opener = opener[: opener.index("\n  }")]

        # Below the inline breakpoint the inspector only exists as a drawer, so
        # every programmatic tab switch has to raise it or the bar is unreachable.
        self.assertIn("state.inspectorDrawer = true", opener)
        self.assertIn('openInspector("上下文")', script)
        self.assertIn('openInspector("来源")', script)
        self.assertNotIn("state.inspectorOpen = true", script.replace(opener, ""))


if __name__ == "__main__":
    unittest.main()
