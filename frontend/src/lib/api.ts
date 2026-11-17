// API配置 - 使用相对路径，通过 Vite 代理转发到后端
// 开发模式走 Vite proxy (/api → http://127.0.0.1:12409)
// 生产模式由 Nginx 同源反代到 backend
export const API_BASE_URL = "";

// 模型配置接口
export interface ModelConfig {
  model_id: string;
  display_name: string;
  icon: string;
  icon_color: string;
  is_default?: boolean;
}

/** 接口不可用时的兜底列表；顺序不作为默认模型依据，默认看 is_default。 */
export const FALLBACK_MODELS: ModelConfig[] = [
  { model_id: "qwen-plus-latest", display_name: "Qwen-Plus", icon: "Zap", icon_color: "orange-400", is_default: true },
  { model_id: "qwen3.8-flash", display_name: "Qwen3.8-Flash", icon: "Zap", icon_color: "yellow-400" },
  { model_id: "deepseek-v4.1-flash", display_name: "DeepSeek V4.1 Flash", icon: "Zap", icon_color: "blue-400" },
];

export const FALLBACK_DEFAULT_MODEL_ID =
  FALLBACK_MODELS.find((model) => model.is_default)?.model_id ?? FALLBACK_MODELS[0].model_id;

/** 从接口返回的模型列表里解析默认模型，避免依赖数组第一项。 */
export function getDefaultModelId(models: ModelConfig[]): string {
  return models.find((model) => model.is_default)?.model_id
    ?? models[0]?.model_id
    ?? FALLBACK_DEFAULT_MODEL_ID;
}

// 获取可用模型列表
export async function fetchAvailableModels(): Promise<ModelConfig[]> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/models`, {
      credentials: "include",
    });
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const data = await response.json();
    return data.models || [];
  } catch (error) {
    console.error("获取模型列表失败:", error);
    // 返回与后端 /api/models 一致的默认模型列表作为降级方案
    return FALLBACK_MODELS;
  }
}

// ── 需求澄清 ───────────────────────────────────────────────────────

export interface ClarifyOption {
  label: string;
  description: string;
  recommended?: boolean;
}

export interface ClarifyAnswer {
  question: string;
  answer: string;
}

export interface ClarifyResponse {
  done: boolean;
  question?: string;
  reason?: string;
  options?: ClarifyOption[];
  allow_other?: boolean;
  progress?: number;
  round?: number;
}

export async function fetchClarifyQuestion(
  topic: string,
  answers: ClarifyAnswer[]
): Promise<ClarifyResponse> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/clarify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ topic, answers }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error("需求澄清失败:", error);
    // 澄清不可用时直接进入研究，不能把用户卡在这一步
    return { done: true, reason: "澄清服务暂不可用，按原问题开始研究", progress: 100 };
  }
}

// ── 历史记录 ───────────────────────────────────────────────────────

export interface HistoryItem {
  id: number;
  thread_id: string;
  title: string;
  question: string;
  status: "running" | "completed" | "paused" | "failed" | string;
  has_answer: boolean;
  stage: string | null;
  stage_label: string | null;
  step_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface HistoryStep {
  seq: number;
  node: string;
  label: string;
  content: string;
  created_at: string | null;
}

export interface HistoryDetail extends HistoryItem {
  plan: string;
  answer: string;
  steps: HistoryStep[];
}

export async function fetchHistory(): Promise<HistoryItem[]> {
  const response = await fetch(`${API_BASE_URL}/api/history`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`历史记录加载失败 (${response.status})`);
  }
  const data = await response.json();
  return data.items || [];
}

export async function fetchHistoryDetail(threadId: string): Promise<HistoryDetail> {
  const response = await fetch(
    `${API_BASE_URL}/api/history/${encodeURIComponent(threadId)}`,
    { credentials: "include" }
  );
  if (!response.ok) {
    throw new Error(
      response.status === 404 ? "这条历史记录不存在了" : `历史记录加载失败 (${response.status})`
    );
  }
  return response.json();
}

export async function deleteHistory(threadId: string): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/history/${encodeURIComponent(threadId)}`,
    { method: "DELETE", credentials: "include" }
  );
  if (!response.ok) {
    let message = `删除失败 (${response.status})`;
    try {
      const data = await response.json();
      if (data?.error) message = data.error;
    } catch {
      // 保留状态码兜底文案
    }
    throw new Error(message);
  }
}
