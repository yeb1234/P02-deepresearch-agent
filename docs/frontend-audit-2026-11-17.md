# DeepResearch 前端全站审计

审计日期：2026-11-17  
范围：`frontend/src` 全部页面与共享组件、`vite.config.ts`、`nginx.conf`、构建产物  
基线：`http://localhost:12309/app/`，视口 609×883

## 结论

这轮审计确认的问题里，影响实际使用的部分已经修完，构建通过；剩下的主要是后端标题/步骤文案质量、历史列表首屏请求和两处路由/鉴权边界，不属于阻断性缺陷。

| 维度 | 判断 |
|---|---|
| 功能硬伤 | 已修：重复时间线、计划状态反向赋值、SSE 断线、进度轮询不终止、取消后整页刷新、输入法回车、历史详情轮询不停 |
| 样式一致性 | 已统一“调研→研究”、活动时间线图标、历史标题兜底、链接样式、滚动条可见度；仍存在少量 12px 次级信息 |
| 性能 | 主包从约 664KB 降到 462KB，Markdown/LangGraph 已拆分；流式 token 改为 80ms 合并刷新；路由未懒加载 |
| 设计 | 深色基底保留，欢迎页标题从三行降到两行；低对比、小触控区做了局部修正，未做整体视觉改版 |

## 一、已修复

### P0 · 会造成明显错误或丢状态

1. **研究事件时间线重复累积**
   - 原实现每次 `researchStream.events` 变化都遍历完整数组，事件又是追加的，历史事件被反复处理，时间线会越来越长。
   - 已改为 `processedResearchEventCountRef` 游标，只处理新增切片；数组变短时重置游标。
   - 文件：`src/App.tsx`

2. **计划状态反向赋值**
   - `researchStream` 的 `generate_plan` 分支写成 `setAwaitingPlanConfirmation("confirmed")`，与 `thread` 通道的 `"unconfirmed"` 相反。
   - 已改回 `"unconfirmed"`。
   - 文件：`src/App.tsx`

3. **取消研究后整页刷新**
   - 原 `handleCancel` 调 `thread.stop()` 后 `window.location.reload()`，研究流任务没有显式停止，当前会话直接丢失。
   - 已改为：研究流有消息或 loading 时只 `researchStream.stop()`，否则 `thread.stop()`，不再 reload。
   - 文件：`src/App.tsx`

4. **SSE 断线不自动恢复**
   - 原 `onerror` 直接 `close()`，浏览器自动重连和 `Last-Event-ID` 回放都被禁用。
   - 已改为：非 CLOSED 状态保留 EventSource 自动重连，连续 10 次失败后才终止并落到错误状态。
   - 文件：`src/lib/useResearchStream.ts`

### P1 · 影响日常操作

5. **`watchTask` 无限轮询**
   - 原实现每 3 秒请求历史详情，失败只改状态，定时器继续跑；记录被删或接口异常会无限请求。
   - 已改为连续 3 次失败后停止轮询，并提示可回历史列表查看。
   - 文件：`src/lib/useResearchStream.ts`

6. **`watchTask` 伪造“最近更新”**
   - 原来每次轮询成功都把 `lastEventAt` 设为 `Date.now()`，后台没有推进也会显示“刚刚更新”。
   - 已改用后端 `updated_at` 解析出的真实时间。
   - 文件：`src/lib/useResearchStream.ts`

7. **历史详情轮询失败不终止**
   - `fetchHistoryDetail(...).catch(() => {})` 后每 3 秒继续请求，记录删除后不会停。
   - 已改为连续 3 次失败停止轮询。
   - 文件：`src/components/HistoryDetailView.tsx`

8. **输入框只支持 Ctrl/Cmd+Enter**
   - 原交互不符合普通聊天输入习惯，也没有处理中文输入法组词。
   - 已改为普通 Enter 发送、Shift+Enter 换行、`isComposing` 期间忽略 Enter；提交和 `submitInput` 都加了 loading 守卫。
   - 文件：`src/components/InputForm.tsx`

9. **输入框无可见键盘焦点**
   - 原 textarea 上 `outline-none focus-visible:ring-0`，容器也没有焦点反馈。
   - 已改为容器 `focus-within:outline`，1px 蓝色描边；实机计算样式为 `solid 1px`、颜色 alpha 0.6。
   - 文件：`src/components/InputForm.tsx`

10. **多行输入时发送按钮悬在输入区中部**
    - 原容器 `items-center` 且按钮 `-mt-3`，输入三行后按钮停在中间。
    - 已改为容器 `items-end`、按钮 `mb-1`；609×883 下按钮底部 328px，容器底部 332px，间距 4px。
    - 文件：`src/components/InputForm.tsx`

11. **`ClarificationCard` 的“其他”状态不复位**
    - 用户在上一题选“其他”并提交后，下一题仍保持 `otherMode=true` 和空 textarea。
    - 已加 `useEffect`，在 `question.question` 变化时重置 `otherMode/otherValue`。
    - 文件：`src/components/ClarificationCard.tsx`

12. **流式 token 每个都触发 Markdown 重渲染**
    - 原每个 token 都 `setStreamingContent(prev + text)`，长报告会持续重渲染整段 Markdown。
    - 已改为 80ms 合并刷新；新节点立即刷新，节点结束清空缓冲。
    - 文件：`src/lib/useResearchStream.ts`

### P1/P2 · 文案、样式与可读性

13. **活动时间线图标全部退回通用图标**
    - 原 `getEventIcon` 只识别英文关键词，而事件标题已是中文。
    - 已补中文关键词映射：生成计划、反思、网络研究、提纲/撰写/审稿。
    - 文件：`src/components/ActivityTimeline.tsx`

14. **活动时间线英文/调试文案**
    - `Searching...`、`No activity to display.`、`Timeline will update during processing.` 已改中文。
    - 文件：`src/components/ActivityTimeline.tsx`

15. **聊天回答中的链接被包成 Badge 卡片**
    - 与“正文内可点链接、不堆叠相关片段卡片”的偏好冲突。
    - 已去掉 Badge，改为正文内蓝色下划线链接。
    - 文件：`src/components/ResearchStreamChatView.tsx`、`src/components/ChatMessagesView.tsx`

16. **每个 AI 气泡都挂一条时间线**
    - 研究流里同一段过程会在多个 AI 消息下重复出现。
    - 已改为只在最后一条 AI 消息挂时间线。
    - 文件：`src/components/ResearchStreamChatView.tsx`

17. **研究流不自动跟随**
    - 长报告追加时用户看不到新内容。
    - 已加自动滚动：距底部小于 160px 才跟随，避免打断向上回看。
    - 文件：`src/components/ResearchStreamChatView.tsx`

18. **滚动条几乎不可见**
    - 原 Radix 滚动条 `opacity: 0.3` 叠加 thumb `rgb(115 115 115 / 0.2)`，有效透明度约 6%；实机上聊天页又没有稳定渲染出自定义 ScrollBar。
    - 已提高透明度/thumb 颜色，并为 `[data-radix-scroll-area-viewport]` 保留 8px 原生细滚动条。
    - 实机 609×883：`offsetWidth 609 / clientWidth 599`，滚动条槽位 10px。
    - 文件：`src/global.css`、`src/components/ui/scroll-area.tsx`

19. **“调研/研究”术语混用**
    - 历史列表、历史详情、阶段标签统一为“研究”；历史步骤 label 也在前端做了一次 `调研→研究` 归一。
    - 前端可见区域已无“调研”字样（实机 `document.body.innerText` 检查 `oldWord: false`）。
    - 文件：`src/components/HistoryList.tsx`、`src/components/HistoryDetailView.tsx`

20. **历史记录标题直接暴露内部计划文本**
    - 实机列表曾出现 `# 需求清晰度进度条: 25% ## 核心需求理解：...` 作为标题。
    - 已加前端兜底：标题以 `#` 开头或包含“需求清晰度进度条/核心需求理解”时，改用 `question`；`question` 同样异常时显示“未命名研究”。
    - 历史详情也做了同样的兜底，并且不再把内部计划文本放在“研究问题”标题下。
    - 文件：`src/components/HistoryList.tsx`、`src/components/HistoryDetailView.tsx`

21. **历史步骤预览横向裁切**
    - 收起态 `max-h-16 overflow-hidden` 的 `<p>` 对长 URL/中文长行会横向截断。
    - 已加 `break-words`；实机预览 `scrollWidth === clientWidth`，横向不再溢出。
    - 文件：`src/components/HistoryDetailView.tsx`

22. **步骤展开按钮缺 `aria-expanded`**
    - 已补 `aria-expanded={open}`；实机步骤按钮均返回 `false/true`。
    - 文件：`src/components/HistoryDetailView.tsx`

23. **低对比/小字号/小触控区**
    - 输入辅助文案从 11px 提到 12px，颜色 `neutral-400`；placeholder 从 `neutral-500` 提到 `neutral-400`。
    - 历史元信息“更新”从 `neutral-600` 提到 `neutral-500`。
    - 删除按钮从 32×32 提到 36×36。
    - 文件：`src/components/InputForm.tsx`、`src/components/HistoryList.tsx`

24. **页面标题与语言**
    - 浏览器标题从“你的行业AI专家-智泊AI-DeepResearch”改为“DeepResearch · 研究计划与报告”，`lang` 改为 `zh-CN`，补了 description。
    - 文件：`frontend/index.html`

25. **欢迎页与主流程文案**
    - H1：`从一个问题，到一份有据可查的研究报告。`
    - 副标题：`输入研究问题，选择研究深度。完整研究先确认计划，再执行检索、写作与评审；仅搜索直接作答。每一步和来源都会保留在历史记录中。`
    - placeholder：`例如：分析国内 AI 芯片产业链的关键瓶颈与竞争变量`
    - 按钮：`开始研究`；计划按钮：`确认计划`；历史：`历史研究`
    - 文件：`src/components/WelcomeScreen.tsx`、`InputForm.tsx`、`ResearchStreamChatView.tsx`、`ChatMessagesView.tsx`、`HistoryList.tsx`、`HistoryDetailView.tsx`

### 性能与构建

26. **单 chunk 首屏包**
    - 旧产物 `index-MKfxpepH.js` 约 664KB / gzip 207KB。
    - 已加 `manualChunks`，拆成：
      - `index` 462.42KB / gzip 146.59KB
      - `markdown` 157.76KB / gzip 47.94KB
      - `langgraph` 33.09KB / gzip 9.30KB
      - `react` 11.83KB / gzip 4.20KB
      - CSS 50.28KB / gzip 9.24KB
    - Vite 不再输出 >500KB 告警。
    - 文件：`frontend/vite.config.ts`

27. **开发代理端口与宿主机不一致**
    - 原 `/api`、`/threads`、`/runs`、`/assistants` 指向 `127.0.0.1:2024`，宿主机后端实际是 12409。
    - 已改为默认 `12409`，支持 `VITE_API_PROXY_TARGET` 覆盖。
    - 文件：`frontend/vite.config.ts`

## 二、未修 / 剩余风险

| 级别 | 问题 | 判断 | 建议 |
|---|---|---|---|
| P2 | `HistoryList` 折叠时仍请求 `/api/history` | 数据量小可接受，但折叠状态仍打数据库 | 首次展开再请求，或缓存上一次结果 |
| P2 | `BrowserRouter` 没有 `basename="/app"` | 当前 Nginx 把 `/login` 指到 SPA、`/app/` 走 `try_files`，可用 | 统一路由约定，避免以后加子路由时 404 |
| P2 | `AuthContext` 的 `whoami()` 内部 catch 返回 `logged_in:false` | 后端短暂不可用会被当成未登录，用户被送到登录页 | 区分网络失败与未登录，网络失败给出重试 |
| P2 | 路由未懒加载 | 首屏仍加载所有页面组件，主包 462KB | 历史详情、登录页可 `React.lazy` 拆分 |
| P2 | 流式阶段仍整段 `ReactMarkdown` | 已从每 token 降到 80ms，但长报告仍有重渲染成本 | 流式阶段先纯文本，完成后切 Markdown |
| P3 | README 的 `docs/assets/welcome.png` 仍是旧文案截图 | 页面已改，截图未替换 | 有稳定截图环境后替换 |
| P3 | 后端仍可能生成异常标题/步骤 label | 前端已兜底“未命名研究”和术语归一，但根因在后端 | 后端标题生成限制长度，步骤 label 统一“研究” |

## 三、实机验证记录

验证环境：`http://localhost:12309/app/`，609×883。构建使用 `docker compose build frontend`，`tsc -b && vite build` 通过。

| 项 | 观察结果 |
|---|---|
| 欢迎页 H1 | 36px，2 行，高 80px；旧版为 3 行 |
| 输入框焦点 | `outline-style: solid`、`outline-width: 1px`、蓝色 alpha 0.6 |
| 多行输入 | 3 行输入后按钮底部 328、容器底部 332，间距 4px |
| 普通 Enter | 用 stub fetch 拦截请求后按 Enter，输入被清空并进入会话视图，证明提交生效 |
| Shift+Enter | `defaultPrevented: false`，页面不进入会话视图，不误提交 |
| 历史列表 | 20 条；阶段显示“研究完成/等待确认计划”；无“调研”字样 |
| 历史异常标题 | 列表不再出现 `# 需求清晰度进度条...`，显示“未命名研究” |
| 历史详情 | “研究问题”不再承载内部计划文本；步骤预览 `scrollWidth === clientWidth` |
| 步骤按钮 | `aria-expanded` 正常返回 `false` |
| 计划确认页 | 按钮为“确认计划”，placeholder 为“补充研究范围，或输入‘确认计划’开始执行” |
| 聊天滚动条 | `offsetWidth 609 / clientWidth 599`，10px 滚动条槽位可见 |
| 构建产物 | 主包 462.42KB / gzip 146.59KB，无 >500KB 告警 |

未验证范围：

- 未做真实断网/后端 404 的故障注入，SSE 重连与 `watchTask` 连续失败终止属于代码层修复。
- 未跑完整长任务回归来观察时间线去重，只确认游标逻辑和旧截图中的重复现象。
- `npm run lint` 仍无法在宿主机执行，`frontend/node_modules` 不存在；以 Docker 内 `tsc -b` + Vite build 作为类型与构建校验。
- BU 浏览器会话的截图接口返回 `page viewport unavailable`，本轮视觉判断基于实机 DOM/计算样式和既有截图，没有新增全页截图。

## 四、建议的下一步

1. 后端生成历史标题时限制长度，禁止把计划/澄清正文写入 `title`；前端兜底只作为防线。
2. 把 `research_steps.label` 的“调研”统一为“研究”，避免前端每个展示点都做字符串替换。
3. 给 `HistoryList` 加“展开后再请求”，减少欢迎页空转请求。
4. 若要继续压首屏，优先懒加载 `HistoryDetailView` 和 `LoginPage`，而不是继续拆 React 运行时。
5. 加一条真实断网回归：研究进行中切网 10 秒，确认 SSE 自动重连后事件不重复、状态能恢复。
