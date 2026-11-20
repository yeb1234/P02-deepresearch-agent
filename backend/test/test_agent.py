from agent.base_agent import *
import os

# 手动跑这个脚本前，先在环境里注入凭据，不要把 Key 写回文件：
#   $env:APP_TOKEN = "<dashscope key>"; $env:APP_ID = "<mcp app id>"
#   $env:GEMINI_API_KEY = "<google key>"   # 可选，只有走 Gemini 时才需要
os.environ.setdefault(
    "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

if __name__ == "__main__":
    agent = Agent()
    response = agent("中国的首都是那里？")
