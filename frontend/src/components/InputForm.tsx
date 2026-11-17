import { useState, useEffect, forwardRef, useImperativeHandle } from "react";
import { Button } from "@/components/ui/button";
import { SquarePen, Brain, Send, StopCircle, Zap, Cpu } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchAvailableModels, getDefaultModelId, type ModelConfig, FALLBACK_DEFAULT_MODEL_ID, FALLBACK_MODELS } from "@/lib/api";

// 与后端 /api/models 保持一致的兜底模型，避免接口未返回时下拉框显示英文占位符
const DEFAULT_MODELS: ModelConfig[] = FALLBACK_MODELS;

// Updated InputFormProps
interface InputFormProps {
  onSubmit: (inputValue: string, effort: string, model: string, thinking: string) => void;
  onCancel: () => void;
  isLoading: boolean;
  hasHistory: boolean;
  /** 切换到会话视图时沿用上一次选择，避免模型/深度被重置 */
  initialEffort?: string;
  initialModel?: string;
  /** 输入框占位文案：计划待确认时提示可以补充需求 */
  placeholder?: string;
}

/** 研究深度同时决定模型思考档位：快速关闭，标准/深入才开启。 */
function thinkingFromEffort(effort: string): string {
  if (effort === "medium") return "standard";
  if (effort === "high") return "deep";
  return "off";
}

export const InputForm = forwardRef<any, InputFormProps>(({
  onSubmit,
  onCancel,
  isLoading,
  hasHistory,
  initialEffort = "low",
  initialModel = FALLBACK_DEFAULT_MODEL_ID,
  placeholder = "例如：分析国内 AI 芯片产业链的关键瓶颈与竞争变量",
}, ref) => {
  const [internalInputValue, setInternalInputValue] = useState("");
  const [effort, setEffort] = useState(initialEffort);
  const [model, setModel] = useState(initialModel);
  const [availableModels, setAvailableModels] = useState<ModelConfig[]>(DEFAULT_MODELS);

  // 加载可用模型列表；接口异常时保留默认列表，保证下拉框始终有可读值
  useEffect(() => {
    let cancelled = false;
    fetchAvailableModels().then((models) => {
      if (cancelled || models.length === 0) return;
      setAvailableModels(models);
      setModel((current) =>
        models.some((item) => item.model_id === current)
          ? current
          : getDefaultModelId(models)
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // 暴露给父组件的方法（用于 ChatMessagesView 的“确认计划”按钮）
  useImperativeHandle(ref, () => ({
    setInputValue(value: string) {
      setInternalInputValue(value);
    },
    submitInput(value: string) {
      const currentEffort = effort;
      const currentModel = model;
      if (!isLoading && value.trim()) {
        onSubmit(value, currentEffort, currentModel, thinkingFromEffort(currentEffort));
        setInternalInputValue("");
      }
    },
  }), [effort, model, onSubmit, isLoading]);

  const handleInternalSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (isLoading || !internalInputValue.trim()) return;
    onSubmit(internalInputValue, effort, model, thinkingFromEffort(effort));
    setInternalInputValue("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    // 中文输入法组词期间不能把回车当作提交。
    if (e.nativeEvent.isComposing) return;
    e.preventDefault();
    handleInternalSubmit();
  };

  const isSubmitDisabled = !internalInputValue.trim() || isLoading;

  return (
    <form
      onSubmit={handleInternalSubmit}
      className={`flex flex-col gap-2 p-3 pb-4`}
    >
      <div
        className={`flex flex-row items-end justify-between text-white rounded-3xl rounded-bl-sm ${
          hasHistory ? "rounded-br-sm" : ""
        } break-words min-h-7 bg-neutral-700 px-4 pt-3 transition-shadow focus-within:outline focus-within:outline-1 focus-within:outline-blue-500/60 focus-within:outline-offset-0`}
      >
        <Textarea
          value={internalInputValue}
          onChange={(e) => setInternalInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          aria-label="研究问题"
          className={`w-full text-neutral-100 placeholder-neutral-400 resize-none border-0 focus:outline-none focus:ring-0 outline-none focus-visible:ring-0 shadow-none
                        md:text-base  min-h-[56px] max-h-[200px]`}
          rows={1}
        />
        <div className="mb-1">
          {isLoading ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="停止接收当前研究进度"
              title="停止接收当前研究进度"
              className="text-red-500 hover:text-red-400 hover:bg-red-500/10 p-2 cursor-pointer rounded-full transition-all duration-200"
              onClick={onCancel}
            >
              <StopCircle className="h-5 w-5" />
            </Button>
          ) : (
            <Button
              type="submit"
              variant="ghost"
              className={`${
                isSubmitDisabled
                  ? "text-neutral-500"
                  : "text-blue-500 hover:text-blue-400 hover:bg-blue-500/10"
              } p-2 cursor-pointer rounded-full transition-all duration-200 text-base`}
              disabled={isSubmitDisabled}
            >
              开始研究
              <Send className="h-5 w-5" />
            </Button>
          )}
        </div>
      </div>
      <div className="flex flex-col items-start gap-1">
        <div className="flex flex-wrap gap-2">
          <div
            className="flex flex-row gap-2 bg-neutral-700 border-neutral-600 text-neutral-300 focus:ring-neutral-500 rounded-xl rounded-t-sm pl-2 max-w-[100%] sm:max-w-[90%]"
            title="研究深度决定检索轮次和模型思考强度；仅搜索只做一轮检索，不生成计划、提纲与评审"
          >
            <div className="flex flex-row items-center text-sm">
              <Brain className="h-4 w-4 mr-2" />
              研究深度
            </div>
            <Select value={effort} onValueChange={setEffort}>
              <SelectTrigger aria-label="研究深度" className="w-[110px] bg-transparent border-none cursor-pointer">
                <SelectValue placeholder="选择深度" />
              </SelectTrigger>
              <SelectContent className="bg-neutral-700 border-neutral-600 text-neutral-300 cursor-pointer">
                <SelectItem
                  value="search"
                  className="hover:bg-neutral-600 focus:bg-neutral-600 cursor-pointer"
                >
                  仅搜索
                </SelectItem>
                <SelectItem
                  value="low"
                  className="hover:bg-neutral-600 focus:bg-neutral-600 cursor-pointer"
                >
                  快速
                </SelectItem>
                <SelectItem
                  value="medium"
                  className="hover:bg-neutral-600 focus:bg-neutral-600 cursor-pointer"
                >
                  标准
                </SelectItem>
                <SelectItem
                  value="high"
                  className="hover:bg-neutral-600 focus:bg-neutral-600 cursor-pointer"
                >
                  深入
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-row gap-2 bg-neutral-700 border-neutral-600 text-neutral-300 focus:ring-neutral-500 rounded-xl rounded-t-sm pl-2  max-w-[100%] sm:max-w-[90%]">
            <div className="flex flex-row items-center text-sm ml-2">
              <Cpu className="h-4 w-4 mr-2" />
              模型选择
            </div>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger aria-label="模型选择" className="w-[150px] bg-transparent border-none cursor-pointer">
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent className="bg-neutral-700 border-neutral-600 text-neutral-300 cursor-pointer">
                {availableModels.map((modelConfig) => {
                  const IconComponent = modelConfig.icon === "Cpu" ? Cpu : Zap;
                  return (
                    <SelectItem
                      key={modelConfig.model_id}
                      value={modelConfig.model_id}
                      className="hover:bg-neutral-600 focus:bg-neutral-600 cursor-pointer"
                    >
                      <div className="flex items-center">
                        <IconComponent className={`h-4 w-4 mr-2 text-${modelConfig.icon_color}`} />
                        {modelConfig.display_name}
                      </div>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>
        </div>
        <p className="text-xs leading-5 text-neutral-400">
          仅搜索：完成一轮检索后直接回答，不生成计划与报告。快速、标准、深入：逐级增加检索轮次与评审轮次。
        </p>
        {hasHistory && (
          <Button
            className="bg-neutral-700 border-neutral-600 text-neutral-300 cursor-pointer rounded-xl rounded-t-sm pl-2 "
            variant="default"
            onClick={() => window.open("/", "_blank")}
          >
            <SquarePen size={16} />
            新建课题
          </Button>
        )}
      </div>
    </form>
  );
});
