import { useStream } from "@langchain/langgraph-sdk/react";
import { useResearchStream } from "@/lib/useResearchStream";
import type { Message } from "@langchain/langgraph-sdk";
import { useState, useEffect, useRef, useCallback } from "react";
import { ProcessedEvent } from "@/components/ActivityTimeline";
import { WelcomeScreen } from "@/components/WelcomeScreen";
import { ChatMessagesView } from "@/components/ChatMessagesView";
import { ResearchStreamChatView } from "@/components/ResearchStreamChatView";
import { HistoryDetailView } from "@/components/HistoryDetailView";
import type { HistoryDetail, ClarifyAnswer, ClarifyResponse } from "@/lib/api";
import { FALLBACK_DEFAULT_MODEL_ID, fetchClarifyQuestion } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface ClarifySession {
  topic: string;
  effort: string;
  model: string;
  thinking: string;
  answers: ClarifyAnswer[];
  question: ClarifyResponse | null;
  loading: boolean;
}

/** 研究深度同时决定模型思考档位：快速关闭，标准/深入才开启。 */
function thinkingFromEffort(effort: string): string {
  if (effort === "medium") return "standard";
  if (effort === "high") return "deep";
  return "off";
}

export default function App() {
  const [processedEventsTimeline, setProcessedEventsTimeline] = useState<
    ProcessedEvent[]
  >([]);
  const [historicalActivities, setHistoricalActivities] = useState<
    Record<string, ProcessedEvent[]>
  >({});
  const [awaitingPlanConfirmation, setAwaitingPlanConfirmation] = useState("unconfirmed");
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const hasFinalizeEventOccurredRef = useRef(false);
  const processedResearchEventCountRef = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [savedEffort, setSavedEffort] = useState("low");
  const [savedModel, setSavedModel] = useState(FALLBACK_DEFAULT_MODEL_ID);
  // 发起研究前的逐题澄清；null = 当前没有澄清会话
  const [clarifySession, setClarifySession] = useState<ClarifySession | null>(null);
  const clarifyBusyRef = useRef(false);
  // 当前正在查看的历史记录（null = 不在历史详情页）
  const [historyThreadId, setHistoryThreadId] = useState<string | null>(null);
  const thread = useStream<{
    messages: Message[];
    initial_search_query_count: number;
    max_research_loops: number;
    reasoning_model: string;
    plan_status: string;
  }>({
    apiUrl: "",
    assistantId: "agent",
    messagesKey: "messages",
    onUpdateEvent: (event: any) => {
      let processedEvent: ProcessedEvent | null = null;
      if (event.generate_plan){
        processedEvent = {
          title: "生成计划",
          data: event.generate_plan?.plan || "未返回研究计划"
        }
        setAwaitingPlanConfirmation("unconfirmed");
        hasFinalizeEventOccurredRef.current = true;
      }
      else if (event.generate_query) {
        processedEvent = {
          title: "生成搜索查询",
          data: event.generate_query?.search_query?.join(", ") || "",
        };
      } else if (event.web_research) {
        const sources = event.web_research.sources_gathered || [];
        const numSources = sources.length;
        const uniqueLabels = [
          ...new Set(sources.map((s: any) => s.label).filter(Boolean)),
        ];
        const exampleLabels = uniqueLabels.slice(0, 3).join(", ");
        processedEvent = {
          title: "网络研究",
          data: `已获取 ${numSources} 条来源${
            exampleLabels ? `；主要来源：${exampleLabels}` : ""
          }。`,
        };
      } else if (event.reflection) {
        processedEvent = {
          title: "反思和分析",
          data: "正在评估检索结果的信息完整度。",
        };
      } else if (event.outline) {
        processedEvent = {
          title: "生成报告提纲",
          data: "报告结构已生成",
        };
      } else if (event.draft) {
        const revision = Number((event.draft as any)?.revision_count ?? 0);
        processedEvent = {
          title: revision > 0 ? `修订报告（第 ${revision} 次）` : "撰写初稿",
          data: revision > 0 ? "根据审稿意见改写报告" : "报告初稿已生成",
        };
      } else if (event.critic_review) {
        const feedback = String((event.critic_review as any)?.critic_feedback || "");
        const score = feedback.match(/审稿评分[：:]\s*([\d.]+)/)?.[1];
        processedEvent = {
          title: "审稿评分",
          data: score ? `当前评分 ${score}/10` : "已返回审稿意见",
        };
      } else if (event.cite_and_polish) {
        processedEvent = {
          title: "引用校对与润色",
          data: "最终格式与引用已处理",
        };
      } else if (event.finalize_answer) {
        processedEvent = {
          title: "最终确定答案",
          data: "正在整理并输出最终报告。",
        };
        hasFinalizeEventOccurredRef.current = true;
      }
      if (processedEvent) {
        setProcessedEventsTimeline((prevEvents) => [
          ...prevEvents,
          processedEvent!,
        ]);
      }
    },
    onError: (error: any) => {
      setError(error.message);
    },
  });

  // 异步任务通道（首次提交走此通道，Plan 确认后切回 useStream）
  const researchStream = useResearchStream();

  useEffect(() => {
    if (scrollAreaRef.current) {
      const scrollViewport = scrollAreaRef.current.querySelector(
        "[data-radix-scroll-area-viewport]"
      );
      if (scrollViewport) {
        scrollViewport.scrollTop = scrollViewport.scrollHeight;
      }
    }
  }, [thread.messages]);

  useEffect(() => {
    if (
      hasFinalizeEventOccurredRef.current &&
      !thread.isLoading &&
      thread.messages.length > 0
    ) {
      const lastMessage = thread.messages[thread.messages.length - 1];
      if (lastMessage && lastMessage.type === "ai" && lastMessage.id) {
        setHistoricalActivities((prev) => ({
          ...prev,
          [lastMessage.id!]: [...processedEventsTimeline],
        }));
      }
      hasFinalizeEventOccurredRef.current = false;
    }
  }, [thread.messages, thread.isLoading, processedEventsTimeline]);

  // 将异步通道的事件汇入 ActivityTimeline（与 useStream 的 onUpdateEvent 逻辑一致）
  useEffect(() => {
    const events = researchStream.events;
    if (events.length < processedResearchEventCountRef.current) {
      processedResearchEventCountRef.current = 0;
    }
    const newEvents = events.slice(processedResearchEventCountRef.current);
    processedResearchEventCountRef.current = events.length;

    for (const event of newEvents) {
      let processedEvent: ProcessedEvent | null = null;
      if (event.generate_plan) {
        processedEvent = {
          title: "生成计划",
          data: event.generate_plan.plan || "未返回研究计划",
        };
        setAwaitingPlanConfirmation("unconfirmed");
        hasFinalizeEventOccurredRef.current = true;
      } else if (event.generate_query) {
        processedEvent = {
          title: "生成搜索查询",
          data: (event.generate_query as any)?.search_query?.join(", ") || "",
        };
      } else if (event.web_research) {
        const sources = (event.web_research as any)?.sources_gathered || [];
        const uniqueLabels = [
          ...new Set(sources.map((s: any) => s.label).filter(Boolean)),
        ];
        processedEvent = {
          title: "网络研究",
          data: `已获取 ${sources.length} 条来源${
            uniqueLabels.length > 0
              ? `；主要来源：${uniqueLabels.slice(0, 3).join(", ")}`
              : ""
          }。`,
        };
      } else if (event.reflection) {
        processedEvent = {
          title: "反思和分析",
          data: "正在评估检索结果的信息完整度。",
        };
      } else if (event.outline) {
        processedEvent = {
          title: "生成报告提纲",
          data: "报告结构已生成",
        };
      } else if (event.draft) {
        const revision = Number((event.draft as any)?.revision_count ?? 0);
        processedEvent = {
          title: revision > 0 ? `修订报告（第 ${revision} 次）` : "撰写初稿",
          data: revision > 0 ? "根据审稿意见改写报告" : "报告初稿已生成",
        };
      } else if (event.critic_review) {
        const feedback = String((event.critic_review as any)?.critic_feedback || "");
        const score = feedback.match(/审稿评分[：:]\s*([\d.]+)/)?.[1];
        processedEvent = {
          title: "审稿评分",
          data: score ? `当前评分 ${score}/10` : "已返回审稿意见",
        };
      } else if (event.cite_and_polish) {
        processedEvent = {
          title: "引用校对与润色",
          data: "最终格式与引用已处理",
        };
      } else if (event.finalize_answer) {
        processedEvent = {
          title: "最终确定答案",
          data: "正在整理并输出最终报告。",
        };
        hasFinalizeEventOccurredRef.current = true;
      }
      if (processedEvent) {
        setProcessedEventsTimeline(prev => [...prev, processedEvent]);
      }
    }
  }, [researchStream.events]);

  const finishClarification = useCallback(
    (session: ClarifySession) => {
      clarifyBusyRef.current = false;
      setClarifySession(null);
      const notes = session.answers
        .map(
          (item, index) =>
            `${index + 1}. ${item.question}\n   选择：${item.answer}`
        )
        .join("\n");
      const content = notes
        ? `${session.topic}\n\n【需求澄清补充】\n${notes}\n\n以上是已确认的约束，请按此执行。`
        : session.topic;
      setProcessedEventsTimeline([]);
      hasFinalizeEventOccurredRef.current = false;
      researchStream.submit(
        content,
        session.effort,
        session.model,
        session.thinking,
        { title: session.topic }
      );
    },
    [researchStream]
  );

  const startClarification = useCallback(
    async (topic: string, effort: string, model: string, thinking: string) => {
      const session: ClarifySession = {
        topic,
        effort,
        model,
        thinking,
        answers: [],
        question: null,
        loading: true,
      };
      clarifyBusyRef.current = true;
      setClarifySession(session);

      const first = await fetchClarifyQuestion(topic, []);
      if (!clarifyBusyRef.current) return;
      if (first.done) {
        finishClarification(session);
        return;
      }
      clarifyBusyRef.current = false;
      setClarifySession({ ...session, question: first, loading: false });
    },
    [finishClarification]
  );

  const handleClarifyAnswer = useCallback(
    async (answer: string) => {
      const session = clarifySession;
      if (!session || !session.question || clarifyBusyRef.current) return;
      const nextAnswers: ClarifyAnswer[] = [
        ...session.answers,
        {
          question: session.question.question || "",
          answer,
        },
      ];
      clarifyBusyRef.current = true;
      setClarifySession({ ...session, answers: nextAnswers, loading: true });

      if (nextAnswers.length >= 3) {
        finishClarification({ ...session, answers: nextAnswers });
        return;
      }

      const next = await fetchClarifyQuestion(session.topic, nextAnswers);
      if (!clarifyBusyRef.current) return;
      if (next.done) {
        finishClarification({ ...session, answers: nextAnswers });
        return;
      }
      clarifyBusyRef.current = false;
      setClarifySession({
        ...session,
        answers: nextAnswers,
        question: next,
        loading: false,
      });
    },
    [clarifySession, finishClarification]
  );

  const handleSkipClarification = useCallback(() => {
    if (!clarifySession) return;
    finishClarification(clarifySession);
  }, [clarifySession, finishClarification]);

  const handleSubmit = useCallback(
    (submittedInputValue: string, effort: string, model: string, thinking: string) => {
      if (!submittedInputValue.trim()) return;
      setProcessedEventsTimeline([]);
      hasFinalizeEventOccurredRef.current = false;

      // 保存 effort/model 以备后续使用；思考档位由 effort 推导
      if (effort) setSavedEffort(effort);
      if (model) setSavedModel(model);

      // 场景 1: 首次提交 → 先做逐题需求澄清，再进入 task queue / SSE 通道
      if (thread.messages.length === 0 && researchStream.messages.length === 0) {
        if (effort) setSavedEffort(effort);
        if (model) setSavedModel(model);
        // 仅搜索模式不再走 3 轮澄清，直接检索并回答
        if (effort === "search") {
          void researchStream.submit(
            submittedInputValue,
            effort,
            model,
            thinking || thinkingFromEffort(effort)
          );
          return;
        }
        void startClarification(
          submittedInputValue,
          effort,
          model,
          thinking || thinkingFromEffort(effort)
        );
        return;
      }

      const planContent = researchStream.getPlan();
      // 仅搜索模式的追问不再复用旧计划，直接带着新问题进入检索问答
      if (savedEffort === "search") {
        researchStream.submit(
          submittedInputValue,
          savedEffort,
          savedModel,
          thinkingFromEffort(savedEffort)
        );
        return;
      }
      const lastHuman = [...researchStream.messages]
        .reverse()
        .find((m) => m.type === "human");
      const lastHumanContent =
        typeof lastHuman?.content === "string" ? lastHuman.content.trim() : "";
      const isExplicitConfirm =
        submittedInputValue.includes("确认计划") ||
        submittedInputValue.includes("需求确认") ||
        submittedInputValue.includes("开始研究");
      const isSameAsLastHuman =
        submittedInputValue.trim() === lastHumanContent;

      // 场景 2: 明确点击“确认计划”/输入确认词 → 用 confirmed 跳过重新规划
      if (planContent && isExplicitConfirm) {
        researchStream.submit("确认计划", savedEffort, savedModel, thinkingFromEffort(savedEffort), {
          plan: planContent,
          planStatus: "confirmed",
        });
        return;
      }

      // 场景 3: 已有计划时再次提交
      if (researchStream.messages.length > 0) {
        if (planContent) {
          if (isSameAsLastHuman) {
            // 用户重复提交同一个问题：视为“继续当前计划”，避免后端重新规划造成计划循环
            researchStream.submit("确认计划", savedEffort, savedModel, thinkingFromEffort(savedEffort), {
              plan: planContent,
              planStatus: "confirmed",
            });
          } else {
            // 用户补充了新要求：交给模型修订计划，而不是强行确认旧计划
            researchStream.submit(submittedInputValue, savedEffort, savedModel, thinkingFromEffort(savedEffort), {
              plan: planContent,
              planStatus: "unconfirmed",
            });
          }
          return;
        }

        // 没有待确认计划（例如已完成报告后的追问）：按新问题重新生成计划
        researchStream.submit(submittedInputValue, savedEffort, savedModel, thinkingFromEffort(savedEffort), {
          planStatus: "unconfirmed",
        });
        return;
      }

      // 场景 4: thread 通道的后续对话（已废弃，保留作为 fallback）
      let initial_search_query_count = 0;
      let max_research_loops = 0;
      switch (savedEffort) {
        case "search":
          initial_search_query_count = 2;
          max_research_loops = 1;
          break;
        case "low":
          initial_search_query_count = 1;
          max_research_loops = 1;
          break;
        case "medium":
          initial_search_query_count = 3;
          max_research_loops = 3;
          break;
        case "high":
          initial_search_query_count = 5;
          max_research_loops = 10;
          break;
      }

      const newMessages: Message[] = [
        ...(thread.messages || []),
        {
          type: "human",
          content: submittedInputValue,
          id: Date.now().toString(),
        },
      ];
      thread.submit({
        messages: newMessages,
        initial_search_query_count: initial_search_query_count,
        max_research_loops: max_research_loops,
        reasoning_model: savedModel,
        plan_status: awaitingPlanConfirmation,
        research_mode: savedEffort === "search" ? "search" : "report",
      } as any);
    },
    [thread, researchStream, savedEffort, savedModel, awaitingPlanConfirmation, startClarification]
  );

  const handleCancel = useCallback(() => {
    // 研究流通道只停止当前页面的接收；后台任务继续跑，进度仍可从历史记录恢复。
    if (researchStream.isLoading || researchStream.messages.length > 0) {
      researchStream.stop();
      return;
    }
    thread.stop();
  }, [thread, researchStream]);

  // 从历史详情回到主对话：载入原始问题/计划/报告，并复用同一个 thread_id
  const handleContinueHistory = useCallback(
    (detail: HistoryDetail) => {
      const question = detail.question || detail.title;
      const isRunning = detail.status === "running";
      const contextContent =
        detail.status === "paused" || isRunning
          ? detail.plan
          : detail.answer || detail.plan || "";
      const loadedMessages = [
        { type: "human", content: question, id: "history-human" },
        ...(contextContent
          ? [{ type: "ai", content: contextContent, id: "history-ai" }]
          : []),
      ];

      setHistoryThreadId(null);
      setProcessedEventsTimeline([]);
      setAwaitingPlanConfirmation("unconfirmed");

      if (isRunning) {
        // 进行中的任务：回到会话页轮询进度，不自动确认、不重复提交
        researchStream.watchTask({
          threadId: detail.thread_id,
          messages: loadedMessages,
          plan: detail.plan || "",
        });
        return;
      }

      researchStream.loadHistory({
        threadId: detail.thread_id,
        messages: loadedMessages,
        plan: detail.plan || "",
        pendingPlan: detail.status === "paused",
      });

      // 待确认任务只回到计划确认页，由用户补充需求或点击“确认计划”
      // 这里不再自动提交，避免“一进去就开跑”。
    },
    [researchStream]
  );

  return (
    <div className="flex h-screen bg-neutral-800 text-neutral-100 font-sans antialiased">
      <main className="h-full w-full max-w-4xl mx-auto">
          {historyThreadId ? (
            <HistoryDetailView
              threadId={historyThreadId}
              onBack={() => setHistoryThreadId(null)}
              onContinue={handleContinueHistory}
              onDeleted={() => setHistoryThreadId(null)}
            />
          ) : thread.messages.length === 0 && researchStream.messages.length === 0 ? (
            <WelcomeScreen
              handleSubmit={handleSubmit}
              isLoading={thread.isLoading}
              onCancel={handleCancel}
              onOpenHistory={setHistoryThreadId}
              clarification={clarifySession}
              onClarifyAnswer={handleClarifyAnswer}
              onClarifySkip={handleSkipClarification}
            />
          ) : thread.messages.length > 0 ? (
            error ? (
              <div className="flex flex-col items-center justify-center h-full">
                <div className="flex flex-col items-center justify-center gap-4">
                  <h1 className="text-2xl text-red-400 font-bold">错误：</h1>
                  <p className="text-red-400">{JSON.stringify(error)}</p>
                  <Button
                    variant="destructive"
                    onClick={() => window.location.reload()}
                  >
                    重试，或请联系你的系统管理员
                  </Button>
                </div>
              </div>
            ) : (
              <ChatMessagesView
                messages={thread.messages}
                isLoading={thread.isLoading}
                scrollAreaRef={scrollAreaRef}
                onSubmit={handleSubmit}
                onCancel={handleCancel}
                liveActivityEvents={processedEventsTimeline}
                historicalActivities={historicalActivities}
              />
            )
          ) : (
            // researchStream 通道显示（首次 plan 生成 + plan 确认后的后续流程）
            <ResearchStreamChatView
              messages={researchStream.messages}
              isLoading={researchStream.isLoading}
              status={researchStream.status}
              liveActivityEvents={processedEventsTimeline}
              streamingContent={researchStream.streamingContent}
              onSubmit={handleSubmit}
              onCancel={handleCancel}
              scrollAreaRef={scrollAreaRef}
              initialEffort={savedEffort}
              initialModel={savedModel}
            />
          )}
      </main>
    </div>
  );
}
