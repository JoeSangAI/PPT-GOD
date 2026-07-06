# Sandun 竞品功能研究与 PPT God 借鉴建议

调研时间：2026-06-06

调研对象：
- Sandun replay: `https://sandun.cc/replay/e551a98cc7d9f7fe2478493a48773d8966b7bb55a63049b9cb26249dcd9a49fa`
- Sandun live home: `https://sandun.cc/`

本次没有登录 Sandun，也没有消耗新的生成积分。证据主要来自公开 replay 页面、公开 replay JSON、公开前端 bundle、以及 PPT God 当前代码和产品文档。

## 一句话结论

Sandun 最值得 PPT God 借鉴的不是“用 SVG 做 PPT”这条技术路线，而是它把复杂生成流程拆成了用户能理解、能确认、能回放、能局部修正的工作台体验。

对 PPT God 最有价值、技术路线也最匹配的功能是：

1. 画布框选 + 指令微调：把“我要改哪里”从自然语言歧义变成可视化选区。
2. 可审计生成时间线/回放：把 agent 的中间产物、成本、进度、失败原因沉淀下来。
3. 生成前需求合同：先让 agent 基于调研生成可修改的默认需求单，再开始大纲和内容规划。
4. 每页证据层：把搜索关键词、来源摘要、图表/图片计划和页面绑定。
5. 先打样/锚点页再批量生成，并在批量前说明成本和剩余页数。

不建议照搬 Sandun 的完整 Fabric/SVG 编辑器、动画编辑器和 SVG-first 出稿链路。它们很强，但和 PPT God 当前“整页图像生成为主、可编辑 PPTX 为派生产物”的架构方向冲突，容易把产品拉成重前端编辑器。

## Sandun 观察到的核心功能

### 1. 生成前需求单

Sandun 在输入“北京5日游攻略”后先做背景调研，然后给用户一个“内容需求单”。该需求单包括：

- 内容页页数：5-10 页、10-15 页、15-20 页、自由发挥。
- 使用场景：工作汇报、客户提案、培训分享、路演答辩、日常检索、自定义。
- 图片策略：智能匹配、仅 AI 图片、仅可商用图库、不添加图片。
- 免费补充文本。

replay 里这个需求单带有 `research_context`，也就是 agent 先调研再给默认选项。用户未互动时它会自动提交默认答案。

产品价值：
- 让用户在高成本生成前确认方向。
- 避免一开始问太多问题，因为默认值来自前置调研。
- 把“需求理解”变成可见合同，而不是隐藏 prompt。

对 PPT God 的适配：
- 高适配。PPT God 已有 `intent_contract`、`source_intent`、content director 原则。
- 不应该做成长表单。建议做成“Agent 已理解的任务合同”，只露出 3-5 个会影响结果的决策。

### 2. 可审计的生成时间线和只读回放

公开 replay JSON 有 351 条 timeline 项，其中包括：

- `phase_start` / `phase_transition` / `phase_complete`
- `step_status_update`
- `task_progress`
- `artifact_updated`
- `search_page_progress`
- `cost_update`
- `style_selected`
- `nano_anchor_preview_ready`

Sandun 还支持“任务回放共享”，开启后任何人可通过只读链接查看回放。

产品价值：
- 用户知道系统真的做了什么，而不是只看到“生成中”。
- 失败、成本、阶段卡顿都能被复盘。
- 对团队内部 debug、用户反馈、客服都非常有价值。

对 PPT God 的适配：
- 高适配。PPT God 已有 `ProjectRun`、SSE、run state、质量报告，但事件沉淀和可分享回放还不完整。
- 建议把当前运行状态扩展为 durable event ledger，不要只存在 Redis/SSE 临时状态里。

### 3. 每页绑定搜索证据和内容策划

Sandun 会为每页生成搜索关键词，并在 replay 里保存 `searchResults`：

- 住宿页：`北京 东城区 西城区 住宿 靠近地铁站 枢纽`
- 抢票页：`故宫 国家博物馆 2026 预约放票时间 小程序`
- 地铁页：`2026 北京地铁线路图 乘车码 机场线 攻略`

每个搜索任务通常有 15 条结果。之后每页会生成 `slidePlans`，包含：

- Core Goal
- Narrative Model
- Content Modules
- Image plans
- Chart plans

产品价值：
- 用户能看到页面为什么这样写。
- 对事实型 PPT 特别重要，降低幻觉感。
- 每页内容策划比整套大纲更可执行。

对 PPT God 的适配：
- 中高适配。PPT God 已有 source context、search service、content plan、visual plan。
- 建议补“每页证据面板”，而不是把搜索结果塞进聊天。

### 4. 初稿 SVG 和正式设计图双阶段

Sandun 的 `draftSlides` 是 SVG，生成较快，视觉上像蓝白草稿。正式设计图再走 Nano/GPT Image2，生成红金国风整页图。

这个 replay 里：

- 内容策划结束时成本约 130。
- 初稿 SVG 结束时成本约 228。
- 正式图第一张锚点页单页成本 30。
- 批量正式图最终项目成本 618。

产品价值：
- 初稿能快速让用户看到结构和信息密度。
- 正式图之前可以先看锚点页。
- 初稿/设计稿双层让用户不必盲等最终图。

对 PPT God 的适配：
- 部分适配。PPT God 不应改成默认 SVG-first，否则冲突于“整页图像生成”的架构约束。
- 可借鉴为“视觉方案预览/低成本结构草图”，但不能让草图成为最终输出的主路径。

### 5. 锚点页预览和成本披露

Sandun 有 `nano_anchor_preview_ready` 事件：

- 先生成第 1 个锚点页。
- 显示已扣成本、单页成本、剩余页数、预计总成本。
- 用户确认后再生成剩余 12 页。

产品价值：
- 高成本批量生成前，用户有一次低风险检查。
- 成本透明，用户更容易接受等待。

对 PPT God 的适配：
- 高适配。PPT God 已有 prototype/seed page 概念和打样流程。
- 建议强化“打样是锚点页确认”这层用户语言，并加入成本/页数估算。

### 6. 框选编辑、元素 AI 修改、图片裁剪

Sandun 前端 bundle 显示它有：

- `框选编辑` 按钮，说明文案是“在画布上框选要修改的区域”。
- 富文本输入里的 `[框选1]` token/chip。
- `/api/ai/edit-slide-element`，用于选中元素后 AI 修改。
- 图片裁剪弹窗：“用可视化裁剪框直接定义最终保留区域。确认前只修改临时草稿，不会立即写回页面。”
- 选中 shape/chart/page 后输入指令修改。

产品价值：
- 解决“改这里”这类自然语言定位难题。
- 用户不用知道元素 ID、prompt、坐标。
- 比单纯“重生成第 X 页”更符合 PPT 修改直觉。

对 PPT God 的适配：
- 框选微调高适配，完整元素编辑器低适配。
- PPT God 当前已经有单页 `finetuneSlide`，但缺少可视化选区。最优路径是给现有 image-first 微调加 region metadata，而不是引入完整 Fabric 编辑器。

### 7. 用户风格库

Sandun 有“用户风格库”：

- 上传参考图或描述需求。
- AI 生成完整设计规范并导入。
- 保存后使用时不再走 AI。

产品价值：
- 对重复使用品牌风格的用户很有价值。
- 降低每次生成风格的成本和不稳定性。

对 PPT God 的适配：
- 中适配。PPT God 已有 style proposal、selected style、style pack。
- 建议作为 P2，不应早于框选微调和事件回放。

### 8. 动画、放映、SVG/PNG/PDF/PPTX 导出

Sandun 有动画配置、放映、当前页 SVG/PNG、整套 PDF/PPTX 导出。它更像“在线 PPT 设计编辑器”。

对 PPT God 的适配：
- 低到中。PPT God 可以保留下载 PPTX / 可编辑 PPTX，但不应优先做动画编辑器。
- 动画会显著增加前端编辑器复杂度，与当前核心质量问题关系不大。

## PPT God 当前能力对照

PPT God 当前已经具备：

- Agent-driven 流程：content / visual / finetune 角色。
- Content Plan、Visual Plan、Prompt、整页图像生成、PPTX 组装。
- Prototype/seed page 方向。
- 视觉素材面板、参考图/Logo/模板处理方向。
- 单页微调：`POST /projects/{project_id}/slides/{slide_id}/finetune`。
- 版本历史：微调前自动归档当前图片。
- 可编辑 PPTX 派生导出：标准/增强/激进解析模式。

关键缺口：

- 没有可视化区域选择，微调靠用户语言描述。
- 运行事件主要是状态，不是完整可回放证据链。
- 生成前任务合同没有像 Sandun 一样可见、可改、可默认推进。
- 每页来源/搜索/证据不够产品化。
- 打样成本、剩余页数、锚点页确认的表达还可以更强。

## 推荐借鉴清单

### P0: 区域框选微调

目标：把当前单页微调从“用户描述区域”升级为“用户框选区域 + 指令”。

建议 MVP：

- 在生成图预览上加框选模式。
- 用户拖出矩形，生成一个 `region-1`。
- 输入框上方显示 chip：`框选 1`。
- 发送时传给后端：
  - `region_id`
  - normalized bbox: `{x, y, width, height}`
  - optional crop image/mask
  - user instruction
- 后端 `FinetuneRequest` 增加 `regions` 字段。
- `_build_direct_finetune_prompt` 明确写入：
  - 用户选中的区域位置
  - 只修改该区域
  - 未选区域保持不变
- 仍然走现有 image edit 生成链路和版本历史。

为什么适合 PPT God：
- 不破坏 image-first。
- 能复用现有 `finetuneSlide`、版本历史、单页生成 run。
- 是用户已经指出的高价值功能。

暂不建议：
- 不要一开始做 SVG 元素级编辑。
- 不要引入 Fabric 作为主编辑器。
- 不要让前端负责理解业务语义，只负责传区域。

### P0: Durable event ledger 和 replay/share

目标：把生成过程变成可回放、可诊断的证据链。

建议 MVP：

- 新增 `ProjectEvent` 或项目级 JSON event log。
- 记录：
  - 用户消息
  - 阶段开始/完成
  - 每页状态
  - 生成/重试/微调 run
  - 错误原因
  - 关键中间产物引用
- 提供只读 replay token。
- replay 页先不必完整复刻编辑器，只需要：
  - 左侧/中间 slide preview
  - 右侧 timeline
  - 点击事件可定位到页

为什么适合 PPT God：
- 与现有 run state/SSE 自然衔接。
- 对真实测试、客户反馈和 debug 的价值非常大。
- 能体现 agent 做了哪些工作，符合 agent-driven 产品定位。

### P1: 生成前任务合同卡

目标：在进入内容规划前，让用户确认关键决策。

建议 MVP：

- 由 content director 生成 `intent_contract` 后，在主界面显示一张“任务理解”卡。
- 只包含真正影响输出的 3-5 项：
  - 页数/篇幅
  - 使用场景
  - 受众
  - 来源覆盖策略
  - 图片/图表策略
- 默认值来自用户输入和上传材料。
- 用户可直接继续，也可补充。

为什么适合 PPT God：
- 与 AGENTS.md 的 “task contract” 原则一致。
- 可以降低后续“页数不对、内容跑偏”的概率。

### P1: 每页证据与素材计划面板

目标：用户能看到每页内容/图片/图表为什么这样生成。

建议 MVP：

- 在单页视图增加“证据/计划”折叠面板：
  - 页面目标
  - 使用的 source pack / search result
  - 图表计划
  - 图片计划
  - 参考图绑定
- 如果没有联网搜索，也显示“来自上传文档第 X 页/段落”的来源。

为什么适合 PPT God：
- PPT God 已经强调来源驱动和质量 gate。
- 比展示 prompt 更符合用户心智。

### P1: 锚点页确认与成本/范围披露

目标：批量生成前让用户确认视觉锚点，并知道本次会生成多少页。

建议 MVP：

- 将 prototype/seed page 改成用户语言：“先打样 1-4 张关键页”。
- 打样完成后展示：
  - 已生成页
  - 待生成页
  - 是否会使用打样页作为参考
  - 预计耗时/调用数量/成本估算（如果能估）
- 用户确认后再全量生成。

为什么适合 PPT God：
- 已有 prototype 流程，主要是产品表达和状态记录增强。

### P2: 用户风格库

目标：让用户保存并复用风格方案。

建议 MVP：

- 保存 selected style 为 user preset。
- preset 含风格名、关键词、色板、字体气质、参考图摘要。
- 下次项目可选择，不必重新提案。

为什么适合 PPT God：
- 与 style pack 和 selected style 兼容。
- 但优先级低于区域微调和回放。

## 不建议优先借鉴的能力

### 完整 SVG/Fabric 编辑器

Sandun 的前端是强画布编辑器路线。它支持选中元素、修改 shape/chart、导出 SVG/PNG、AI 修复 SVG、动画等。

PPT God 不应优先照搬：
- 会把前端从“展示和触发”变成重业务编辑器。
- 会和 image-first 生成主路径冲突。
- 维护成本高，且容易把质量问题转移成编辑器问题。

可以借鉴局部概念：
- 只借“框选区域 + 指令”。
- 不借完整元素树、Fabric 控制点、SVG path 编辑。

### 动画编辑器

动画对在线展示有价值，但对 PPT God 当前核心目标不是 P0。

原因：
- PPT God 当前最关键是内容质量、视觉一致性、可控修改和导出稳定性。
- 动画会引入 PowerPoint 兼容和播放语义复杂度。

### 默认 SVG 草稿作为主生成路径

Sandun 的 SVG 草稿很有用，但 PPT God 的方向是整页图像生成。

可借鉴：
- 低成本结构预览。
- 页面信息密度/布局意图可视化。

不建议：
- 把所有页面先 deterministic SVG 排版，再统一图像美化。
- 默认程序化叠文字作为最终页。

## 推荐路线图

### 第一阶段：把“修改哪里”做清楚

交付：
- 单页预览框选。
- 区域 chip。
- `finetuneSlide` 支持 regions。
- 微调版本历史继续可回退。

成功标准：
- 用户能框选标题、图片、局部区域并发指令。
- 未框选区域明显更稳定。
- 微调失败时错误原因可见。

### 第二阶段：把“系统做了什么”做清楚

交付：
- project event ledger。
- 生成过程 timeline。
- 每页状态和中间产物记录。
- 只读 replay link。

成功标准：
- 任一项目可回放生成过程。
- 能从 replay 看出卡在哪一步、花了多少、失败原因是什么。

### 第三阶段：把“生成前合同”和“每页证据”做清楚

交付：
- 任务理解卡。
- 每页证据/计划面板。
- 打样/批量前成本和页数披露。

成功标准：
- 用户在生成前能修改关键决策。
- 用户能追溯页面事实依据和素材依据。

### 第四阶段：复用资产和风格

交付：
- 用户风格库。
- 参考图/模板/Logo 的可复用 preset。

成功标准：
- 同一客户/品牌项目不必每次重新定义风格。

## 最终建议排序

| 优先级 | 功能 | 借鉴价值 | 技术适配 | 建议 |
|---|---|---:|---:|---|
| P0 | 框选区域微调 | 很高 | 高 | 立即进入方案设计 |
| P0 | 可审计 timeline/replay | 很高 | 高 | 和 run state/SSE 合并设计 |
| P1 | 生成前任务合同 | 高 | 高 | 基于 `intent_contract` 做轻量 UI |
| P1 | 每页证据/计划面板 | 高 | 中高 | 先做折叠面板，不打断主流程 |
| P1 | 锚点页确认 + 成本披露 | 高 | 高 | 强化现有 prototype |
| P2 | 用户风格库 | 中 | 中高 | 适合稳定后做 |
| P3 | SVG 草稿/结构预览 | 中 | 中 | 只做预览，不做主路径 |
| 不建议 | 完整 Fabric 编辑器 | 高但偏离 | 低 | 不作为当前路线 |
| 不建议 | 动画编辑器 | 中 | 低 | 暂缓 |

