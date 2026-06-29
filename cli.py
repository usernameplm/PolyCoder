"""
cli.py — 命令行交互入口

功能：
- 循环读取用户输入
- 调用 ask() 并打印回答
- /usage 命令查看累计 Token 用量
- Ctrl+C 退出
"""

import asyncio
from agent import ask


async def main():
    print("=" * 50)
    print("  Agent 已就绪（直接调用 Anthropic API）")
    print("  输入问题后按回车，输入 /usage 查看 Token 用量")
    print("  按 Ctrl+C 退出")
    print("=" * 50)

    total_input = 0   # 累计输入 Token
    total_output = 0  # 累计输出 Token

    while True:
        try:
            question = input("\n你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            break

        if not question:
            continue

        # 内置命令
        if question == "/usage":
            print(f"  累计 Token：输入 {total_input}，输出 {total_output}")
            print(f"  大约费用（Sonnet）：${(total_input * 3 + total_output * 15) / 1_000_000:.4f} USD")
            continue

        if question.lower() in ("quit", "q", "exit", "退出"):
            print("再见！")
            break

        # 调用 Agent
        try:
            print("Agent：", end="", flush=True)
            result = await ask(question)
            print(result.text)

            # 累计 Token 用量
            total_input  += result.input_tokens
            total_output += result.output_tokens
            print(f"  （本次：输入 {result.input_tokens} / 输出 {result.output_tokens} tokens）")

        except Exception as e:
            print(f"\n[错误] {e}")
            print("提示：检查 .env 里的 ANTHROPIC_API_KEY 是否正确，以及网络是否正常。")


if __name__ == "__main__":
    asyncio.run(main())