"""
agent 包 — 底层 Agentic Loop 组件（run_agent_loop、ToolExecutor）。

不再对外暴露 ask()/AskResult：单 Agent 直接对话入口已删除，
CLI 和所有 Web 接口统一走 coordinator/agent.py 的 CoordinatorAgent。
"""
