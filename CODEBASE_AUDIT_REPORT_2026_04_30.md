# PPT-GOD 代码库全身体检报告

**审计日期**: 2026-04-30
**审计范围**: 全栈（Frontend + Backend + DB + API + Quality）
**代码规模**: ~25,000 行（124 个文件）
**审计团队**: 5 个专项 Agent 并行审计

---

## 评分卡

| 维度 | 评分 | 说明 |
|------|------|------|
| 安全性 | D | XSS漏洞 + 路径遍历 + 无输入消毒 |
| 性能 | C | N+1查询 + 阻塞IO + 无索引 + 全量加载 |
| 可维护性 | F | 5803行App.tsx + 1897行slides.py + 大量重复代码 |
| 类型安全 | D | any泛滥 + schema漂移 + eslint主动关闭类型检查 |
| 错误处理 | D | 覆盖率低 + 静默吞异常 + 死代码 |
| 架构设计 | C | 模块化不足 + 职责混乱 + 同步/异步混用 |
| 测试覆盖 | D | 核心模块零测试 + 测试结构混乱 |
| 部署配置 | C | 生产配置指向localhost + migration反向 |

---

## 致命（Critical）— 8 项

### C1. XSS 安全漏洞 — `dangerouslySetInnerHTML` 渲染未消毒的用户内容
**位置**: `frontend/src/App.tsx:3793`, `:4277`, `:3835`, `:4762`
**问题**: 4 处使用 `dangerouslySetInnerHTML` 渲染 `marked.parse()` 输出。`marked` 默认不净化 HTML，用户输入的 Markdown 中可注入 `<script>` 标签或 `onerror=` 事件处理器。
**证据**:
```tsx
<div dangerouslySetInnerHTML={{ __html: renderMarkdown(text.body) }} />
```
`renderMarkdown` 仅做样式类名替换，无 HTML 消毒。
**修复**: 安装 DOMPurify，在 `renderMarkdown` 返回前消毒 HTML。

---

### C2. FastAPI 事件循环被阻塞 — 所有 Service 层全是同步代码
**位置**: `backend/app/services/`（64 个函数，0 个 async）
**问题**: FastAPI 是异步框架，但所有 service 函数都是同步的。特别是 `image_generation.py:152` 和 `:276` 有 `time.sleep()` 调用，会阻塞整个事件循环，导致所有并发请求卡住。
**证据**:
```python
# 阻塞代码
sleep_time = 5 * (2 ** attempt)
time.sleep(sleep_time)  # 阻塞整个服务器！
```
**修复**: 将 IO 密集型操作改为 `async` 或使用 `asyncio.to_thread()` 包装。

---

### C3. `App.tsx` 5803 行 — 超级单体组件
**位置**: `frontend/src/App.tsx`
**问题**: 一个文件包含 5803 行代码，40+ useState、15+ useRef、20+ useEffect，承担了状态管理、路由逻辑、API 调用、UI 渲染、聊天流、拖拽、undo/redo 等所有职责。
**影响**:
- 任何小改动都可能引发意外 bug
- 代码 Review 困难
- 多人协作冲突率高
- 首屏加载慢（单一巨大 chunk）
**修复**: 按功能拆分为独立组件和自定义 hooks。

---

### C4. 数据库 Migration 完全反向 — `upgrade()` 删表
**位置**: `backend/alembic/versions/4c3107fea191_initial.py`
**问题**: `upgrade()` 执行 `op.drop_table('slides')`/`drop_table('projects')`，而 `downgrade()` 执行 `create_table`。运行 `alembic upgrade head` 会把已有表删掉，是毁灭性的。
**根因**: 对已有数据库做 autogenerate 时，Alembic 检测到表已存在，生成了 drop 操作。
**修复**: 立即修复 initial migration，将 upgrade/downgrade 内容互换。

---

### C5. `content_plan_confirmed` 字段从未被迁移
**位置**: `backend/app/models/models.py:24`
**问题**: `Project.content_plan_confirmed` 存在于模型中，但全部 3 个 migration 文件都没有该字段。说明直接改模型但没生成 migration，靠 SQLite 宽松模式蒙混过关。
**风险**: 新环境部署会因字段缺失报错。
**修复**: 立即生成 migration `add content_plan_confirmed to projects`。

---

### C6. ThreadPoolExecutor 回调中执行 `db.commit()` — 线程安全问题
**位置**: `backend/app/services/generation_pipeline.py:271, 291, 305, 323`
**问题**: SQLAlchemy session 不是线程安全的。在 `ThreadPoolExecutor` 的回调循环中调用 `slide.status = "failed"` 和 `db.commit()`，会导致 SQLite 线程错误或静默数据损坏。
**修复**: 将所有 DB 变更移回主线程，worker 线程只返回纯数据结果。

---

### C7. SQLAlchemy 懒加载在 worker 线程中触发
**位置**: `backend/app/services/generation_pipeline.py:30-84`
**问题**: `_load_reference_images` 接收 `Slide` 对象后访问 `slide.project.reference_images`（懒加载关系）。在 ThreadPoolExecutor worker 中触发 SQL 查询，加剧线程安全违规。
**修复**: 在主线程预加载所有参考图数据，向 worker 传入纯数据结构（文件路径、PIL Image）。

---

### C8. 直接 DOM 操作破坏 React 虚拟 DOM
**位置**: `frontend/src/App.tsx:3835, :4762, :5276-5290, :5793`
**问题**: 代码直接修改 `element.innerHTML`，绕过 React 状态管理。导致虚拟 DOM 与实际 DOM 不同步、内存泄漏、不可预见的 UI bug。
**修复**: 使用 React state 和条件渲染。

---

## 严重（Major）— 18 项

### M1. `updateProject` PATCH 因 Schema 错误导致 422
**位置**: `backend/app/api/projects.py`, `frontend/src/api/client.ts`
**问题**: 前端发送 `{"content_plan_confirmed": true}`（无 title），但后端 `update_project()` 使用 `payload: ProjectCreate`，其中 `title: str` 是必填项。导致 Pydantic 校验错误 → HTTP 422。
**修复**: 创建 `ProjectUpdate` schema，所有字段设为 Optional。

---

### M2. 数据库高频查询字段全部无索引
**位置**: `backend/app/models/models.py`
**问题**: 以下字段被频繁 `filter()` 但无任何索引：
- `Slide.project_id` — 几乎每个 slide 查询都按它过滤
- `Slide.status` — 生成状态检查
- `ReferenceImage.project_id` / `slide_id` / `role`
- `Project.status`
**修复**: 添加复合索引，如 `Index('ix_slides_project_id_status', 'project_id', 'status')`。

---

### M3. Schema 与 Model 严重漂移
**位置**: `backend/app/schemas/project.py`, `models.py`
**具体问题**:
1. `ProjectCreate.topic` (line 14): Schema 有该字段，但模型和路由完全忽略它 — 幽灵字段。
2. `SlideResponse` 缺少 `error_msg`: 模型有该字段，但 schema 没定义。
3. `ReferenceImage` 完全没有 Schema: API 返回时全是手写 dict。
4. `ProjectBase.content_plan_confirmed`: Schema 允许传 None，但数据库 `nullable=False`。
**修复**: 对齐 schema 和 model，删除死字段，补上缺失字段。

---

### M4. `Slide.reference_images` 缺少级联删除
**位置**: `backend/app/models/models.py:52`
**问题**: `Slide.reference_images` 没有 `cascade="all, delete-orphan"。删除 slide 时，页面级 ReferenceImage 会变成孤儿记录。
**修复**: 添加级联删除。注意只影响页面级参考图（slide_id 非空）。

---

### M5. N+1 查询问题
**位置**: `backend/app/api/slides.py:317` `list_slides`
**问题**: 获取 slides 后遍历访问 `s.reference_images`，每页触发一次关联查询。`create_visual_plan` 和 `create_prompts` 已正确使用 `joinedload`，但 `list_slides` 漏了。
**修复**: 添加 `joinedload(Slide.reference_images)`。

---

### M6. API 路由文件过大
**位置**: `backend/app/api/slides.py` (1897 行), `chat.py` (704 行)
**问题**: `slides.py` 一个文件处理 15+ 个端点，违反单一职责原则。
**修复**: 拆分为 `slides_crud.py`, `slides_generation.py`, `slides_reorder.py` 等。

---

### M7. 错误处理严重不足
**位置**: `backend/app/api/` 全部
**数据**: 64 个 API 函数中只有 58 处 try/except。
**问题**: 许多端点缺少异常捕获，出错返回 500 且无友好错误信息。`chat.py:324` 甚至 `except Exception: pass` 静默吞掉序列化错误。

---

### M8. TypeScript `any` 类型泛滥
**位置**: `frontend/src/api/client.ts`, `App.tsx`
**数据**: 至少 15+ 处 `any`，包括核心函数参数。
**证据**:
```ts
const body: any = {}
pageContext?: any
contentJson: any
visualJson: any
selectedStyle: any
```
**根因**: `eslint.config.js` 主动关闭 `@typescript-eslint/no-explicit-any`。
**修复**: 重新开启规则，定义 `Project`, `Slide`, `ChatMessage`, `ApiResponse<T>` 接口。

---

### M9. 异步/同步混用混乱
**位置**: `backend/app/api/`
**数据**: 64 个 API 函数中仅 8 个是 `async def`。所有 service 层 64 个函数全是同步。
**问题**: 外部 API 调用（MiniMax、DeerAPI）本可以并行，却被串行阻塞。async 路由直接调用同步 SQLAlchemy 查询阻塞事件循环。

---

### M10. `dangerouslySetInnerHTML` 无输入消毒
**位置**: `frontend/src/App.tsx`
**问题**: 除了 marked 输出的 XSS 风险外，图片加载失败时直接设置 `innerHTML = '<div...>'` 也是潜在风险点。

---

### M11. 内存中的 `generation_progress` 不可靠
**位置**: `backend/app/api/slides.py:50`
**问题**: `generation_progress: dict[str, dict] = {}` 不是线程安全的，多 worker 进程不共享，重启后数据丢失。
**修复**: 改为 Redis 存储或从 slide 状态实时计算进度。

---

### M12. 路径遍历漏洞
**位置**: `backend/app/api/documents.py:122`
**问题**: `delete_document` 中 `filename` 直接拼入 `os.path.join(docs_dir, filename)`，未过滤 `..` 路径遍历（`upload_document` 有校验但 delete 没有）。
**修复**: 添加 `secure_filename` 校验。

---

### M13. `_running_tasks` 存在竞态条件
**位置**: `backend/app/api/slides.py:564-575`
**问题**: 对全局 dict 的 get-then-cancel-then-replace 操作不是原子的，并发请求下不安全。

---

### M14. 代码重复严重
**位置**: 多处
**问题**:
- `_load_project_documents` 在 `slides.py` 和 `chat.py` 中完全重复
- `_reference_process_mode_instruction` 在 `slides.py` 和 `prompt_engine.py` 中重复
- think-tag/markdown 清洗逻辑在 5+ 文件中重复
- `workflow.js` 和 `workflow.ts` 几乎完全重复
- Loading Spinner SVG 在 `App.tsx` 中复制粘贴 7+ 次

---

### M15. 未使用的依赖
**位置**: `frontend/package.json`
**问题**: `react-markdown` + `remark-gfm` 已安装但源代码中从未引用。`@types/marked` 冗余（marked v18 自带类型）。
**修复**: 移除这 3 个包，添加 `@types/turndown`。

---

### M16. `pollForStyleProposals` 低效轮询
**位置**: `frontend/src/api/client.ts:392-406`
**问题**: 每 3 秒调用 `fetchProjects()` 遍历所有项目找目标，而不是轮询单项目端点。
**修复**: 使用 `GET /projects/{id}`（需后端配合暴露该端点）。

---

### M17. useEffect 无依赖数组导致每次渲染都执行
**位置**: `frontend/src/App.tsx:2353-2368`
**问题**:
```tsx
useEffect(() => { ... }); // 完全无 deps
```
每次渲染都执行对象比较、状态更新、历史操作，有无限循环风险。

---

### M18. Git 仓库混入大文件和用户数据
**位置**: `.gitignore` 缺漏
**问题**:
- `backend/pptgod.db.backup-20260429-174810` (3.3MB) 已提交
- `backend/1` (17KB Celery 日志) 已提交
- `.gitignore` 未忽略 `backend/pptgod.db*`、`frontend/test-output/`
**修复**: 清理误提交文件，补全 `.gitignore`。

---

## 中等（Minor）— 14 项

### m1. `list_slides` 路由未使用 `response_model`
**位置**: `backend/app/api/slides.py:311`
**问题**: 返回手写 dict，没有 `response_model=List[SlideResponse]`，字段校验缺失。

---

### m2. `status` 字段无 Enum 约束
**位置**: `backend/app/models/models.py`
**问题**: `status` 是普通 `String`，没有 `Enum` 或 `CheckConstraint`。实际状态值（draft/planning/visual_ready/...）全靠业务层硬编码，容易因拼写错误导致状态机混乱。

---

### m3. `created_at` / `updated_at` 缺少 `server_default`
**位置**: `backend/app/models/models.py`
**问题**: 只有 Python 层面的 `default=utc_now`，批量插入或 raw SQL 时不生效。

---

### m4. `ReferenceImage` 缺少审计字段
**位置**: `backend/app/models/models.py`
**问题**: 没有 `created_at`，而 `Project` 和 `Slide` 都有。

---

### m5. 前端缺少 `fetchProject(id)`
**位置**: `frontend/src/api/client.ts`
**问题**: 后端有 `GET /projects/{id}`，但前端只有 `fetchProjects()` 列表接口。代码必须拉取全部项目再客户端过滤。

---

### m6. `ProjectCreate.topic` 是死字段
**位置**: `backend/app/schemas/project.py:14`
**问题**: Schema 有 `topic` 字段，但 `Project` 模型和 `create_project` 路由都完全忽略它。

---

### m7. 死代码 — `update_reference_image` 的不可达代码
**位置**: `backend/app/api/slides.py:1413-1423`
**问题**: `return` 后有 `db.delete(ref)` / `os.remove()` 等代码永远不会执行，是复制粘贴残留。

---

### m8. `generateStyleProposals` 返回双形态
**位置**: `backend/app/api/projects.py`, `frontend/src/api/client.ts`
**问题**: 后端返回两种互斥结构（缓存时带 proposals，异步时带 status），前端类型为 `Promise<any>` 无收窄。

---

### m9. 错误信息展示原始 JSON
**位置**: `frontend/src/api/client.ts`
**问题**: `checkRes()` 对 FastAPI 的 `{"detail": "..."}` 错误直接切片显示，用户看到 `HTTP 400: {"detail":"没有失败的页面需要重试"}`。

---

### m10. `updateReferenceImageMode` 用 FormData 发 PATCH
**位置**: `frontend/src/api/client.ts:182-190`
**问题**: 其他 PATCH/POST 都用 `application/json`，此端点却用 `multipart/form-data` 传单个字符串，不一致。

---

### m11. `chat.py` 静默吞掉序列化错误
**位置**: `backend/app/api/chat.py:324-325`
**问题**: `except Exception: pass`，LLM 失去关键页面上下文约束，可能修改错误幻灯片。

---

### m12. 时间戳使用 Python default 而非 server_default
**位置**: `backend/app/models/models.py`
**问题**: `onupdate` 只在 ORM 层生效，直接 UPDATE 语句不会触发。

---

### m13. `llm_client.py` 绕过 Pydantic Settings 直接读 `.env`
**位置**: `backend/app/core/llm_client.py:13-26`
**问题**: `settings` 对象已通过 `SettingsConfigDict` 读取 `.env`，自定义文件读取逻辑冗余且形成第二真相源。

---

### m14. 缺少启动时 API Key 校验
**位置**: `backend/app/core/config.py`
**问题**: `MINIMAX_API_KEY` 和 `DEER_API_KEY` 默认空字符串，应用能启动但运行时 LLM 调用会 401。

---

## 提示（Info）— 6 项

### I1. SQLAlchemy 1.x 风格代码
**位置**: `backend/app/models/models.py`
**问题**: 使用 `Column(String, ...)` 而非 2.0 推荐的 `mapped_column(String, ...)`。技术债务。

---

### I2. ID 字段使用无长度限制的 `String`
**位置**: `backend/app/models/models.py`
**问题**: UUID 固定 36 字符，无长度限制在 PostgreSQL/MySQL 中会产生宽 VARCHAR。

---

### I3. `frontend/.env.production` 指向 localhost
**位置**: `frontend/.env.production`
**问题**: `VITE_API_BASE_URL=http://localhost:8000`，不是生产配置。

---

### I4. 前端 README 是 Vite 默认模板
**位置**: `frontend/README.md`
**问题**: 与项目完全无关。

---

### I5. 日志中英文混用
**位置**: 全局
**问题**: 部分中文、部分英文，不利于日志解析和监控。

---

### I6. `requirements.txt` 版本全部固定
**问题**: 所有依赖用 `==` 精确版本，安全但升级成本高。

---

## 测试覆盖分析

### 已测试模块（12 个 pytest 文件）
- API 集成测试（chat、slide content/visual、reorder、delete）
- JSON 解析逻辑（think 标签、破损 JSON 修复）
- 图片生成预算控制（mock/real/cached 模式）
- Slide 操作（reorder、delete 页码压缩）
- 参考图加载顺序和上下文
- 生成锁冲突恢复、过期 pending 任务重置

### 关键未测试路径
- `document_parser.py` — PDF/Word/PPT 解析 **零测试**
- `template_extractor.py` — 模板提取 **零测试**
- `style_proposal.py` — 风格提案生成 **零测试**
- `pptx_assembler.py` — PPTX 组装 **零测试**
- `prompt_engine.py` — LLM prompt 生成（仅间接测试）
- `content_plan.py` — 内容规划生成（仅回归测试覆盖截断逻辑）
- `projects.py` — 大部分 project API 端点 **零测试**
- `rollback` 逻辑 — **零测试**
- 前端 — **无任何单元测试**

### 测试结构问题
- 根目录 `test-e2e.py`、`test-fast.py`、`test-one.py` 是 standalone 脚本，不在 pytest 发现路径内
- `test_slide_ops.py` 使用 200+ 行自定义 SQLAlchemy mock，脆弱难维护

---

## 修复优先级矩阵

| 优先级 | 问题 | 预估工作量 | 影响 |
|--------|------|-----------|------|
| **P0（立即）** | C1 XSS：为 marked 输出加 DOMPurify | 30 分钟 | 安全 |
| **P0（立即）** | C4/C5 修复 migration 反向 + 补 content_plan_confirmed | 30 分钟 | 部署 |
| **P0（立即）** | C6/C7 ThreadPoolExecutor 中移除 db.commit + 预加载 ref 数据 | 2 小时 | 稳定性 |
| **P1（本周）** | M1 修复 `updateProject` PATCH 422 | 15 分钟 | 功能 |
| **P1（本周）** | M2 加数据库索引 | 20 分钟 | 性能 |
| **P1（本周）** | M3 Schema 对齐 | 30 分钟 | 可靠性 |
| **P1（本周）** | M5 N+1 查询修复 | 10 分钟 | 性能 |
| **P1（本周）** | M12 路径遍历修复 | 15 分钟 | 安全 |
| **P1（本周）** | M18 清理误提交大文件 + 补 .gitignore | 20 分钟 | Git 卫生 |
| **P2（本月）** | C3 拆分 App.tsx | 2-3 天 | 可维护性 |
| **P2（本月）** | M6 拆分 slides.py | 1 天 | 可维护性 |
| **P2（本月）** | M8 替换 any 类型 + 开启 eslint 规则 | 1-2 天 | 类型安全 |
| **P2（本月）** | M14 提取公共工具函数 | 半天 | 代码质量 |
| **P2（本月）** | 补齐核心服务测试 | 1-2 天 | 质量 |
| **P3（后续）** | M11 进度改为 Redis 存储 | 半天 | 可靠性 |
| **P3（后续）** | M16 轮询改为单项目端点 | 1 小时 | 性能 |

---

## 值得肯定的方面

1. **Celery 任务有 Redis 锁保护**: `generate_slides_task` 正确使用 `nx=True` 锁和 `finally` 清理
2. **Fallback 机制完善**: `visual_plan.py` 和 `content_plan.py` 在 LLM 返回畸形 JSON 时有兜底处理
3. **图片生成有预算控制**: `image_generation.py` 的 retry 逻辑避免对可能已计费的错误重试（仅 429 重试）
4. **API 路径对齐良好**: 42 对前后端端点路径、方法、参数名完全匹配
5. **文件上传有校验**: 扩展名、MIME、大小校验 + PIL 转 PNG，较安全
6. **无硬编码密钥**: 所有 API Key 通过 pydantic-settings + .env 读取
7. **无 SQL 注入**: 100% ORM 操作，零 raw SQL

---

*报告由 5 个专项审计 Agent 并行分析 + 人工复核生成*
