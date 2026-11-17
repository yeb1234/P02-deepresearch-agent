import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, ChevronDown, Clock, Loader2, Play, Trash2 } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { deleteHistory, fetchHistoryDetail, type HistoryDetail, type HistoryStep } from "@/lib/api";
import { formatFullDateTime } from "@/lib/datetime";
import { cn } from "@/lib/utils";

type MdComponentProps = {
  className?: string;
  children?: ReactNode;
  [key: string]: any;
};

const mdComponents = {
  h1: ({ className, children, ...props }: MdComponentProps) => (
    <h1 className={cn("mb-2 mt-6 text-2xl font-bold text-neutral-100", className)} {...props}>
      {children}
    </h1>
  ),
  h2: ({ className, children, ...props }: MdComponentProps) => (
    <h2 className={cn("mb-2 mt-5 text-xl font-bold text-neutral-100", className)} {...props}>
      {children}
    </h2>
  ),
  h3: ({ className, children, ...props }: MdComponentProps) => (
    <h3 className={cn("mb-1 mt-4 text-lg font-semibold text-neutral-100", className)} {...props}>
      {children}
    </h3>
  ),
  p: ({ className, children, ...props }: MdComponentProps) => (
    <p className={cn("mb-3 leading-7 text-neutral-200", className)} {...props}>
      {children}
    </p>
  ),
  a: ({ className, children, href, ...props }: MdComponentProps) => (
    <a
      className={cn("text-blue-400 hover:text-blue-300", className)}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ className, children, ...props }: MdComponentProps) => (
    <ul className={cn("mb-3 list-disc pl-6 text-neutral-200", className)} {...props}>
      {children}
    </ul>
  ),
  ol: ({ className, children, ...props }: MdComponentProps) => (
    <ol className={cn("mb-3 list-decimal pl-6 text-neutral-200", className)} {...props}>
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
      className={cn("my-3 border-l-4 border-neutral-600 pl-4 text-sm italic text-neutral-400", className)}
      {...props}
    >
      {children}
    </blockquote>
  ),
  code: ({ className, children, ...props }: MdComponentProps) => (
    <code
      className={cn("rounded bg-neutral-900 px-1 py-0.5 font-mono text-xs", className)}
      {...props}
    >
      {children}
    </code>
  ),
  pre: ({ className, children, ...props }: MdComponentProps) => (
    <pre
      className={cn("my-3 overflow-x-auto rounded-lg bg-neutral-900 p-3 font-mono text-xs", className)}
      {...props}
    >
      {children}
    </pre>
  ),
  hr: ({ className, ...props }: MdComponentProps) => (
    <hr className={cn("my-4 border-neutral-700", className)} {...props} />
  ),
  table: ({ className, children, ...props }: MdComponentProps) => (
    <div className="my-3 overflow-x-auto">
      <table className={cn("w-full border-collapse", className)} {...props}>
        {children}
      </table>
    </div>
  ),
  th: ({ className, children, ...props }: MdComponentProps) => (
    <th
      className={cn("border border-neutral-600 px-3 py-2 text-left font-bold", className)}
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ className, children, ...props }: MdComponentProps) => (
    <td className={cn("border border-neutral-600 px-3 py-2", className)} {...props}>
      {children}
    </td>
  ),
};

const STATUS_LABEL: Record<string, string> = {
  running: "进行中",
  completed: "已完成",
  paused: "待确认",
  failed: "失败",
};

function formatTime(value: string | null) {
  return formatFullDateTime(value);
}

function normalizeStageLabel(value: string | null) {
  return value?.replace(/调研/g, "研究") ?? null;
}

function looksInternalText(value: string | undefined | null) {
  const text = (value || "").trim();
  return (
    !text ||
    text.startsWith("#") ||
    text.includes("需求清晰度进度条") ||
    text.includes("核心需求理解")
  );
}

function StepItem({ step, index }: { step: HistoryStep; index: number }) {
  const [open, setOpen] = useState(false);
  const content = step.content || "";
  const preview = content.length > 240 ? `${content.slice(0, 240)}…` : content;

  return (
    <div className="rounded-xl border border-neutral-700/60 bg-neutral-800/40">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left"
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-neutral-700 text-xs text-neutral-300">
          {index + 1}
        </span>
        <span className="flex-1 truncate text-sm text-neutral-200">
          {step.label.replace(/调研/g, "研究")}
        </span>
        <span className="shrink-0 text-xs text-neutral-500">
          {formatTime(step.created_at)}
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-neutral-500 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>
      {content && (
        open ? (
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words border-t border-neutral-700/60 px-3 py-3 font-sans text-sm leading-6 text-neutral-300">
            {content}
          </pre>
        ) : (
          <p className="max-h-16 overflow-hidden break-words border-t border-neutral-700/60 px-3 py-2 text-xs leading-5 text-neutral-400">
            {preview}
          </p>
        )
      )}
    </div>
  );
}

interface HistoryDetailViewProps {
  threadId: string;
  onBack: () => void;
  onContinue?: (detail: HistoryDetail) => void;
  onDeleted?: () => void;
}

export function HistoryDetailView({ threadId, onBack, onContinue, onDeleted }: HistoryDetailViewProps) {
  const [detail, setDetail] = useState<HistoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchHistoryDetail(threadId)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "历史记录加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  // 运行中的任务在历史详情里持续刷新，能看到阶段和步骤推进，而不是静态快照
  useEffect(() => {
    if (!detail || detail.status !== "running") return;
    let cancelled = false;
    let failures = 0;
    let timer: number | null = null;
    timer = window.setInterval(() => {
      fetchHistoryDetail(threadId)
        .then((data) => {
          if (cancelled) return;
          failures = 0;
          setDetail(data);
        })
        .catch(() => {
          // 连续失败时停止轮询，避免记录被删除或接口异常后无限请求。
          failures += 1;
          if (failures >= 3) {
            cancelled = true;
            if (timer !== null) window.clearInterval(timer);
          }
        });
    }, 3000);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [threadId, detail?.status]);

  const handleDelete = async () => {
    if (!detail || detail.status === "running" || deleting) return;

    // 第一次点击只切换成确认状态，第二次点击才真正删除。
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      setError(null);
      return;
    }

    setConfirmingDelete(false);
    setDeleting(true);
    setError(null);
    try {
      await deleteHistory(detail.thread_id);
      if (onDeleted) onDeleted();
      else onBack();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  };

  const stageLabel = normalizeStageLabel(detail?.stage_label ?? null);
  const title = (() => {
    const raw = (detail?.title || "").trim();
    if (looksInternalText(raw)) {
      const question = (detail?.question || "").trim();
      return looksInternalText(question) ? "未命名研究" : question;
    }
    return raw.split("\n")[0].replace(/^#+\s*/, "");
  })();
  const questionText = looksInternalText(detail?.question)
    ? ""
    : detail?.question || "";

  return (
    <div className="flex h-full flex-col">
      <div className="mx-auto flex w-full max-w-4xl items-start gap-3 px-4 pb-3 pt-6 md:px-6">
        <button
          type="button"
          onClick={onBack}
          className="mt-0.5 flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-sm text-neutral-400 transition-colors hover:bg-neutral-700/60 hover:text-neutral-200"
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold text-neutral-100">
            {title}
          </h1>
          {detail && (
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-neutral-500">
              <Clock className="h-3 w-3" />
              {formatTime(detail.created_at)}
              <span className="text-neutral-700">·</span>
              {detail.stage_label?.includes("服务重启中断")
                ? "已中断"
                : STATUS_LABEL[detail.status] ?? detail.status}
              {stageLabel && (
                <>
                  <span className="text-neutral-700">·</span>
                  <span>{stageLabel}</span>
                </>
              )}
              {detail.step_count > 0 && (
                <>
                  <span className="text-neutral-700">·</span>
                  <span>{detail.step_count} 步</span>
                </>
              )}
              {detail.updated_at && (
                <>
                  <span className="text-neutral-700">·</span>
                  <span>最近更新 {formatTime(detail.updated_at)}</span>
                </>
              )}
            </div>
          )}
        </div>
        {detail && onContinue && (
          <button
            type="button"
            onClick={() => onContinue(detail)}
            className="mt-0.5 flex shrink-0 items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500"
          >
            <Play className="h-3.5 w-3.5" />
            {detail.status === "running"
              ? "回到研究现场"
              : detail.status === "paused"
              ? "回到计划确认"
              : detail.status === "failed"
              ? "回到会话"
              : "继续追问"}
          </button>
        )}
        {detail && (
          <div className="mt-0.5 flex shrink-0 items-center gap-1.5">
            {confirmingDelete && !deleting && (
              <button
                type="button"
                onClick={() => setConfirmingDelete(false)}
                className="rounded-lg px-2 py-1.5 text-xs text-neutral-400 transition-colors hover:bg-neutral-700/60 hover:text-neutral-200"
              >
                取消
              </button>
            )}
            <button
              type="button"
              onClick={handleDelete}
              disabled={detail.status === "running" || deleting}
              title={
                detail.status === "running"
                  ? "研究进行中，暂不能删除"
                  : "删除这条研究记录"
              }
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                confirmingDelete
                  ? "border-red-500/50 bg-red-500/15 text-red-300 hover:bg-red-500/25"
                  : "border-red-500/30 text-red-400 hover:bg-red-500/10 hover:text-red-300"
              )}
            >
              {deleting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Trash2 className="h-3.5 w-3.5" />
              )}
              {deleting ? "删除中" : confirmingDelete ? "确认删除" : "删除"}
            </button>
          </div>
        )}
      </div>

      <ScrollArea className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-4 pb-16 pt-2 md:px-6">
          {loading ? (
            <div className="flex items-center gap-2 py-10 text-sm text-neutral-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载研究记录…
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-4 text-sm text-red-400">
              {error}
              <button
                type="button"
                onClick={onBack}
                className="ml-2 underline underline-offset-2 hover:text-red-300"
              >
                返回历史列表
              </button>
            </div>
          ) : detail ? (
            <>
              {questionText && (
                <div className="mb-6 rounded-2xl border border-neutral-700/60 bg-neutral-800/60 px-4 py-3">
                  <div className="mb-1 text-xs text-neutral-500">研究问题</div>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-neutral-200">
                    {questionText}
                  </p>
                </div>
              )}

              {detail.steps && detail.steps.length > 0 && (
                <section className="mb-6">
                  <div className="mb-2 flex items-center gap-2 text-xs text-neutral-500">
                    <span>研究过程</span>
                    <span className="text-neutral-600">
                      {detail.steps.length} 步 · 点击展开每一步的产出
                    </span>
                  </div>
                  <div className="flex flex-col gap-2">
                    {detail.steps.map((step, index) => (
                      <StepItem key={step.seq} step={step} index={index} />
                    ))}
                  </div>
                </section>
              )}

              {detail.answer ? (
                <article className="text-[15px]">
                  <div className="mb-2 text-xs text-neutral-500">最终报告</div>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                    {detail.answer}
                  </ReactMarkdown>
                </article>
              ) : (
                <div className="rounded-2xl border border-dashed border-neutral-700 bg-neutral-800/40 px-4 py-5">
                  <p className="text-sm text-neutral-300">
                    {detail.stage_label?.includes("服务重启中断")
                      ? `这次研究在「${(stageLabel || detail.stage_label).replace("（服务重启中断）", "")}」被服务重启中断，没有生成最终报告。`
                      : detail.status === "paused"
                      ? "这次研究停留在计划确认阶段，还没有生成最终报告。"
                      : detail.status === "failed"
                      ? "这次研究执行失败，没有生成最终报告。"
                      : "这次研究还没有保存最终报告。"}
                  </p>
                  {detail.plan && detail.steps.length === 0 && (
                    <div className="mt-4">
                      <div className="mb-2 text-xs text-neutral-500">当时的研究计划</div>
                      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-xl bg-neutral-900/70 p-4 font-sans text-sm leading-6 text-neutral-300">
                        {detail.plan}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  );
}
