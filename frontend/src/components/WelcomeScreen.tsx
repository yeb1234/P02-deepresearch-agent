import { Loader2 } from "lucide-react";
import { InputForm } from "./InputForm";
import { HistoryList } from "./HistoryList";
import { ClarificationCard } from "./ClarificationCard";
import type { ClarifyResponse } from "@/lib/api";

interface ClarificationState {
  topic: string;
  question: ClarifyResponse | null;
  loading: boolean;
}

interface WelcomeScreenProps {
  handleSubmit: (
    submittedInputValue: string,
    effort: string,
    model: string,
    thinking: string
  ) => void;
  onCancel: () => void;
  isLoading: boolean;
  onOpenHistory: (threadId: string) => void;
  clarification?: ClarificationState | null;
  onClarifyAnswer?: (answer: string) => void;
  onClarifySkip?: () => void;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({
  handleSubmit,
  onCancel,
  isLoading,
  onOpenHistory,
  clarification = null,
  onClarifyAnswer,
  onClarifySkip,
}) => (
  <div className="welcome-scroll h-full w-full overflow-y-auto">
    <div className="mx-auto flex min-h-[52vh] w-full max-w-3xl flex-col items-center justify-center gap-4 px-4 pb-4 pt-10 text-center">
      <div>
        <h1 className="mb-3 text-balance text-4xl font-semibold text-neutral-100 md:text-5xl">
          从问题到报告，每一步都有出处
        </h1>
        <p className="text-lg text-neutral-400 md:text-xl">
          输入研究问题，选择研究深度。完整研究先确认计划，再执行检索、写作与评审；仅搜索直接作答。每一步和来源都会保留在历史记录中。
        </p>
      </div>
      <div className="mt-4 w-full">
        {clarification ? (
          <div className="mx-auto w-full max-w-2xl text-left">
            <p className="mb-2 truncate text-xs text-neutral-500">
              原始课题：{clarification.topic}
            </p>
            {clarification.question ? (
              <ClarificationCard
                question={clarification.question}
                isLoading={clarification.loading}
                onAnswer={onClarifyAnswer || (() => {})}
                onSkip={onClarifySkip || (() => {})}
              />
            ) : (
              <div className="flex items-center gap-2 rounded-2xl border border-neutral-700 bg-neutral-800/90 px-4 py-3 text-sm text-neutral-300">
                <Loader2 className="h-4 w-4 animate-spin text-neutral-400" />
                正在梳理研究范围与约束…
              </div>
            )}
          </div>
        ) : (
          <InputForm
            onSubmit={handleSubmit}
            isLoading={isLoading}
            onCancel={onCancel}
            hasHistory={false}
          />
        )}
      </div>
      <p className="text-xs text-neutral-400">DeepResearch · 计划、检索、写作与评审</p>
    </div>

    <div className="mx-auto w-full max-w-3xl px-4 pb-16">
      <HistoryList onSelect={onOpenHistory} />
    </div>
  </div>
);
