"""Command-line entry point for the minimal agent framework."""

import argparse
import sys

from agent_framework import AgentConfig, ChatAgent
from agent_framework.llm import create_llm_client


def build_agent(offline=False, model=None):
    config = AgentConfig.from_env()
    if model:
        config.model = model
    if offline:
        config.offline = True
    client = create_llm_client(config, offline=config.offline)
    return ChatAgent(
        llm_client=client,
        config=config,
    )


def run_interactive(agent):
    print("Agent 已启动。输入 :quit 退出，:clear 清空记忆，:tools 查看工具。")
    while True:
        try:
            user_text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_text:
            continue
        if user_text in (":quit", ":exit", "exit", "quit"):
            break
        if user_text == ":clear":
            agent.clear()
            print("已清空会话记忆。")
            continue
        if user_text == ":tools":
            print("可用工具：" + ", ".join(agent.tool_names))
            continue
        if user_text == ":history":
            for message in agent.history():
                if message.role == "system":
                    continue
                print("{}: {}".format(message.role, message.content))
            continue

        answer = agent.ask(user_text)
        print("Agent> {}".format(answer))


def parse_args(argv):
    parser = argparse.ArgumentParser(description="最小可交互 Agent 框架")
    parser.add_argument("prompt", nargs="?", help="一次性提问，不进入交互模式")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="强制使用离线演示客户端",
    )
    parser.add_argument("--model", help="覆盖配置中的模型名")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    agent = build_agent(offline=args.offline, model=args.model)
    if args.prompt:
        print(agent.ask(args.prompt))
        return 0
    run_interactive(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
