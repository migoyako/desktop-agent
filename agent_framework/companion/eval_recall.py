"""确定性的中文召回评测集：种子记忆 → 查询 → hit@1 / hit@5 / MRR。

用例只依赖本地规则抽取与 FTS/关键词召回，不调用模型；用于在调整分词、
评分或衰减参数后快速比较召回质量。

文档链单独一套样本（``evaluate_docs``）：它与记忆链的分数不可比，指标也必须
分开报。里面刻意留了 ``lexical=False`` 的语义题——词法检索按设计答不上来，
留着是为了给"RAG 到底行不行"划一条能重复验证的界，而不是当失败掩盖掉。
"""

from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path

from .documents import DocumentService
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


@dataclass(frozen=True)
class DocCase:
    """一条文档检索用例：``heading`` 点名期望命中的小节（纯文本定长块用 ``""``）。

    ``lexical=True`` 表示这题应当被本机词法答上来；``False`` 是故意留下的语义题。
    """

    name: str
    query: str
    heading: str
    lexical: bool = True


DOC_CORPUS = (
    (
        "weekly-report.md",
        "# 周报流程\n"
        "## 提交时间\n"
        "每周五 17:00 之前把周报提交到共享目录，过期不补。\n"
        "## 模板要求\n"
        "周报模板固定三节：本周进展、风险与阻塞、下周计划。\n"
        "## 归档位置\n"
        "周报归档在共享目录的 weekly 子目录，保留一年。\n",
    ),
    (
        "deploy.md",
        "# 部署手册\n"
        "## 前置检查\n"
        "部署前先确认数据库备份已经完成，再执行 migrate。\n"
        "## 回滚步骤\n"
        "回滚执行 rollback.sh，并把版本号写回上一个标签。\n"
        "## 值班联系人\n"
        "值班表在运维群置顶，紧急情况联系林舟。\n",
    ),
    (
        "dishes.txt",
        "酸汤肥牛：先炒香泡椒，加水煮肥牛，起锅前放豆芽和胡椒粉。\n"
        "蒜蓉虾：开背铺蒜蓉，蒸六分钟，淋热油和蒸鱼豉油。\n",
    ),
)


DOC_CASES = (
    DocCase("weekly-report.md", "周报 提交时间", "周报流程 > 提交时间"),
    DocCase("weekly-report.md", "模板 三节 下周计划", "周报流程 > 模板要求"),
    DocCase("weekly-report.md", "归档 weekly 子目录 保留一年", "周报流程 > 归档位置"),
    DocCase("weekly-report.md", "有哪些小节", "本文档目录"),
    DocCase("deploy.md", "回滚 版本号 标签", "部署手册 > 回滚步骤"),
    DocCase("deploy.md", "数据库 备份 migrate", "部署手册 > 前置检查"),
    DocCase("deploy.md", "值班 运维群 林舟", "部署手册 > 值班联系人"),
    DocCase("dishes.txt", "酸汤肥牛 泡椒 豆芽", ""),
    # 语义题：词法按设计答不上来，留着是为了让 R4 的边界可重复验证。
    DocCase("weekly-report.md", "这周干的活怎么汇报", "周报流程 > 模板要求", lexical=False),
    DocCase("deploy.md", "线上出问题了怎么退回上一版", "部署手册 > 回滚步骤", lexical=False),
    DocCase("dishes.txt", "想吃酸的，牛肉怎么做", "", lexical=False),
)


def evaluate_docs(cases=DOC_CASES, limit: int = 5) -> dict:
    """文档链评测：入库→检索→按小节标题点名，指标只算词法题。"""
    limit = max(1, int(limit))
    results = []
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        store = CompanionStore(data_dir / "companion.db")
        service = DocumentService(store, data_dir)
        for name, body in DOC_CORPUS:
            service.ingest(name, body.encode("utf-8"), persona_id="")
        for case in cases:
            hits = service.search(case.query, limit=limit, persona_id=None)
            rank = 0
            for index, hit in enumerate(hits, start=1):
                if (
                    str(hit["name"]) == case.name
                    and str(hit["heading"]) == case.heading
                ):
                    rank = index
                    break
            results.append(
                {
                    "query": case.query,
                    "expected": "{}｜{}".format(case.name, case.heading or "正文"),
                    "lexical": bool(case.lexical),
                    "rank": rank,
                    "recalled": [
                        "{}｜{}".format(hit["name"], hit["heading"] or "正文")
                        for hit in hits
                    ],
                }
            )

    lexical = [item for item in results if item["lexical"]]
    semantic = [item for item in results if not item["lexical"]]
    total = len(lexical) or 1
    hit1 = sum(1 for item in lexical if item["rank"] == 1) / float(total)
    hit5 = sum(1 for item in lexical if 0 < item["rank"] <= limit) / float(total)
    mrr = sum(
        1.0 / item["rank"] for item in lexical if item["rank"] > 0
    ) / float(total)
    semantic_hits = sum(1 for item in semantic if item["rank"] > 0)
    return {
        "cases": len(results),
        "limit": limit,
        "lexical_cases": len(lexical),
        "semantic_cases": len(semantic),
        "hit@1": round(hit1, 4),
        "hit@5": round(hit5, 4),
        "mrr": round(mrr, 4),
        "semantic_hits": semantic_hits,
        "results": results,
    }


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


def format_docs_report(report: dict) -> str:
    lines = [
        "docs: cases={cases}（词法 {lexical_cases}／语义 {semantic_cases}）"
        "limit={limit} hit@1={hit@1:.4f} hit@5={hit@5:.4f} "
        "mrr={mrr:.4f}".format(**report)
    ]
    for item in report["results"]:
        if item["lexical"] and item["rank"] == 1:
            continue
        label = "未命中首位" if item["lexical"] else "语义题"
        lines.append(
            "{}：query={} expected={} rank={}".format(
                label,
                item["query"],
                item["expected"],
                item["rank"] or "miss",
            )
        )
    lines.append(
        "语义题命中 {}/{}：这些题不计进上面的 hit@1/hit@5。命中只说明词法碰巧撞上了"
        "字面（比如「退回上一版」撞上「版本号写回上一个标签」），过不了的那部分要补"
        "得上语义检索。".format(
            report["semantic_hits"], report["semantic_cases"]
        )
    )
    return "\n".join(lines)


def main() -> int:
    print(format_report(evaluate()))
    print(format_docs_report(evaluate_docs()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
