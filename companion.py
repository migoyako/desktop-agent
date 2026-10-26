"""Launch the first desktop companion version."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

from agent_framework.companion.models import AgentMode
from agent_framework.companion.runtime import CompanionRuntime


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="数字分身桌面原型")
    parser.add_argument(
        "--ui",
        choices=("auto", "qt", "tk", "cli"),
        default="auto",
        help="选择界面后端",
    )
    parser.add_argument("--data-dir", help="数据目录，默认是项目下的 data")
    parser.add_argument("--session-id", default="main")
    parser.add_argument("--workspace", help="预先授权的工作目录")
    parser.add_argument("--offline", action="store_true", help="强制使用离线模型")
    return parser.parse_args(argv)


def print_memories(runtime):
    rows = runtime.memories()
    if not rows:
        print("暂无记忆。")
        return
    for memory in rows:
        print(
            "{} | {} | {} | {}".format(
                memory.memory_id,
                memory.kind,
                memory.status,
                memory.content,
            )
        )


def print_affect(runtime):
    state = runtime.affect_snapshot()
    print("用户状态：{}".format(state["user"]))
    print("角色状态：{}".format(state["agent"]))


def run_cli(runtime):
    mode = runtime.current_mode()
    print("数字分身已启动。")
    print("命令：/mode chat|work、/workspace 路径、/memory、/affect、/undo、/quit")
    while True:
        try:
            text = input("[{}] 你> ".format(mode.value)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        if text.startswith("/mode "):
            value = text.split(None, 1)[1].strip().lower()
            if value not in ("chat", "work"):
                print("模式只能是 chat 或 work。")
                continue
            mode = AgentMode(value)
            runtime.set_mode(mode)
            print("已切换到{}模式。".format("陪伴" if value == "chat" else "工作"))
            continue
        if text.startswith("/workspace "):
            path = text.split(None, 1)[1].strip()
            try:
                selected = runtime.select_workspace(path)
                print("已授权：{}".format(selected))
            except Exception as error:
                print("授权失败：{}".format(error))
            continue
        if text == "/memory":
            print_memories(runtime)
            continue
        if text == "/affect":
            print_affect(runtime)
            continue
        if text == "/undo":
            receipts = [
                item
                for item in runtime.latest_receipts(10)
                if item.undoable and item.undo_status == "available"
            ]
            if not receipts:
                print("没有可撤销的写入。")
                continue
            try:
                print(runtime.undo_receipt(receipts[0].receipt_id))
            except Exception as error:
                print("撤销失败：{}".format(error))
            continue
        result = runtime.handle_message(text, mode)
        print("分身> " + result["answer"])
        proposal = result.get("proposal")
        if proposal:
            print()
            print(proposal.user_visible_plan)
            answer = input("确认写入？[y/N] ").strip().lower()
            if answer in ("y", "yes", "是"):
                executed = runtime.confirm_proposal(proposal.action_id, mode)
                print("分身> " + executed["answer"])
            else:
                print(runtime.cancel_proposal(proposal.action_id))
        pending = runtime.pending_proposals()
        if pending and not proposal:
            print("有一条待确认动作，可在图形界面中处理。")
    return 0


def main(argv=None):
    args = parse_args(argv)
    runtime = CompanionRuntime.from_config(
        data_dir=args.data_dir,
        offline=args.offline,
        session_id=args.session_id,
    )
    if args.workspace:
        try:
            runtime.select_workspace(args.workspace)
        except Exception as error:
            print("工作目录授权失败：{}".format(error), file=sys.stderr)
            return 2

    if args.ui == "cli":
        return run_cli(runtime)
    if args.ui in ("auto", "qt") and importlib.util.find_spec("PySide6"):
        try:
            from agent_framework.companion.ui.qt_app import launch_qt

            return launch_qt(runtime)
        except ImportError:
            if args.ui == "qt":
                print("PySide6 未安装。", file=sys.stderr)
                return 2
    if args.ui == "qt":
        print("PySide6 未安装，请运行 python -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    from agent_framework.companion.ui.tk_app import launch_tk

    launch_tk(runtime)
    return 0


if __name__ == "__main__":
    sys.exit(main())
