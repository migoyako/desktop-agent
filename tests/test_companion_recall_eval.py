"""M3.5 recall evaluation baseline tests."""

import unittest

from agent_framework.companion.eval_recall import CASES, evaluate


class RecallEvalTest(unittest.TestCase):
    def test_case_set_covers_common_query_shapes(self):
        queries = [case.query for case in CASES]

        self.assertGreaterEqual(len(CASES), 20)
        self.assertTrue(any(len(query) == 1 for query in queries))
        self.assertTrue(any(len(query) >= 4 for query in queries))
        self.assertTrue(
            any(
                any(char.isascii() and char.isalpha() for char in query)
                for query in queries
            )
        )

    def test_baseline_hit_at_five_and_mrr(self):
        report = evaluate()

        self.assertEqual(report["cases"], len(CASES))
        self.assertEqual(report["missing"], [])
        self.assertGreaterEqual(report["hit@5"], 0.95)
        self.assertGreaterEqual(report["mrr"], 0.90)


if __name__ == "__main__":
    unittest.main()
