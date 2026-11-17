// 基于 SSE 的研究流 hook
// 与 @langchain/langgraph-sdk 的 useStream 并存，用于异步任务场景

import { useEffect, useRef, useState, useCallback } from "react";
import { fetchHistoryDetail } from "@/lib/api";
import { parseBackendDate } from "@/lib/datetime";

const API_BASE_URL = "";

interface ResearchEvent {
  generate_plan?: { plan: string };
  generate_query?: { search_query: string[] };
  web_research?: { sources_gathered: any[] };
  reflection?: any;
  outline?: any;
  draft?: any;
  critic_review?: any;
  cite_and_polish?: any;
  quick_answer?: any;
  confirm_plan?: any;
  finalize_answer?: any;
  task_paused?: boolean;
  token?: { text: string; node: string };
  error?: string;
}

export interface ResearchStreamStatus {
  phase: string;
  detail: string;
  startedAt: number | null;
  lastEventAt: number | null;
  eventCount: number;
}

interface UseResearchStreamReturn {
  events: ResearchEvent[];
  messages: any[];
  isLoading: boolean;
  status: ResearchStreamStatus;
  /** 当前正在流式输出的节点名称（null 表示非流式状态） */
  streamingNode: string | null;
  /** 当前节点已流式输出的累计文本 */
  streamingContent: string;
  submit: (input: string, effort: string, model: string, thinking?: string, extra?: { plan?: string; planStatus?: string; title?: string }) => Promise<void>;
  /** 把历史记录载入会话，后续 submit 会复用同一个 thread_id */
  loadHistory: (payload: { threadId: string; messages: any[]; plan?: string; pendingPlan?: boolean }) => void;
  /** 回到仍在后台运行的任务：轮询进度，完成后落到会话里 */
  watchTask: (payload: { threadId: string; messages: any[]; plan?: string }) => void;
  /** 取当前会话缓存的计划（历史续跑时用于传递 plan） */
  getPlan: () => string;
  stop: () => void;
}

const EMPTY_STATUS: ResearchStreamStatus = {
  phase: "空闲",
  detail: "",
  startedAt: null,
  lastEventAt: null,
  eventCount: 0,
};

function tokenPhase(node: string | null) {
  switch (node) {
    case "generate_plan":
      return "正在生成研究计划";
    case "outline":
      return "正在生成报告提纲";
    case "draft":
      return "正在撰写报告";
    case "critic_review":
      return "正在审稿评分";
    case "cite_and_polish":
      return "正在引用校对与润色";
    case "quick_answer":
      return "正在整理回答";
    default:
      return "正在生成内容";
  }
}

function describeEvent(event: any): { phase: string; detail: string } {
  if (event.error) return { phase: "执行出错", detail: String(event.error) };
  if (event.task_paused) return { phase: "等待确认计划", detail: "计划已生成，等待用户确认" };
  if (event.finalize_answer) return { phase: "报告完成", detail: "最终报告已生成" };
  if (event.generate_plan) return { phase: "计划已生成", detail: "等待确认研究方向" };
  if (event.confirm_plan) return { phase: "确认计划", detail: "开始进入检索阶段" };
  if (event.generate_query) return { phase: "检索准备", detail: "检索词已生成，等待网络检索" };
  if (event.web_research) {
    const count = event.web_research?.sources_gathered?.length ?? 0;
    return { phase: "信息评估", detail: `已获得 ${count} 条来源，等待评估是否充分` };
  }
  if (event.reflection) return { phase: "继续检索/准备写作", detail: "信息充分性评估已完成" };
  if (event.outline) return { phase: "撰写初稿", detail: "提纲已生成，等待初稿" };
  if (event.draft) {
    const revision = Number(event.draft?.revision_count ?? 0);
    return {
      phase: revision > 0 ? `审稿中（第 ${revision} 次修订后）` : "审稿中",
      detail: revision > 0 ? "修订稿已生成，等待审稿结果" : "初稿已生成，等待审稿结果",
    };
  }
  if (event.critic_review) {
    const rawScore = event.critic_review?.critic_score;
    const feedback = String(event.critic_review?.critic_feedback || "");
    const parsed = feedback.match(/审稿评分[：:]\s*([\d.]+)/);
    const score = rawScore ?? (parsed ? Number(parsed[1]) : null);
    return {
      phase: "修订/润色",
      detail: score != null ? `审稿评分 ${score}/10，等待下一步处理` : "审稿意见已返回，等待下一步处理",
    };
  }
  if (event.cite_and_polish) return { phase: "报告定稿", detail: "引用校对与润色已完成，准备输出" };
  if (event.quick_answer) return { phase: "正在整理回答", detail: "检索已完成，生成简短回答" };
  return { phase: "处理中", detail: "收到新的研究事件" };
}

export function useResearchStream(): UseResearchStreamReturn {
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [messages, setMessages] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [status, setStatus] = useState<ResearchStreamStatus>(EMPTY_STATUS);
  const [streamingNode, setStreamingNode] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState("");
  const eventSourceRef = useRef<EventSource | null>(null);
  // SSE 断线后由浏览器自动重连；记录连续失败次数，避免服务不可用时无限重试。
  const reconnectAttemptsRef = useRef(0);
  const messageIdCounter = useRef(0);
  const planRef = useRef("");  // 缓存 generate_plan 中的计划内容
  const taskIdRef = useRef<string>("");  // 保存首次任务响应的 task_id，后续提交复用
  // 与 messages state 同步的 ref：submit 里要读最新会话，不能依赖闭包里的旧 messages
  const messagesRef = useRef<any[]>([]);
  // 防重入：连续点击/回车时不重复提交
  const loadingRef = useRef(false);
  // 回到运行中任务时的进度轮询
  const watchTimerRef = useRef<number | null>(null);
  const watchingRef = useRef(false);
  const watchPollingRef = useRef(false);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      if (watchTimerRef.current !== null) {
        window.clearInterval(watchTimerRef.current);
        watchTimerRef.current = null;
      }
    };
  }, []);

  // 流式状态 refs（避免闭包过期问题）
  const streamingNodeRef = useRef<string | null>(null);
  const streamingContentRef = useRef("");
  const streamingFlushTimerRef = useRef<number | null>(null);

  /** 重置流式状态 */
  const _resetStreaming = useCallback(() => {
    streamingNodeRef.current = null;
    streamingContentRef.current = "";
    if (streamingFlushTimerRef.current !== null) {
      window.clearTimeout(streamingFlushTimerRef.current);
      streamingFlushTimerRef.current = null;
    }
    setStreamingNode(null);
    setStreamingContent("");
  }, []);

  const submit = useCallback(
    async (input: string, effort: string, model: string, thinking: string = "off", extra?: { plan?: string; planStatus?: string; title?: string }) => {
      if (!input.trim() || loadingRef.current) return;

      // 停止之前的连接
      stop();
      loadingRef.current = true;

      setIsLoading(true);
      setEvents([]);
      const submittedAt = Date.now();
      setStatus({
        phase: "提交任务",
        detail: "正在建立研究任务与事件连接",
        startedAt: submittedAt,
        lastEventAt: submittedAt,
        eventCount: 0,
      });
      planRef.current = extra?.plan || "";
      _resetStreaming();

      // 首次提交（还没有任何会话消息）时清空 taskIdRef，让后端生成新 task_id；
      // 历史续跑/计划确认时保留已有 task_id，复用 LangGraph checkpoint
      const baseMessages = messagesRef.current;
      const isFirstSubmit = baseMessages.length === 0;
      if (isFirstSubmit) {
        taskIdRef.current = "";
      }

      const humanMsg = {
        type: "human",
        content: input,
        id: String(++messageIdCounter.current),
      };
      const nextMessages = [...baseMessages, humanMsg];
      messagesRef.current = nextMessages;
      setMessages(nextMessages);

      let initial_search_query_count: number;
      let max_research_loops: number;
      switch (effort) {
        case "search":
          initial_search_query_count = 2;
          max_research_loops = 1;
          break;
        case "low":
          initial_search_query_count = 1;
          max_research_loops = 1;
          break;
        case "medium":
        default:
          initial_search_query_count = 3;
          max_research_loops = 3;
          break;
        case "high":
          initial_search_query_count = 5;
          max_research_loops = 10;
          break;
      }

      try {
        // 构建请求体（包含完整对话历史和 plan 状态）
        const body: any = {
          messages: nextMessages,
          initial_search_query_count,
          max_research_loops,
          reasoning_model: model,
          thinking_level: thinking || "off",
          research_mode: effort === "search" ? "search" : "report",
        };
        if (extra?.plan && effort !== "search") {
          body.plan = extra.plan;
          body.plan_status = extra.planStatus || "confirmed";
        }
        if (extra?.title) {
          body.title = extra.title;
        }
        // 后续提交回传同一个 task_id，使后端复用 LangGraph checkpoint
        if (taskIdRef.current) {
          body.task_id = taskIdRef.current;
        }

        // 1. 提交任务，立即拿到 task_id
        const res = await fetch(`${API_BASE_URL}/api/research`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.error || `HTTP ${res.status}`);
        }

        const { stream_url, task_id } = await res.json();

        // 保存 task_id 供后续提交复用
        if (task_id) {
          taskIdRef.current = task_id;
        }

        // 2. 建立 SSE 连接接收事件
        const es = new EventSource(`${API_BASE_URL}${stream_url}`);
        eventSourceRef.current = es;
        reconnectAttemptsRef.current = 0;

        es.onopen = () => {
          reconnectAttemptsRef.current = 0;
          setStatus(prev =>
            prev.phase === "连接中断"
              ? { ...prev, phase: "已重新连接", detail: "事件流已恢复" }
              : prev
          );
        };

        es.onmessage = (e) => {
          try {
            const raw = JSON.parse(e.data);

            // ── 处理 token 流式事件 ──────────────────────────
            if (raw.token) {
              const { text, node } = raw.token as { text: string; node: string };
              if (node !== streamingNodeRef.current) {
                // 新节点开始流式
                streamingNodeRef.current = node;
                streamingContentRef.current = text;
                if (streamingFlushTimerRef.current !== null) {
                  window.clearTimeout(streamingFlushTimerRef.current);
                  streamingFlushTimerRef.current = null;
                }
                setStreamingNode(node);
                setStreamingContent(text);
              } else {
                // 同一节点继续追加；按 80ms 合并刷新，避免每个 token 触发整段
                // Markdown 重渲染，长报告时尤其明显。
                streamingContentRef.current += text;
                if (streamingFlushTimerRef.current === null) {
                  streamingFlushTimerRef.current = window.setTimeout(() => {
                    streamingFlushTimerRef.current = null;
                    setStreamingContent(streamingContentRef.current);
                  }, 80);
                }
              }
              setStatus(prev => ({
                ...prev,
                phase: tokenPhase(node),
                detail: "正在接收模型输出",
                lastEventAt: Date.now(),
              }));
              return; // token 事件不需要进一步处理
            }

            const event: ResearchEvent = raw;
            const eventStatus = describeEvent(raw);
            setStatus(prev => ({
              ...prev,
              phase: eventStatus.phase,
              detail: eventStatus.detail,
              lastEventAt: Date.now(),
              eventCount: prev.eventCount + 1,
            }));

            if (event.error) {
              setIsLoading(false);
              loadingRef.current = false;
              setEvents(prev => [...prev, event]);
              _resetStreaming();
              es.close();
              return;
            }

            setEvents(prev => [...prev, event]);

            // 节点完成事件到达 → 清空对应节点的流式 buffer
            if (event.generate_plan) {
              planRef.current = event.generate_plan.plan || "";
              if (streamingNodeRef.current === "generate_plan") {
                _resetStreaming();
              }
            }

            // finalize_answer 时结束
            if (event.finalize_answer) {
              setIsLoading(false);
              loadingRef.current = false;
              // 使用后端 Post.extract_pattern 清洗后的内容（不含 markdown fence）
              const finalContent = (event.finalize_answer as any)?.messages?.[0]?.content || "";
              const aiMessage = {
                type: "ai",
                content: finalContent,
                id: String(++messageIdCounter.current),
              };
              messagesRef.current = [...messagesRef.current, aiMessage];
              setMessages(messagesRef.current);
              _resetStreaming();
              es.close();
            }

            // task_paused 时结束（等待 Plan 确认，后续继续用本 hook 提交确认）
            if (event.task_paused) {
              setIsLoading(false);
              loadingRef.current = false;
              _resetStreaming();
              // 从缓存的 generate_plan 事件中获取计划内容
              const planContent = planRef.current;
              const aiMessage = {
                type: "ai",
                content: planContent,
                id: String(++messageIdCounter.current),
              };
              messagesRef.current = [...messagesRef.current, aiMessage];
              setMessages(messagesRef.current);
              es.close();
            }
          } catch {
            // 忽略解析错误
          }
        };

        es.onerror = () => {
          // EventSource 在非 CLOSED 状态下会按浏览器策略自动重连，并带上
          // Last-Event-ID 请求服务端回放。不要在这里 close()，否则会丢掉重连能力。
          reconnectAttemptsRef.current += 1;
          if (es.readyState !== 2 && reconnectAttemptsRef.current <= 10) {
            setStatus(prev => ({
              ...prev,
              phase: "连接中断",
              detail: `正在重新连接（第 ${reconnectAttemptsRef.current} 次）`,
              lastEventAt: Date.now(),
            }));
            return;
          }

          setIsLoading(false);
          loadingRef.current = false;
          _resetStreaming();
          setStatus(prev => ({
            ...prev,
            phase: "连接中断",
            detail: "事件连接已断开，可在历史记录中继续查看",
            lastEventAt: Date.now(),
          }));
          es.close();
          setEvents(prev => [
            ...prev,
            { error: "事件连接已断开，可在历史记录中继续查看" } as ResearchEvent,
          ]);
        };
      } catch (err: any) {
        setIsLoading(false);
        loadingRef.current = false;
        _resetStreaming();
        setStatus(prev => ({
          ...prev,
          phase: "提交失败",
          detail: err.message || "提交失败",
          lastEventAt: Date.now(),
        }));
        setEvents(prev => [
          ...prev,
          { error: err.message || "提交失败" } as ResearchEvent,
        ]);
      }
    },
    [_resetStreaming]
  );

  const stop = useCallback(() => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (watchTimerRef.current !== null) {
      window.clearInterval(watchTimerRef.current);
      watchTimerRef.current = null;
    }
    watchingRef.current = false;
    watchPollingRef.current = false;
    loadingRef.current = false;
    setIsLoading(false);
    setStatus(prev => ({
      ...prev,
      phase: "已停止",
      detail: "当前页面已停止接收事件，后台任务可能仍在继续",
      lastEventAt: Date.now(),
    }));
    _resetStreaming();
  }, [_resetStreaming]);

  /**
   * 把历史记录载入会话。
   * - 保留 thread_id，后续 submit 会复用同一个 LangGraph checkpoint；
   * - 同步刷新 messagesRef，保证下一次 submit 带上完整历史上下文。
   */
  const loadHistory = useCallback(
    (payload: { threadId: string; messages: any[]; plan?: string; pendingPlan?: boolean }) => {
      stop();
      taskIdRef.current = payload.threadId;
      planRef.current = payload.plan || "";
      messagesRef.current = payload.messages;
      setMessages(payload.messages);
      setEvents([]);
      setIsLoading(false);
      setStatus(
        payload.pendingPlan
          ? {
              phase: "等待确认计划",
              detail: "可以在输入框补充需求，或点击“确认计划”开始研究",
              startedAt: null,
              lastEventAt: null,
              eventCount: 0,
            }
          : {
              phase: "已载入历史",
              detail: "可继续追问，或从历史详情查看已完成的步骤",
              startedAt: null,
              lastEventAt: null,
              eventCount: 0,
            }
      );
      _resetStreaming();
      messageIdCounter.current = payload.messages.length + 1;
    },
    [stop, _resetStreaming]
  );

  const getPlan = useCallback(() => planRef.current, []);

  /**
   * 回到仍在后台运行的任务：
   * - 立即把已有消息放回会话，输入框锁住；
   * - 每 3 秒读一次历史详情，更新阶段与步数；
   * - 任务暂停/完成/失败后停止轮询，把计划或最终报告落到会话里。
   */
  const watchTask = useCallback(
    (payload: { threadId: string; messages: any[]; plan?: string }) => {
      stop();
      taskIdRef.current = payload.threadId;
      planRef.current = payload.plan || "";
      messagesRef.current = payload.messages;
      setMessages(payload.messages);
      setEvents([]);
      setStreamingNode(null);
      setStreamingContent("");
      messageIdCounter.current = payload.messages.length + 1;

      watchingRef.current = true;
      loadingRef.current = true;
      setIsLoading(true);
      const startedAt = Date.now();
      setStatus({
        phase: "正在读取任务进度",
        detail: "任务仍在后台运行",
        startedAt,
        lastEventAt: startedAt,
        eventCount: 0,
      });

      const finishWatch = () => {
        watchingRef.current = false;
        if (watchTimerRef.current !== null) {
          window.clearInterval(watchTimerRef.current);
          watchTimerRef.current = null;
        }
        loadingRef.current = false;
        setIsLoading(false);
      };

      let failures = 0;

      const tick = async () => {
        if (!watchingRef.current || watchPollingRef.current) return;
        watchPollingRef.current = true;
        try {
          const detail = await fetchHistoryDetail(payload.threadId);
          if (!watchingRef.current) return;
          failures = 0;

          if (detail.status === "running") {
            setStatus((prev) => ({
              ...prev,
              phase: (detail.stage_label || "任务进行中").replace(/调研/g, "研究"),
              detail:
                detail.step_count > 0
                  ? `已完成 ${detail.step_count} 步，后台仍在继续`
                  : "后台任务正在执行",
              lastEventAt:
                parseBackendDate(detail.updated_at)?.getTime() ?? prev.lastEventAt,
              eventCount: detail.step_count,
            }));
            return;
          }

          finishWatch();

          if (detail.status === "paused") {
            const plan = detail.plan || payload.plan || "";
            let next = messagesRef.current;
            if (plan && next[next.length - 1]?.content !== plan) {
              next = [
                ...next,
                {
                  type: "ai",
                  content: plan,
                  id: String(++messageIdCounter.current),
                },
              ];
            }
            messagesRef.current = next;
            setMessages(next);
            planRef.current = plan;
            setStatus({
              phase: "等待确认计划",
              detail: "可以在输入框补充需求，或点击“确认计划”开始研究",
              startedAt: null,
              lastEventAt: null,
              eventCount: detail.step_count,
            });
            return;
          }

          if (detail.status === "completed") {
            const answer = detail.answer || "";
            let next = messagesRef.current;
            if (answer && next[next.length - 1]?.content !== answer) {
              next = [
                ...next,
                {
                  type: "ai",
                  content: answer,
                  id: String(++messageIdCounter.current),
                },
              ];
            }
            messagesRef.current = next;
            setMessages(next);
            setStatus({
              phase: "报告已完成",
              detail: "最终报告已生成",
              startedAt: null,
              lastEventAt: null,
              eventCount: detail.step_count,
            });
            return;
          }

          setStatus({
            phase: "任务已中断",
            detail: detail.stage_label || "后台任务未正常完成，可以继续追问",
            startedAt: null,
            lastEventAt: null,
            eventCount: detail.step_count,
          });
        } catch (err) {
          failures += 1;
          if (failures >= 3) {
            finishWatch();
            setStatus((prev) => ({
              ...prev,
              phase: "进度读取已停止",
              detail: "连续多次读取失败，任务可能已被删除；可返回历史列表重新查看",
            }));
            return;
          }
          setStatus((prev) => ({
            ...prev,
            phase: "读取进度失败",
            detail: err instanceof Error ? err.message : "无法读取任务进度",
            lastEventAt: prev.lastEventAt,
          }));
        } finally {
          watchPollingRef.current = false;
        }
      };

      void tick();
      watchTimerRef.current = window.setInterval(tick, 3000);
    },
    [stop]
  );

  return {
    events,
    messages,
    isLoading,
    status,
    streamingNode,
    streamingContent,
    submit,
    loadHistory,
    watchTask,
    getPlan,
    stop,
  };
}
