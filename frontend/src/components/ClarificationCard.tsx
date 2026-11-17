import { useEffect, useState } from "react";
import { Loader2, MessageCircleQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { ClarifyResponse } from "@/lib/api";

interface ClarificationCardProps {
  question: ClarifyResponse;
  isLoading: boolean;
  onAnswer: (answer: string) => void;
  onSkip: () => void;
}

const OPTION_LETTERS = ["A", "B", "C", "D"];

export function ClarificationCard({
  question,
  isLoading,
  onAnswer,
  onSkip,
}: ClarificationCardProps) {
  const [otherMode, setOtherMode] = useState(false);
  const [otherValue, setOtherValue] = useState("");
  const round = question.round || 1;
  const progress = Math.max(0, Math.min(100, question.progress ?? round * 30));

  // 换到下一道题时重置“其他”输入，避免上一题的展开状态和草稿带到新题。
  useEffect(() => {
    setOtherMode(false);
    setOtherValue("");
  }, [question.question]);

  const handleOtherSubmit = () => {
    const value = otherValue.trim();
    if (!value || isLoading) return;
    onAnswer(value);
  };

  return (
    <div className="w-full rounded-2xl border border-neutral-700 bg-neutral-800/90 p-4 text-left shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium text-neutral-200">
          <MessageCircleQuestion className="h-4 w-4 text-blue-400" />
          研究范围澄清
          <span className="text-xs font-normal text-neutral-500">第 {round} / 3 个问题</span>
        </div>
        {isLoading && <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />}
      </div>

      <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-neutral-700">
        <div
          className="h-full rounded-full bg-blue-500/80 transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>

      <p className="mt-4 text-base font-medium leading-7 text-neutral-100">
        {question.question}
      </p>
      {question.reason && (
        <p className="mt-1 text-xs text-neutral-500">{question.reason}</p>
      )}

      <div className="mt-3 flex flex-col gap-2">
        {(question.options || []).map((option, index) => (
          <button
            key={`${option.label}-${index}`}
            type="button"
            disabled={isLoading}
            onClick={() => onAnswer(option.label)}
            className="group flex w-full items-start gap-3 rounded-xl border border-neutral-700 bg-neutral-800/60 px-3 py-2.5 text-left transition-colors hover:border-neutral-500 hover:bg-neutral-700/70 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-neutral-600 bg-neutral-800 text-xs font-semibold text-neutral-300 group-hover:border-blue-400/60 group-hover:text-blue-300">
              {OPTION_LETTERS[index] || index + 1}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-neutral-100">
                {option.label}
                {option.recommended && (
                  <span className="rounded border border-blue-500/30 bg-blue-500/10 px-1.5 py-0.5 text-[10px] font-normal text-blue-300">
                    建议
                  </span>
                )}
              </span>
              {option.description && (
                <span className="mt-0.5 block text-xs leading-5 text-neutral-400">
                  {option.description}
                </span>
              )}
            </span>
          </button>
        ))}

        {question.allow_other !== false && !otherMode && (
          <button
            type="button"
            disabled={isLoading}
            onClick={() => setOtherMode(true)}
            className="flex w-full items-start gap-3 rounded-xl border border-dashed border-neutral-700 px-3 py-2.5 text-left transition-colors hover:border-neutral-500 hover:bg-neutral-700/40 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-neutral-600 bg-neutral-800 text-xs font-semibold text-neutral-300">
              {OPTION_LETTERS[(question.options || []).length] || "E"}
            </span>
            <span className="text-sm text-neutral-300">
              其他
              <span className="ml-2 text-xs text-neutral-500">自行填写具体范围或要求</span>
            </span>
          </button>
        )}
      </div>

      {otherMode && (
        <div className="mt-3 rounded-xl border border-neutral-700 bg-neutral-900/50 p-2">
          <Textarea
            value={otherValue}
            onChange={(event) => setOtherValue(event.target.value)}
            placeholder="例如：只看中国大陆市场，2025 年至今，偏商业决策"
            className="min-h-[64px] resize-none border-0 bg-transparent text-sm text-neutral-100 placeholder-neutral-500 shadow-none focus-visible:ring-0"
            rows={2}
            autoFocus
          />
          <div className="mt-1 flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              className="text-neutral-400 hover:text-neutral-200"
              onClick={() => setOtherMode(false)}
              disabled={isLoading}
            >
              取消
            </Button>
            <Button
              type="button"
              className="bg-blue-600 text-white hover:bg-blue-500"
              onClick={handleOtherSubmit}
              disabled={!otherValue.trim() || isLoading}
            >
              提交
            </Button>
          </div>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between border-t border-neutral-700/70 pt-3">
        <button
          type="button"
          onClick={onSkip}
          disabled={isLoading}
          className="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-300 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
        >
          跳过澄清，直接按原问题研究
        </button>
        <span className="text-[11px] text-neutral-600">选择后自动进入下一题</span>
      </div>
    </div>
  );
}
