"""Real-browser scaling and typography regression for the web front end (M12.1/M12.2, D55/R21)."""

import unittest

from agent_framework.companion.web.layout_audit import run_audit

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def describe(problem):
    return "{} {}x{} {}% {} +{}".format(
        problem["page"],
        problem["size"][0],
        problem["size"][1],
        problem["scale"],
        problem["axis"],
        problem["node"],
        problem["delta"],
    )


@unittest.skipIf(sync_playwright is None, "未安装 playwright（见 requirements-optional.txt）")
class WebLayoutAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.report = run_audit()
        except Exception as error:  # browser binary missing or unable to start
            cls.report = None
            cls.skip_reason = "浏览器不可用：{}".format(error)

    def test_no_control_clips_its_own_text(self):
        if self.report is None:
            self.skipTest(self.skip_reason)
        self.assertEqual([], [describe(item) for item in self.report["problems"]])

    def test_window_controls_stay_reachable_and_degrade_to_icons(self):
        if self.report is None:
            self.skipTest(self.skip_reason)
        minimize = [
            item for item in self.report["controls"]
            if item["page"] == "main" and item["name"] == "minimize"
        ]
        self.assertEqual(8, len(minimize))
        self.assertTrue(all(item["hit"] for item in minimize))
        for item in minimize:
            width, _height = item["size"]
            # A label needs room twice over: the raw window has to clear the media
            # breakpoint, and the window has to clear it in 100% equivalents too.
            room = width >= 900 and width * 100 >= 780 * item["scale"]
            with self.subTest(size=item["size"], scale=item["scale"]):
                self.assertEqual(not room, item["iconOnly"])

    def test_the_reading_measure_stays_a_measure(self):
        if self.report is None:
            self.skipTest(self.skip_reason)
        rows = [item for item in self.report["measures"] if item["page"] == "main"]
        self.assertTrue(rows)
        widest = max(item["chars"] for item in rows)
        narrowest = min(item["chars"] for item in rows)
        self.assertLessEqual(widest, 45, "a line of {} CJK glyphs is unreadable".format(widest))
        self.assertGreaterEqual(narrowest, 16, "the column collapsed to {} glyphs".format(narrowest))

    def test_the_transcript_keeps_its_share_of_the_window(self):
        if self.report is None:
            self.skipTest(self.skip_reason)
        bars = [item for item in self.report["chrome"] if item["page"] == "main"]
        self.assertTrue(bars)
        # The binding case is 200% type in the 1024x576 minimum window, where
        # the top bar and the two-line composer are both at their largest.
        share = min(100.0 * item["chat"] / item["viewport"] for item in bars)
        self.assertGreaterEqual(share, 25, "the transcript only keeps {:.0f}%".format(share))

    def test_a_user_background_keeps_the_layout_and_skips_the_overlay(self):
        if self.report is None:
            self.skipTest(self.skip_reason)
        try:
            report = run_audit(scales=(100,), background=True)
        except Exception as error:
            self.skipTest("浏览器不可用：{}".format(error))
        self.assertEqual([], [describe(item) for item in report["problems"]])
        checks = report["background"]
        self.assertTrue(
            any(
                item["page"] == "main"
                and item["on"]
                and item["washed"]
                and item["image"]
                for item in checks
            )
        )
        overlay = [item for item in checks if item["page"] == "overlay"]
        self.assertTrue(overlay)
        self.assertTrue(
            all(not item["on"] and not item["washed"] and not item["image"] for item in overlay)
        )


if __name__ == "__main__":
    unittest.main()
