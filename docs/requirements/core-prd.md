# PPT GOD — 核心决策 PRD

> 本 PRD 记录从 Nano Banana PPT 重写过程中确定的**不可变架构决策**。
> 实现细节（API 路径、数据库字段、组件 props）不在此文档中定义，开发时自然确定。

---

## 1. 项目概述

PPT GOD 是 Nano Banana PPT 的完整重写版本，从 Python CLI 升级为全栈 Web 应用（FastAPI + React）。

**核心目标**：
- 保留 main 分支验证有效的"简洁哲学"
- 用现代工程化包装（数据库、前端 UI、API、任务队列）
- 抛弃 v4 分支的过度工程化（多层 Gate、文件驱动计划、代码硬拼 Prompt）

---

## 2. 架构总览

### 技术栈
| 层级 | 技术 |
|------|------|
| 前端 | React 18 + Vite + Tailwind CSS |
| 后端 | FastAPI + SQLAlchemy + Pydantic |
| 数据库 | PostgreSQL |
| 任务队列 | Celery + Redis |
| 实时推送 | Server-Sent Events (SSE) |
| 部署 | Docker Compose（本地开发） |
| LLM | MiniMax（LLM）+ DeerAPI（图像生成） |

### 核心流程
```
用户输入（文档/大纲/主题）
    ↓
Content Agent → Content Plan（JSON 存数据库）
    ↓
Visual Agent → Visual Plan Intent（JSON 存数据库）
    ↓
Prompt Engine 组装 Rich Brief → 调用 LLM → Final Image Prompt
    ↓
Executor（Celery 任务）→ 调用 DeerAPI/MiniMax 生图
    ↓
Assembler → 图片 + 原生图片 + Logo → PPTX
    ↓
用户下载
```

---

## 3. 模块职责边界（不可变）

### 3.1 前端
- **只做展示和触发**，不做业务逻辑判断
- 三栏布局：左（缩略图导航）| 中（主预览）| 右（Agent 聊天）
- 每页以卡片形式预览 Content Plan / Visual Plan
- 支持"查看 Prompt"Modal（只读，预留编辑扩展）
- 项目流状态由后端驱动，前端只轮询/接收 SSE

### 3.2 Content Agent（后端 Service）
- **职责**：将用户输入（文档/大纲/主题）转化为 Content Plan
- **输入**：用户上传的文件（PDF/DOCX/TXT）或文本大纲或主题描述
- **输出**：Content Plan JSON（每页的 page_num, type, text_content, section_title）
- **参考旧代码**：`agents/narrative.py` 的核心逻辑（大纲解析、分页、Speaker Notes 分离）
- **不做什么**：不决定视觉风格、不写生图 Prompt

### 3.3 Visual Agent（后端 Service）
- **职责**：将 Content Plan 转化为 Visual Plan Intent
- **输入**：Content Plan JSON + style_id + reference_images
- **输出**：Visual Plan Intent JSON（每页的 layout, visual_suggestion, design_notes, reference_image_ids）
- **核心机制**：参考 main 分支的 `generate_visual_plan`，让 LLM 根据 brief 生成每页的 visual_description
- **不做什么**：不写最终生图 Prompt、不调用生图 API

### 3.4 Prompt Engine（后端 Service）
- **职责**：将 Visual Plan Intent 翻译为最终生图 Prompt
- **核心机制**：
  1. 读取 Style Markdown 模板（色彩/质感/氛围）
  2. 读取 Layout Markdown 模板（构图约束）
  3. 注入 Content（标题/正文/数据）
  4. 注入 Reference Image 说明（逐张声明用途）
  5. 组装成 **Rich Brief**（给 LLM 的详细指令，不是最终 Prompt）
  6. **调用 LLM** 将 Rich Brief 翻译成自然流畅的 Final Image Prompt
- **语言策略**：技术术语英文，业务内容中文
- **分层策略**：Style / Layout / Content / References 严格不重叠
- **负面约束**：极简，只保留核心 1-2 条（不要生成 logo、16:9 比例）
- **输出**：Final Image Prompt（字符串，每页独立、自包含）
- **可查看性**：每页 Final Prompt 保存到数据库，前端可调取展示

### 3.5 Image Generation Service（后端 Service）
- **职责**：调用 DeerAPI/MiniMax 生成单页图片
- **输入**：Final Prompt + reference_images（PIL Image 列表）+ 技术参数（aspect_ratio, resolution）
- **输出**：PIL Image（16:9，已裁剪）
- **参考旧代码**：`core/generator.py` 的 API 调用逻辑（HTTP 请求、base64 解码、错误处理）

### 3.6 Chart Generator（后端 Service）
- **职责**：用 Matplotlib 生成数据图表，截图作为参考图
- **Route 2 实现**：数据 → Matplotlib 渲染 → 截图 → 作为 reference_image 传给生图模型
- **参考旧代码**：`core/data_visualizer.py` 的核心算法

### 3.7 PPTX Assembler（后端 Service）
- **职责**：将生成的图片组装为最终 PPTX
- **功能**：
  - 按页序插入图片
  - 叠加 Logo（如果用户上传了）
  - 写入 Speaker Notes
  - 不处理原生图片的智能排版（已取消此功能）
- **参考旧代码**：`modules/pptx_assembler.py` 的核心逻辑

### 3.8 Template Clone Service（后端 Service）
- **职责**：解析用户上传的模板文件（PDF/PPTX/图片）
- **简化逻辑**：
  - 提取配色方案
  - 提取整体风格气质（作为文字描述）
  - 提取模板图作为 reference_image
  - **不提取 Logo**
  - **不分析字体**

### 3.9 Celery Tasks（后端任务队列）
- **职责**：管理生成流水线的异步执行
- **任务拆分**：按页拆分，每页一个 Celery Task
- **状态机**：pending → generating → completed / failed
- **恢复机制**：项目级别状态保存到数据库，关闭页面后可恢复
- **重试机制**：指数退避智能重试（429/503/连接错误）
- **SSE 推送**：状态变更时推送到前端

---

## 4. 数据库核心实体（不可变）

| 实体 | 核心字段 | 说明 |
|------|----------|------|
| **Project** | id, title, status, style_id, created_at | 项目级别状态机 |
| **Slide** | id, project_id, page_num, type, status, error_msg, content_json, visual_json, prompt_text, image_path | 每页完整状态 |
| **Template** | id, name, category, palette, style_description | 系统预置风格模板元数据 |
| **ReferenceImage** | id, project_id, file_path, role(style_ref/content_ref/chart_ref) | 参考图资产 |
| **PromptLog** | id, slide_id, rich_brief, final_prompt, model_used | 调试用，记录每页输入输出 |

---

## 5. Prompt Engine 核心机制（不可变）

### 5.1 输入：Visual Plan Intent
半结构化 JSON + `design_notes` 自由文本字段：
```json
{
  "page_type": "content",
  "style_id": "minimal_blue",
  "layout": "left_text_right_visual",
  "content": {"headline": "...", "body": [...]},
  "visual_suggestion": "科技感的蓝色背景...",
  "design_notes": "这页要有呼吸感，右侧留出大片空白",
  "reference_image_ids": ["img1", "img2"]
}
```

### 5.2 模板系统
- **Style 模板**：每个风格一个 Markdown 文件（`templates/styles/{style_id}.md`）
  - 包含：色彩、质感、氛围、艺术方向
  - 中英双语：英文正文（发给模型）+ 中文注释（给你维护）
- **Layout 模板**：每个页面类型一个 Markdown 文件（`templates/layouts/{layout_type}.md`）
  - 包含：构图约束、元素位置、图文比例
  - 中英双语

### 5.3 分层原则（严格不重叠）
| 层级 | 负责 | 不负责 |
|------|------|--------|
| Style | 色彩、质感、氛围、艺术风格 | 构图、元素位置 |
| Layout | 构图、元素位置、图文比例 | 色彩、内容文字 |
| Content | 标题、正文、数据、表格 | 风格、构图 |
| References | 参考图用途说明 | 风格描述、构图描述 |

### 5.4 输出：Rich Brief → LLM → Final Prompt
**不是代码拼接，是 LLM 翻译。**

Prompt Engine 组装的 Rich Brief 示例：
```
You are an expert Prompt Engineer for image generation.
Generate a natural, fluent image generation prompt for a presentation slide.

【Design System】
Style: Minimalist business, blue-white palette, generous whitespace
Layout: Left 60% text, right 40% visual

【Content】
Headline: 2024 Annual Summary
Body: Revenue +35%, Users 1M+

【References】
Image 1: Style template - extract color mood and composition style

Requirements:
- 16:9 aspect ratio
- Text readability is highest priority
- Do not generate logos
```

LLM 输出 Final Prompt（自然语言，流畅，无 `【】` 标记）。

### 5.5 负面约束策略
**极简，只保留核心：**
- "Do not render any logo or brand mark"
- "Image must be 16:9 landscape"

**废弃 v4 的完整负面约束列表**（不要黑块、不要重复文字、不要齿轮灯泡等）。

---

## 6. 功能映射：保留 vs 废弃

### 保留并改造
| 旧模块 | 新模块 | 策略 |
|--------|--------|------|
| `agents/narrative.py` | `services/content_plan.py` | 重写代码，保留核心逻辑 |
| `agents/visual.py` | `services/visual_plan.py` | 重写，只输出 intent，不写 final prompt |
| `core/generator.py` | `services/image_generation.py` | 保留 API 调用逻辑，prompt 输入改为从 Prompt Engine 接收 |
| `core/data_visualizer.py` | `services/chart_generator.py` | 复用 Matplotlib 核心算法 |
| `modules/pptx_assembler.py` | `services/pptx_assembler.py` | 重写，简化原生图片排版 |
| `agents/template.py` | `services/template_clone.py` | 重写，大幅简化 |
| `utils/llm_client.py` | `core/llm_client.py` | 保留 fallback 逻辑 |
| `styles/` (38 个 md) | `templates/styles/` | 改造为双语注释 |

### 废弃（不新建对应模块）
| 旧模块 | 废弃原因 |
|--------|----------|
| `core/image_selector.py` | 取消自动提取/智能选择图片 |
| `modules/style_generator.py` | 取消 AI 实时铸模风格 |
| `utils/image_assets.py`（VLM 部分） | 取消图片安检、语义分析 |
| `utils/doc_normalizer.py` | 不再文件驱动 |
| `utils/plan_sync.py` | 不再 md ↔ json 双向同步 |
| `prompts/registry.py`（旧形式） | Prompt 改为 Markdown 模板驱动 |
| `core/failure_classifier.py` | 简化为成功/失败两种状态 |
| `modules/page_analyzer.py` | layout 由 Visual Plan 明确指定 |

---

## 7. 实现阶段

| 阶段 | 目标 | 核心交付物 |
|------|------|-----------|
| **P0: 骨架** | 前后端跑通 | FastAPI + React + PostgreSQL + 基础 CRUD |
| **P1: Content Plan** | 能生成大纲 | 迁移 narrative 逻辑，前端卡片预览 Content Plan |
| **P2: Visual + Prompt** | 能生图、看 Prompt | Visual Plan + Prompt Engine + LLM 翻译 + Prompt Modal |
| **P3: 完整流水线** | 能下载 PPTX | Celery + Executor + Assembler + 状态机 + 重试 |

---

## 8. 关键原则（开发时随时回顾）

1. **Prompt 由 LLM 写，不由代码拼。**
2. **每层只做一件事，边界清晰。**
3. **负面约束极简，信任模型能力。**
4. **数据库驱动，不是文件驱动。**
5. **前端只展示，逻辑放后端。**
6. **知识迁移，不是代码搬家。**
