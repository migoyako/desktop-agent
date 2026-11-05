"""M3.5 recall evaluation baseline tests."""

import contextlib
import io
import unittest

from agent_framework.companion.eval_recall import (
    CASES,
    DOC_CASES,
    DOC_CORPUS,
    evaluate,
    evaluate_docs,
    format_docs_report,
    format_report,
    main,
)


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


class DocEvalTest(unittest.TestCase):
    """文档链评测：指标只算词法题，语义题单独报，两条链的分数不可混。"""

    @classmethod
    def setUpClass(cls):
        cls.report = evaluate_docs()

    def test_the_doc_case_set_declares_both_halves_of_the_boundary(self):
        lexical = [case for case in DOC_CASES if case.lexical]
        semantic = [case for case in DOC_CASES if not case.lexical]

        self.assertGreaterEqual(len(DOC_CORPUS), 3)
        self.assertGreaterEqual(len(lexical), 6)
        self.assertGreaterEqual(len(semantic), 2)
        self.assertEqual(self.report["lexical_cases"], len(lexical))
        self.assertEqual(self.report["semantic_cases"], len(semantic))
        # 点名必须用真实的标题路径：小节名前面带着它的父级标题。
        self.assertIn(" > ", " ".join(case.heading for case in lexical))
        self.assertTrue(any(case.heading == "" for case in lexical), "缺纯文本定长块用例")

    def test_the_lexical_subset_is_recoverable_within_the_budget(self):
        lexical = [item for item in self.report["results"] if item["lexical"]]

        self.assertTrue(all(item["rank"] > 0 for item in lexical), lexical)
        self.assertGreaterEqual(self.report["hit@5"], 0.99)
        self.assertGreaterEqual(self.report["hit@1"], 0.85)
        self.assertGreaterEqual(self.report["mrr"], 0.90)

    def test_the_lexical_boundary_stays_visible_instead_of_being_tuned_away(self):
        semantic = [item for item in self.report["results"] if not item["lexical"]]

        self.assertEqual(len(semantic), self.report["semantic_cases"])
        # 至少有一题词法答不上来；碰巧命中字面的那题也不许算进 hit@1/hit@5。
        self.assertLess(self.report["semantic_hits"], len(semantic))
        missed = [item for item in semantic if item["rank"] == 0]
        self.assertTrue(any("怎么汇报" in item["query"] for item in missed))

    def test_both_chains_are_reported_apart(self):
        memory_line = format_report(evaluate()).splitlines()[0]
        doc_text = format_docs_report(self.report)
        doc_line = doc_text.splitlines()[0]

        self.assertFalse(memory_line.startswith("docs:"))
        self.assertTrue(doc_line.startswith("docs:"))
        self.assertIn("hit@1", doc_line)
        self.assertIn("语义题命中", doc_text)

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            self.assertEqual(0, main())
        printed = captured.getvalue()
        self.assertIn("cases=26", printed)
        self.assertIn("docs: cases=", printed)


if __name__ == "__main__":
    unittest.main()
