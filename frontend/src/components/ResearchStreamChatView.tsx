// ResearchStreamChatView — 显示 useResearchStream 通道的消息流
// 用于首次 plan 生成 + plan 确认后的研究流程（与 LangGraph SDK 通道共存）

import React, { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, Copy, CopyCheck } from "lucide-react";
import { InputForm } from "@/components/InputForm";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ResearchStreamStatus } from "@/lib/useResearchStream";
import {
  ActivityTimeline,
  ProcessedEvent,
} from "@/components/ActivityTimeline";

type MdComponentProps = {
  className?: string;
  children?: React.ReactNode;
  [key: string]: any;
};

const mdComponents = {
  h1: ({ className, children, ...props }: MdComponentProps) => (
    <h1 className={cn("text-2xl font-bold mt-4 mb-2", className)} {...props}>
      {children}
    </h1>
  ),
  h2: ({ className, children, ...props }: MdComponentProps) => (
    <h2 className={cn("text-xl font-bold mt-3 mb-2", className)} {...props}>
      {children}
    </h2>
  ),
  h3: ({ className, children, ...props }: MdComponentProps) => (
    <h3 className={cn("text-lg font-bold mt-3 mb-1", className)} {...props}>
      {children}
    </h3>
  ),
  p: ({ className, children, ...props }: MdComponentProps) => (
    <p className={cn("mb-3 leading-7", className)} {...props}>
      {children}
    </p>
  ),
  a: ({ className, children, href, ...props }: MdComponentProps) => (
    <a
      className={cn(
        "break-words text-blue-400 underline decoration-blue-400/35 underline-offset-2 hover:text-blue-300",
        className
      )}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ className, children, ...props }: MdComponentProps) => (
    <ul className={cn("list-disc pl-6 mb-3", className)} {...props}>
      {children}
    </ul>
  ),
  ol: ({ className, children, ...props }: MdComponentProps) => (
    <ol className={cn("list-decimal pl-6 mb-3", className)} {...props}>
      {children}
    </ol>
  ),
  li: ({ className, children, ...props }: MdComponentProps) => (
    <li className={cn("mb-1", className)} {...props}>
      {children}
    </li>
  ),
  blockquote: ({ className, children, ...props }: MdComponentProps) => (
    <blockquote
      className={cn(
        "border-l-4 border-neutral-600 pl-4 italic my-3 text-sm",
        className
      )}
      {...props}
    >
      {children}
    </blockquote>
  ),
  code: ({ className, children, ...props }: MdComponentProps) => (
    <code
      className={cn(
        "bg-neutral-900 rounded px-1 py-0.5 font-mono text-xs",
        className
      )}
      {...props}
    >
      {children}
    </code>
  ),
  pre: ({ className, children, ...props }: MdComponentProps) => (
    <pre
      className={cn(
        "bg-neutral-900 p-3 rounded-lg overflow-x-auto font-mono text-xs my-3",
        className
      )}
      {...props}
    >
      {children}
    </pre>
  ),
  hr: ({ className, ...props }: MdComponentProps) => (
    <hr className={cn("border-neutral-600 my-4", className)} {...props} />
  ),
};

function formatDuration(ms: number) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes > 0) return `${minutes}分${String(seconds).padStart(2, "0")}秒`;
  return `${seconds}秒`;
}

function formatAgo(ms: number) {
  if (ms < 5_000) return "刚刚";
  if (ms < 60_000) return `${Math.floor(ms / 1000)} 秒前`;
  return `${Math.floor(ms / 60_000)} 分钟前`;
}

interface ResearchStreamChatViewProps {
  messages: Array<{ type: string; content: string; id: string }>;
  isLoading: boolean;
  status: ResearchStreamStatus;
  liveActivityEvents: ProcessedEvent[];
  /** 当前节点已流式输出的累计文本 */
  streamingContent: string;
  onSubmit: (inputValue: string, effort: string, model: string, thinking: string) => void;
  onCancel: () => void;
  scrollAreaRef: React.RefObject<HTMLDivElement | null>;
  initialEffort?: string;
  initialModel?: string;
}

export function ResearchStreamChatView({
  messages,
  isLoading,
  status,
  liveActivityEvents,
  streamingContent,
  onSubmit,
  onCancel,
  scrollAreaRef,
  initialEffort,
  initialModel,
}: ResearchStreamChatViewProps) {
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  // 已确认过的计划消息 id：防止同一份计划在加载态切换时再次露出确认按钮
  const [confirmedMessageId, setConfirmedMessageId] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const inputFormRef = React.useRef<any>(null);

  useEffect(() => {
    if (!isLoading) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [isLoading]);

  // 研究流是持续追加的；只有用户仍在底部附近时才自动跟随，避免打断向上回看。
  useEffect(() => {
    const viewport = scrollAreaRef.current?.querySelector(
      "[data-radix-scroll-area-viewport]"
    ) as HTMLElement | null;
    if (!viewport) return;
    const distance =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    if (distance < 160) {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, [messages, streamingContent, liveActivityEvents, scrollAreaRef]);

  const elapsed = status.startedAt ? formatDuration(now - status.startedAt) : "";
  const idleFor = status.lastEventAt ? Math.max(0, now - status.lastEventAt) : 0;
  const isStalled = isLoading && idleFor >= 90_000;
  const isVeryStalled = isLoading && idleFor >= 300_000;
  const isPendingPlan = status.phase === "计划已生成" || status.phase === "等待确认计划";
  const lastAiMessageIndex = messages.reduce(
    (lastIndex, message, index) => (message.type === "ai" ? index : lastIndex),
    -1
  );

  const handleCopy = async (text: string, messageId: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedMessageId(messageId);
      setTimeout(() => setCopiedMessageId(null), 2000);
    } catch (err) {
      console.error("复制失败: ", err);
    }
  };

  // "确认计划"按钮点击事件
  const handleStartResearch = (messageId: string) => {
    // 先锁住这条计划消息，避免加载态切换时按钮重新露出被二次点击
    setConfirmedMessageId(messageId);
    if (inputFormRef.current?.setInputValue && inputFormRef.current?.submitInput) {
      inputFormRef.current.setInputValue("确认计划");
      inputFormRef.current.submitInput("确认计划");
    }
  };

  return (
    <div className="flex flex-col h-full">
      <ScrollArea className="flex-1 overflow-y-auto" ref={scrollAreaRef}>
        <div className="p-4 md:p-6 space-y-2 max-w-4xl mx-auto pt-16">
          {messages.map((message, index) => (
            <div key={message.id || `msg-${index}`} className="space-y-6">
              <div
                className={`flex items-start gap-3 ${
                  message.type === "human" ? "justify-end" : ""
                }`}
              >
                {message.type === "human" ? (
                  <div className="text-white rounded-3xl break-words min-h-7 bg-neutral-700 max-w-[100%] sm:max-w-[90%] px-4 pt-3 rounded-br-lg">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {typeof message.content === "string"
                        ? message.content
                        : JSON.stringify(message.content)}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className="relative break-words flex flex-col">
                    {liveActivityEvents.length > 0 &&
                      index === lastAiMessageIndex && (
                      <div className="mb-3 border-b border-neutral-700 pb-3 text-xs">
                        <ActivityTimeline
                          processedEvents={liveActivityEvents}
                          isLoading={isLoading}
                          title="研究过程"
                        />
                      </div>
                    )}
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                      {typeof message.content === "string"
                        ? message.content
                        : JSON.stringify(message.content)}
                    </ReactMarkdown>
                    {message.content && (
                      <Button
                        variant="default"
                        className="cursor-pointer bg-neutral-700 border-neutral-600 text-neutral-300 self-end"
                        onClick={() => handleCopy(message.content, message.id)}
                      >
                        {copiedMessageId === message.id ? "已复制" : "复制"}
                        {copiedMessageId === message.id ? <CopyCheck /> : <Copy />}
                      </Button>
                    )}
                  </div>
                )}
              </div>
              {/* 在最后一条 AI 消息后显示"确认计划"按钮（仅当处于 plan 待确认状态时） */}
              {index === messages.length - 1 &&
                message.type === "ai" &&
                !isLoading &&
                (status.phase === "计划已生成" || status.phase === "等待确认计划") &&
                message.id !== confirmedMessageId &&
                !liveActivityEvents.some(e => e.title === "最终确定答案") && (
                  <div className="flex items-start gap-3">
                    <div className="relative group max-w-[85%] md:max-w-[80%] rounded-xl p-3 shadow-sm break-words bg-neutral-800 text-neutral-100">
                      <div className="flex items-center justify-between gap-4">
                        <span className="text-sm text-neutral-400">
                          确认计划后开始检索、写作与评审；如需调整，请在下方补充说明。
                        </span>
                        <Button
                          variant="default"
                          className="bg-neutral-700 hover:bg-neutral-600 cursor-pointer text-sm shrink-0"
                          onClick={() => handleStartResearch(message.id)}
                        >
                          确认计划
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
            </div>
          ))}
          {/* 流式内容气泡（实时 token 累积渲染） */}
          {streamingContent && (
            <div className="flex items-start gap-3">
              <div className="relative break-words flex flex-col w-full max-w-[85%] md:max-w-[80%] bg-neutral-800 text-neutral-100 rounded-xl rounded-bl-none p-3 shadow-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {streamingContent}
                </ReactMarkdown>
                <span className="text-xs text-neutral-500 mt-1 inline-flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  正在生成回答…
                </span>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
      {isLoading && (
        <div className="mx-auto w-full max-w-4xl px-4 md:px-6 pb-2">
          <div className="rounded-xl border border-neutral-700 bg-neutral-800/80 px-3 py-2 text-xs text-neutral-300">
            <div className="flex items-center gap-2">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-400" />
              <span className="font-medium text-neutral-200">
                {status.phase || "研究进行中"}
              </span>
              {status.detail && (
                <span className="text-neutral-500 truncate">· {status.detail}</span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-neutral-500">
              {elapsed && <span>已运行 {elapsed}</span>}
              <span>最近更新 {formatAgo(idleFor)}</span>
              {status.eventCount > 0 && <span>已记录 {status.eventCount} 个研究步骤</span>}
            </div>
            {isStalled && (
              <p className={cn("mt-2", isVeryStalled ? "text-red-400" : "text-amber-400")}>
                已 {formatDuration(idleFor)} 没有新进展，当前停在「{status.phase}」。
                {isVeryStalled
                  ? "可以停止接收进度；已完成的步骤会保留在历史记录中。"
                  : "如果仍无更新，可以停止接收进度，之后在历史记录中继续查看。"}
              </p>
            )}
          </div>
        </div>
      )}
      <InputForm
        ref={inputFormRef}
        onSubmit={onSubmit}
        isLoading={isLoading}
        onCancel={onCancel}
        hasHistory={messages.length > 0}
        initialEffort={initialEffort}
        initialModel={initialModel}
        placeholder={
          isPendingPlan
            ? "补充研究范围，或输入“确认计划”开始执行"
            : "继续追问本次研究，或输入新的问题"
        }
      />
    </div>
  );
}
