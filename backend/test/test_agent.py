from agent.base_agent import *
import os

os.environ['APP_TOKEN'] = 'REDACTED-DASHSCOPE-KEY'
os.environ['LLM_BASE_URL'] = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
os.environ['APP_ID'] = 'REDACTED-MCP-APP-ID'
os.environ['GEMINI_API_KEY'] = 'REDACTED-GOOGLE-KEY'

if __name__ == '__main__':
    agent = Agent()
    response = agent("中国的首都是那里？")