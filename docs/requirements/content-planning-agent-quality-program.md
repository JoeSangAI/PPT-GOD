# 内容规划 Agent 质量工程

## 目标

内容规划 Agent 要稳定接住不同长度、不同结构、不同意图的素材，并输出可直接进入视觉阶段的高质量 PPT 文案。

核心质量要求：

- 能识别用户真实任务和交付场景，并用开放的交付理解承接，而不是靠固定类型枚举。
- 能根据素材体量决定合理页数，不误把长材料压成薄摘要，也不把短材料灌水成长 deck。
- 能保留用户要求保留的原文结构、关键章节、金句、事实、数据和结尾。
- 能避免模板化标题、空正文、占位符、重复页、Markdown/source marker 泄漏。
- 长时间生成时状态稳定，不因模型长思考被误判中断。

## Agent Team 审计分工

- 源材料覆盖审计：检查原文结构、尾部章节、中文金句、source draft 和 page map 的覆盖关系。
- 意图路由审计：检查 content director contract 到 planning policy 的信息损耗。
- 运行状态审计：检查 content_plan run 的创建、进度、stale、取消、重试、晚写入风险。
- 文案质量审计：建立可复跑的质量维度和 benchmark case。

## 已落地

- 内容规划运行心跳从通用 300 秒扩展为 `content_plan` 专用 1800 秒，避免长文规划被误判 stale。
- source-preserve page map 在模型漏掉原文尾部或结构锚点时，会用 source draft 补齐，不再二次整段重试。
- 中文金句和高信号中文判断句纳入 source fact 检测，模型改写导致丢失时会恢复 source body。
- 非固定关键词的结尾页纳入尾部覆盖检测，例如“复盘与下一步：从今天开始的三件事”。
- 新增 `content_plan_quality` 结构化质量报告，可同时返回多类问题，而不是只在单点 raise。
- 新增 case-based benchmark 接口，支持必含锚点、必含金句、禁止新增内容等检查。
- source draft 的演讲备注改为讲法、证据和转场结构，不再批量输出“这一页口头展开”式正文复述。
- 运行时 page map 门禁会拒绝明确占位备注和正文复读型备注，但不把交付场景拆成固定 genre。
- source_capacity 不再被 5000 字硬阈值挡住；结构清楚的短材料也会按章节容量展开。
- “不超过 N 页 / 最多 N 页”被当作上限，而不是默认把目标页数做满 N 页。
- 最终保存前会拒绝普通模型页的薄正文，避免一页只有一条泛泛要点的低质量结果进入画布。
- content_plan 写回会校验当前 run 是否仍是项目最新 active run，避免旧后台任务晚写入覆盖新结果。
- content_plan 取消/失效后的异常分支不再改写项目状态。
- draft 阶段不再要求模型输出 `reading / presentation / mixed` 这类固定场景枚举，改为自然语言 `delivery_intent`。
- 项目更新保存 intent contract 时会保留 content director 的 `delivery_intent`、coverage、compression、page_budget_policy 等字段，不再折叠丢失。
- 内容总监模型漏填 `delivery_intent` 时，会从用户原始需求生成一条自然语言交付理解，保证开放意图字段不会空传。
- 新增长素材、短素材、同源不同交付意图的 benchmark fixtures，防止能力只对单一案例有效。
- 质量评估新增重复 bullet 标签检测，防止页面看似完整但每页都是同一套“背景/洞察/行动”模板。
- 前端 content_plan 长时间后台生成后，会释放本地 run ownership，允许后台终态继续更新当前项目提示。

## 当前测试资产

- `backend/tests/test_content_plan_quality.py`
  - 检测空正文、重复标题、占位符、内联页码、模板化/复述型备注、source-preserve 覆盖缺失。
  - 检测 benchmark case 的 required anchors、gold sentences、forbidden terms。

- `backend/tests/test_content_plan_policy.py`
  - 覆盖 page map 路由、长材料 source draft、source-preserve 修复、中文金句恢复、非关键词结尾覆盖。
  - 覆盖结构化短材料 source_capacity、页数上限语义、薄正文保存门禁、开放 delivery_intent prompt。

- `backend/tests/test_content_plan_benchmarks.py`
  - 覆盖长讲稿 source_capacity 不截断中尾部结构和金句。
  - 覆盖短素材保持紧凑但每页仍有有效正文。
  - 覆盖同一份材料在不同自然语言交付意图下产生不同预算策略，且不引入 genre 分支。

- `backend/tests/test_chat_source_intent.py`
  - 覆盖 draft prompt 使用开放 `delivery_intent`，并阻止 `scene_type` 或固定场景枚举回流。

- `backend/tests/test_project_intent_contract.py`
  - 覆盖项目更新时保留 content director contract 的开放字段。

- `frontend/src/project-isolation.test.mjs`
  - 覆盖 content_plan 进入后台长跑后不再提示刷新，不再吞掉后续后台终态。

- `backend/tests/test_run_state.py`
  - 覆盖内容规划长心跳和普通 run 300 秒心跳边界。
  - 覆盖取消 run、被新 run 取代的旧 run、取消后异常分支的写回保护。

## 下一批优先级

1. 保留完整 director contract 语义
   - 当前 rich contract 会部分折叠到 legacy policy。
   - 需要让 `coverage`、`compression`、`depth`、`delivery_intent`、`page_budget_policy` 更直接影响 page map prompt 和 source policy。

2. 强化开放式交付理解
   - 当前 contract 仍保留少量历史字段，但生成阶段应优先继承 `delivery_intent`。
   - 新测试应继续证明复杂交付目标通过自然语言理解传递，而不是新增固定类型矩阵。

3. 强化 source structure 覆盖
   - 目前 source draft 的结构锚点更强，上传文档 heading 的直接覆盖还可以更强。
   - 需要覆盖 source draft condense 后丢掉中段 heading 的场景。

4. 运行状态鲁棒性
   - 增加重复点击并发保护。
   - 让 `/generation-progress` 在 terminal 状态下也保留 last_run 错误信息。

5. 建立更完整 benchmark fixture matrix
   - short prompt no docs
   - long markdown manuscript
   - PPT-like source
   - ambiguous user intent
   - preserve-source long-form talk
   - synthesize decision brief
   - minimal material

## 质量门槛建议

- 默认所有内容规划相关改动必须跑：
  - `backend/tests/test_content_plan_quality.py`
  - `backend/tests/test_content_plan_policy.py`
  - `backend/tests/test_source_intent.py`
  - `backend/tests/test_chat_source_intent.py`
  - `backend/tests/test_content_director.py`
- `backend/tests/test_run_state.py`
- `backend/tests/test_content_plan_benchmarks.py`
- `backend/tests/test_project_intent_contract.py`

- 对真实大材料回归，先用非 LLM benchmark 检查保存结果，再用线上模型做人工抽检。
