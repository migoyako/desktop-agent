"""确定性的中文召回评测集：种子记忆 → 查询 → hit@1 / hit@5 / MRR。

用例只依赖本地规则抽取与 FTS/关键词召回，不调用模型；用于在调整分词、
评分或衰减参数后快速比较召回质量。
"""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

from .memory import MemoryService
from .models import AgentMode
from .store import CompanionStore


@dataclass(frozen=True)
class RecallCase:
    seed: str
    query: str
    expected: str


CASES = (
    RecallCase("请记住我喜欢喝咖啡。", "咖啡", "我喜欢喝咖啡"),
    RecallCase("请记住我喜欢喝茶。", "茶", "我喜欢喝茶"),
    RecallCase("请记住我不喜欢香菜。", "香菜", "我不喜欢香菜"),
    RecallCase(
        "请记住我最近喜欢看科幻电影。",
        "科幻电影",
        "我最近喜欢看科幻电影",
    ),
    RecallCase("请记住我习惯早起。", "习惯", "我习惯早起"),
    RecallCase(
        "请记住我希望周末去爬山。",
        "爬山",
        "我希望周末去爬山",
    ),
    RecallCase("请记住我更喜欢猫。", "更喜欢", "我更喜欢猫"),
    RecallCase(
        "请记住我喜欢听爵士乐。",
        "爵士",
        "我喜欢听爵士乐",
    ),
    RecallCase("请记住我不喜欢熬夜。", "熬夜", "我不喜欢熬夜"),
    RecallCase("请记住我喜欢吃草莓。", "草莓", "我喜欢吃草莓"),
    RecallCase(
        "请记住大家都叫我林舟。",
        "林舟",
        "大家都叫我林舟",
    ),
    RecallCase(
        "请记住生日是5月1日。",
        "生日",
        "生日是5月1日",
    ),
    RecallCase(
        "请记住职业是产品设计师。",
        "设计师",
        "职业是产品设计师",
    ),
    RecallCase("请记住我对花生过敏。", "花生", "我对花生过敏"),
    RecallCase("请记住我的猫叫团子。", "团子", "我的猫叫团子"),
    RecallCase("请记住我住在杭州。", "杭州", "我住在杭州"),
    RecallCase(
        "请记住我每周三打羽毛球。",
        "羽毛球",
        "我每周三打羽毛球",
    ),
    RecallCase("请记住我会弹吉他。", "吉他", "我会弹吉他"),
    RecallCase(
        "请记住我最喜欢的颜色是蓝色。",
        "蓝色",
        "我最喜欢的颜色是蓝色",
    ),
    RecallCase("请记住我讨厌下雨天。", "下雨", "我讨厌下雨天"),
    RecallCase("请记住我在学日语。", "日语", "我在学日语"),
    RecallCase(
        "请记住我需要早起赶地铁。",
        "地铁",
        "我需要早起赶地铁",
    ),
    RecallCase("请记住我的手机是安卓。", "安卓", "我的手机是安卓"),
    RecallCase("请记住我怕打雷。", "打雷", "我怕打雷"),
    RecallCase("请记住我在学 Python。", "python", "我在学 Python"),
    RecallCase(
        "请记住我的项目叫 Aurora。",
        "aurora",
        "我的项目叫 Aurora",
    ),
)

SESSION_ID = "recall-eval"


def evaluate(cases=CASES, limit: int = 5) -> dict:
    limit = max(1, int(limit))
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        store = CompanionStore(data_dir / "companion.db")
        store.ensure_session(SESSION_ID, AgentMode.CHAT)
        memory = MemoryService(store)
        for case in cases:
            message_id = store.add_message(
                SESSION_ID,
                "user",
                case.seed,
                AgentMode.CHAT,
            )
            memory.observe_user_message(SESSION_ID, message_id, case.seed)
        by_content = {
            item.content: item.memory_id
            for item in store.list_memories(
                SESSION_ID,
                statuses=["confirmed"],
            )
        }
        results = []
        missing = []
        for case in cases:
            expected_id = by_content.get(case.expected)
            if expected_id is None:
                missing.append(case.expected)
            ranked = memory.recall(SESSION_ID, case.query, limit=limit)
            ids = [item.memory_id for _, item, _ in ranked]
            rank = (
                ids.index(expected_id) + 1
                if expected_id is not None and expected_id in ids
                else 0
            )
            results.append(
                {
                    "query": case.query,
                    "expected": case.expected,
                    "rank": rank,
                    "recalled": ids,
                }
            )

    total = len(results)
    hit1 = sum(1 for item in results if item["rank"] == 1) / float(total)
    hit5 = sum(1 for item in results if 0 < item["rank"] <= limit) / float(
        total
    )
    mrr = sum(
        1.0 / item["rank"] for item in results if item["rank"] > 0
    ) / float(total)
    return {
        "cases": total,
        "limit": limit,
        "hit@1": round(hit1, 4),
        "hit@5": round(hit5, 4),
        "mrr": round(mrr, 4),
        "missing": missing,
        "results": results,
    }


def format_report(report: dict) -> str:
    lines = [
        "cases={cases} limit={limit} hit@1={hit@1:.4f} "
        "hit@5={hit@5:.4f} mrr={mrr:.4f}".format(**report)
    ]
    if report["missing"]:
        lines.append("缺少期望记忆：" + "、".join(report["missing"]))
    for item in report["results"]:
        if item["rank"] == 1:
            continue
        lines.append(
            "未命中首位：query={} expected={} rank={}".format(
                item["query"],
                item["expected"],
                item["rank"] or "miss",
            )
        )
    return "\n".join(lines)


def main() -> int:
    report = evaluate()
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
