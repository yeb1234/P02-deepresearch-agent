import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Clock, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { deleteHistory, fetchHistory, type HistoryItem } from "@/lib/api";
import { formatDateTime, formatRelativeTime } from "@/lib/datetime";
import { cn } from "@/lib/utils";

interface HistoryListProps {
  onSelect: (threadId: string) => void;
}

const STATUS_META: Record<string, { label: string; className: string }> = {
  running: {
    label: "进行中",
    className: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  },
  completed: {
    label: "已完成",
    className: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  },
  paused: {
    label: "待确认",
    className: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  },
  failed: {
    label: "失败",
    className: "bg-red-500/10 text-red-400 border-red-500/30",
  },
};

function formatTime(value: string | null) {
  return formatDateTime(value);
}

function formatRelative(value: string | null) {
  return formatRelativeTime(value);
}

function normalizeStageLabel(value: string | null) {
  return value?.replace(/调研/g, "研究") ?? null;
}

function looksInternalText(value: string) {
  const text = value.trim();
  return (
    !text ||
    text.startsWith("#") ||
    text.includes("需求清晰度进度条") ||
    text.includes("核心需求理解")
  );
}

 // 历史标题来自后端，偶尔会把需求澄清的内部文本写进 title；
 // 列表里优先用用户自己的问题做标题，避免把计划片段当成记录名。
function displayTitle(item: { title: string; question: string }) {
  const title = (item.title || "").trim();
  if (looksInternalText(title)) {
    const question = (item.question || "").trim();
    return looksInternalText(question) ? "未命名研究" : question;
  }
  return title.split("\n")[0].replace(/^#+\s*/, "");
}

export function HistoryList({ onSelect }: HistoryListProps) {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false); // 默认折叠，点击标题展开
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchHistory();
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "历史记录加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleDelete = async (item: HistoryItem) => {
    if (item.status === "running" || deletingId === item.id) return;

    // 第一次点击只展开确认区，第二次点击才真正删除，避免误触。
    if (confirmingId !== item.id) {
      setConfirmingId(item.id);
      setError(null);
      return;
    }

    setConfirmingId(null);
    setDeletingId(item.id);
    setError(null);
    try {
      await deleteHistory(item.thread_id);
      setItems((prev) => prev.filter((row) => row.id !== item.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="w-full text-left">
      <div className="mb-3 flex items-center justify-between">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex items-center gap-2 rounded-lg px-1 py-1 text-left text-sm font-medium tracking-wide text-neutral-300 transition-colors hover:text-neutral-100"
        >
          <ChevronDown
            className={cn(
              "h-4 w-4 text-neutral-500 transition-transform",
              open && "rotate-180"
            )}
          />
          历史研究
          {!loading && items.length > 0 && (
            <span className="text-xs font-normal text-neutral-500">
              {items.length}
            </span>
          )}
          {!open && (
            <span className="text-xs font-normal text-neutral-500">
              点击展开
            </span>
          )}
        </button>
        {open && (
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-neutral-500 transition-colors hover:bg-neutral-700/60 hover:text-neutral-300 disabled:cursor-not-allowed"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            刷新
          </button>
        )}
      </div>

      {open && (loading ? (
        <div className="flex items-center gap-2 rounded-2xl border border-neutral-700/60 bg-neutral-800/40 px-4 py-5 text-sm text-neutral-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载研究记录…
        </div>
      ) : error ? (
        <div className="rounded-2xl border border-red-500/20 bg-red-500/5 px-4 py-4 text-sm text-red-400">
          {error}
          <button
            type="button"
            onClick={load}
            className="ml-2 underline underline-offset-2 hover:text-red-300"
          >
            重试
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-neutral-700 bg-neutral-800/30 px-4 py-8 text-center">
          <p className="text-sm text-neutral-400">还没有研究记录</p>
          <p className="mt-1 text-xs text-neutral-500">
            完成的研究会保留在这里，可随时回看
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {items.map((item) => {
            const interrupted = item.stage_label?.includes("服务重启中断");
            const stageLabel = normalizeStageLabel(item.stage_label);
            const title = displayTitle(item);
            const status = interrupted
              ? {
                  label: "已中断",
                  className: "bg-amber-500/10 text-amber-400 border-amber-500/30",
                }
              : STATUS_META[item.status] ?? {
                  label: item.status || "未知",
                  className: "bg-neutral-700 text-neutral-300 border-neutral-600",
                };
            return (
              <div
                key={item.id}
                className="group flex w-full items-center gap-2 rounded-2xl border border-neutral-700/70 bg-neutral-800/60 transition-colors hover:border-neutral-600 hover:bg-neutral-700/50"
              >
                <button
                  type="button"
                  onClick={() => onSelect(item.thread_id)}
                  className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3 text-left"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm text-neutral-100">
                        {title}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 rounded-full border px-1.5 py-0.5 text-[11px] leading-none",
                          status.className
                        )}
                      >
                        {status.label}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1 text-xs text-neutral-500">
                      <Clock className="h-3 w-3" />
                      {formatTime(item.updated_at || item.created_at)}
                      {item.updated_at && (
                        <span className="text-neutral-500">
                          · 更新 {formatRelative(item.updated_at)}
                        </span>
                      )}
                      {stageLabel && (
                        <span className="ml-1 text-neutral-400">
                          · {stageLabel}
                        </span>
                      )}
                      {item.step_count > 0 && (
                        <span className="text-neutral-500">
                          {item.step_count} 步
                        </span>
                      )}
                    </div>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-neutral-600 transition-colors group-hover:text-neutral-300" />
                </button>
                <div className="mr-2 flex shrink-0 items-center gap-1 py-1">
                  {deletingId === item.id ? (
                    <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />
                  ) : confirmingId === item.id ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setConfirmingId(null)}
                        className="rounded-md px-2 py-1 text-[11px] text-neutral-400 transition-colors hover:bg-neutral-700 hover:text-neutral-200"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(item)}
                        className="rounded-md bg-red-500/10 px-2 py-1 text-[11px] text-red-400 transition-colors hover:bg-red-500/20"
                      >
                        确认删除
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => handleDelete(item)}
                      disabled={item.status === "running"}
                      title={
                        item.status === "running"
                          ? "研究进行中，暂不能删除"
                          : "删除这条研究记录"
                      }
                      aria-label={`删除研究记录 ${title}`}
                      className="flex h-9 w-9 items-center justify-center rounded-lg text-neutral-500 transition-colors hover:bg-red-500/10 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}
