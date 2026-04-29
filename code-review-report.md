# PPT GOD Visual Plan — 代码审查报告

审查范围：
1. `backend/app/services/visual_plan.py`
2. `backend/app/services/template_extractor.py`
3. `backend/app/api/slides.py`
4. `frontend/src/App.tsx`
5. `frontend/src/api/client.ts`

---

## 1. Critical Issues（必须修复）

### 1.1 全局内存字典 — 进程级状态泄漏与并发风险
**文件**：`backend/app/api/slides.py:21`
**代码**：
```python
generation_progress: dict[str, dict] = {}
```
**问题**：
- 进度存储在进程内存中，**应用重启后数据全部丢失**，无法恢复后台任务状态。
- 没有 TTL / 清理机制，项目数量增长时会导致**内存无限膨胀**。
- 多 Worker 部署（如 Gunicorn）时，**各 Worker 之间进度不共享**，前端轮询可能打到没有进度的 Worker 上，表现为进度"卡死"。
- 非协程安全：FastAPI 虽然是单线程 asyncio，但如果有同步阻塞代码或后台线程，字典操作可能出现竞态。

**修复建议**：
将 `generation_progress` 迁移到 Redis（推荐）或数据库表中，设置合理的 TTL（如 24 小时）：
```python
# 使用 Redis
import redis
r = redis.Redis.from_url(settings.REDIS_URL)
r.setex(f"ppt:progress:{project_id}", 86400, json.dumps(data))
```

---

### 1.2 乐观锁并发缺陷 — 重复启动生成任务
**文件**：`backend/app/api/slides.py:313-317`
**代码**：
```python
if project.status == "generating":
    raise HTTPException(status_code=400, detail="当前已有生成任务在执行中")
```
**问题**：
这是**纯内存检查**，没有数据库锁。在并发请求场景下：
1. 请求 A 读取 status = "prompt_ready"
2. 请求 B 同时读取 status = "prompt_ready"
3. A 和 B 都通过检查，各自启动一个 Celery 任务
4. 两个任务同时写入同一批 slide 的图片文件，**产生覆盖和数据混乱**

**修复建议**：
使用数据库级别的乐观锁或分布式锁：
```python
# 方案 A：数据库行锁
from sqlalchemy import select
result = db.execute(
    select(Project).where(Project.id == project_id).with_for_update()
)
project = result.scalar_one()
if project.status == "generating":
    raise HTTPException(400, "...")
project.status = "generating"
db.commit()  # 释放锁

# 方案 B：Redis 分布式锁（跨 Worker 安全）
with redis_lock.Lock(r, f"ppt:generate:{project_id}", timeout=300):
    ...
```

---

### 1.3 LLM 调用无异常处理 — 服务级联崩溃
**文件**：`backend/app/services/visual_plan.py:132-142`
**代码**：
```python
response = client.chat.completions.create(
    model=settings.MINIMAX_LLM_MODEL,
    messages=[...],
    temperature=0.5,
)
```
**问题**：
LLM API 调用可能因网络超时、API 限额、服务不可用等原因抛出异常。此处**完全没有 try-except**，异常会直接上抛，导致：
- 如果由前端直接调用，返回 500 错误，用户体验差
- 如果在 Celery 后台任务中调用，任务失败但**没有优雅降级**，整批页面都得不到 visual description

**修复建议**：
```python
try:
    response = client.chat.completions.create(...)
except (openai.APIError, openai.APITimeoutError, openai.RateLimitError) as e:
    logger.error(f"LLM API 失败: {e}")
    # 降级：返回基于规则生成的默认描述
    return _generate_fallback_visual_plan(content_plan, style)
```

---

### 1.4 文件上传 — 无大小限制 + 原始文件名未净化
**文件**：`backend/app/api/slides.py:440-446`
**代码**：
```python
filename = f"{prefix}{role}_{file.filename}"
file_path = os.path.join(project_upload_dir, filename)
with open(file_path, "wb") as buffer:
    shutil.copyfileobj(file.file, buffer)
```
**问题**：
1. **无文件大小限制**：恶意用户可上传数 GB 文件，耗尽磁盘空间。
2. **`file.filename` 直接使用**：虽然 `os.path.basename` 在前面的逻辑中没有被显式调用（实际上 `UploadFile.filename` 可能已经过滤了路径分隔符，但依赖框架行为不可靠），文件名中的特殊字符可能导致文件系统问题。

**修复建议**：
```python
import uuid
from pathlib import Path

# 限制文件大小（如 10MB）
MAX_FILE_SIZE = 10 * 1024 * 1024
content = await file.read()
if len(content) > MAX_FILE_SIZE:
    raise HTTPException(413, "文件过大，最大支持 10MB")

# 使用随机文件名，保留原始扩展名
safe_name = f"{prefix}{role}_{uuid.uuid4().hex}{Path(file.filename).suffix}"
file_path = os.path.join(project_upload_dir, safe_name)
with open(file_path, "wb") as f:
    f.write(content)
```

---

### 1.5 React 直接操作 DOM — 绕过虚拟 DOM 导致不一致
**文件**：`frontend/src/App.tsx:1513-1516`, `2183-2186`
**代码**：
```tsx
onError={(e) => {
  const el = e.target as HTMLImageElement;
  el.style.display = "none";
  el.parentElement!.innerHTML = '<div class="...">图片加载失败</div>';
}}
```
**问题**：
- `parentElement!.innerHTML = ...` **直接修改 DOM**，完全绕过 React 的虚拟 DOM。React 下一次 reconcile 时可能因 DOM 结构与预期不符而报错或行为异常。
- `!` 非空断言：如果组件在图片加载前被卸载，`parentElement` 可能为 null，导致**运行时崩溃**。

**修复建议**：
使用 React state 控制错误状态：
```tsx
const [imgError, setImgError] = useState(false);

{!imgError ? (
  <img src={...} onError={() => setImgError(true)} />
) : (
  <div className="...">图片加载失败</div>
)}
```

---

### 1.6 useEffect 缺少依赖数组 — 每次渲染都执行
**文件**：`frontend/src/App.tsx:2415-2443`
**代码**：
```tsx
useEffect(() => {
  if (slide.content_json !== prevContentRef.current && !isUndoingRef.current) {
    // ... setState calls
  }
});
```
**问题**：
**没有依赖数组**，这意味着每次组件渲染都会执行这个 effect。虽然内部有 `prevContentRef` 检查防止无限循环，但：
- 造成**不必要的性能开销**
- 如果未来有人修改逻辑，不慎移除了防护条件，会立刻引发**无限渲染循环**
- React 18 Strict Mode 下，这个 effect 会被执行两次（mount + simulate remount），可能意外触发两次状态更新

**修复建议**：
```tsx
useEffect(() => {
  if (slide.content_json !== prevContentRef.current && !isUndoingRef.current) {
    prevContentRef.current = slide.content_json;
    // ... 状态更新
  }
}, [slide.content_json]); // 明确依赖
```

---

### 1.7 快捷键事件监听器 — 闭包陈旧 + 频繁注册注销
**文件**：`frontend/src/App.tsx:2354-2371`
**代码**：
```tsx
useEffect(() => {
  const onKeyDown = (e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
      // ...
    }
  };
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}, [history.length]);
```
**问题**：
- 依赖 `history.length` 意味着**每次 undo/redo 都会重新添加/移除全局事件监听器**。
- `handleUndo` / `handleRedo` 被闭包捕获，如果它们引用了过时的 state（比如 `history` 数组的旧引用），会导致撤销/重做逻辑错误。
- 全局 `window.addEventListener` 在编辑器组件内捕获快捷键，但如果页面中有输入框（如聊天框、标题编辑），快捷键会被错误拦截。

**修复建议**：
```tsx
// 使用 ref 存储最新状态，避免闭包问题
const historyRef = useRef(history);
historyRef.current = history;
const historyIndexRef = useRef(historyIndex);
historyIndexRef.current = historyIndex;

useEffect(() => {
  const onKeyDown = (e: KeyboardEvent) => {
    // 如果焦点在输入框，不拦截
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      if (e.shiftKey) {
        handleRedo();
      } else {
        handleUndo();
      }
    }
  };
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}, []); // 只在 mount/unmount 时注册
```

---

### 1.8 模板提取目录竞态 — TOCTOU 漏洞
**文件**：`backend/app/services/template_extractor.py:63-70`
**代码**：
```python
output_dir = os.path.join(upload_dir, project_id, "templates")
os.makedirs(output_dir, exist_ok=True)
if os.path.exists(output_dir):
    shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)
```
**问题**：
- **TOCTOU（Time-of-check to time-of-use）**：检查 `os.path.exists` 和 `shutil.rmtree` 之间，如果另一个请求创建了同名文件/目录，`rmtree` 会抛出异常。
- 并发上传模板时，一个请求正在 `rmtree`，另一个请求可能正在读取目录，导致**文件丢失或读取错误**。

**修复建议**：
使用时间戳或 UUID 作为子目录名，避免覆盖：
```python
from datetime import datetime
output_dir = os.path.join(upload_dir, project_id, "templates", datetime.now().strftime("%Y%m%d_%H%M%S"))
os.makedirs(output_dir, exist_ok=True)
# 不再删除旧目录，定期用 cron 清理即可
```

---

### 1.9 轮询内存泄漏 — 组件卸载后 interval 仍在运行
**文件**：`frontend/src/App.tsx:1887-1939`
**代码**：
在按钮 onClick 中创建了 `checkInterval` 和 `progressInterval`，但这些 interval 的 cleanup 只在：
1. 切换项目时（第367-377行 useEffect cleanup）
2. interval 自身逻辑中（当 slides 生成完成时）

**问题**：
- 如果组件在 interval 运行期间**卸载**（如用户关闭浏览器标签页，但 React 层面是组件卸载），interval **不会被清理**。
- 虽然 `contentPlanPollTimeoutRef` 和 `contentPlanProgressIntervalRef` 在 useEffect cleanup 中被清理，但按钮点击中创建的 interval **没有保存到这些 ref 中**（`checkInterval` 就没有被保存到任何 ref）。
- 闭包引用了 `selectedProject` 等 state，导致**已卸载组件的状态无法被 GC**。

**修复建议**：
将 interval 管理提取到自定义 Hook 中，确保 cleanup：
```tsx
function useContentPlanPolling(projectId: string, onComplete: (slides: Slide[]) => void) {
  const intervalsRef = useRef<ReturnType<typeof setInterval>[]>([]);
  
  const start = useCallback(async () => {
    const progressInterval = setInterval(...);
    const checkInterval = setInterval(...);
    intervalsRef.current = [progressInterval, checkInterval];
  }, [projectId]);
  
  useEffect(() => {
    return () => intervalsRef.current.forEach(clearInterval);
  }, []);
  
  return { start };
}
```

---

### 1.10 空值判断逻辑错误 — 误判"已完成"状态
**文件**：`backend/app/api/slides.py:211`, `273`
**代码**：
```python
if all(s.visual_json for s in slides):
    project.status = "visual_ready"
# ...
if all(s.prompt_text for s in slides):
    project.status = "prompt_ready"
```
**问题**：
- Python 中**空字典 `{}` 是 falsy 的**。如果某 slide 的 `visual_json` 是 `{}`（表示"已初始化但没有内容"），`all()` 会返回 False，项目状态永远不会推进。
- 同理，空字符串 `""` 也是 falsy 的。如果某 slide 的 `prompt_text` 是 `""`（LLM 返回了空 prompt），状态也不会推进。
- 更隐蔽的是：如果 `slides` 列表为空（理论上不应发生，但如果数据异常），`all()` 返回 True，会错误地标记为已完成。

**修复建议**：
```python
# 显式检查是否为 None
if all(s.visual_json is not None for s in slides):
    project.status = "visual_ready"

# 或检查是否包含必要字段
if all(s.visual_json and s.visual_json.get("visual_description") for s in slides):
    project.status = "visual_ready"
```

---

## 2. Medium Issues（建议修复）

### 2.1 大量 TypeScript `any` 类型
**文件**：`frontend/src/App.tsx`, `frontend/src/api/client.ts`
**影响位置**：
- `App.tsx:43-58`：`style_proposal: any`, `selected_style: any`, `content_json: any`, `visual_json: any`
- `App.tsx:208-211`：`projectStatus: any`, `referenceImages: any[]`, `templatePages: any[]`, `documents: any[]`
- `client.ts:150`：`pageContext?: any`
- `client.ts:270`：`selectedStyle: any`

**问题**：
`any` 类型**完全放弃了 TypeScript 的类型安全**，导致：
- 编译器无法捕获拼写错误（如 `msg.positoining`）
- IDE 无法提供自动补全
- 重构时无法安全重命名

**修复建议**：
定义明确的接口类型：
```typescript
// types/slide.ts
interface VisualJson {
  visual_description: string;
  layout: string;
  seed_family: string;
  is_seed_recommended?: boolean;
}

interface Slide {
  id: string;
  page_num: number;
  type: 'cover' | 'toc' | 'content' | 'hero' | 'data' | 'ending' | 'section';
  status: 'pending' | 'planning' | 'visual_ready' | 'prompt_ready' | 'generating' | 'completed' | 'failed';
  content_json: {
    text_content?: {
      headline?: string;
      subhead?: string;
      body?: string | Array<{ content: string } | string>;
    };
    speaker_notes?: string;
  };
  visual_json: VisualJson | null;
  prompt_text: string | null;
  image_path: string | null;
  reference_images?: Array<{ id: string; role: string; url: string }>;
}
```

---

### 2.2 异常类型错误处理 — `err: any`
**文件**：`frontend/src/App.tsx`（多处）
**示例**：`catch (err: any) { alert("..." + err.message) }`

**问题**：
在 TypeScript 中，`catch` 子句的参数类型默认是 `unknown`。使用 `err: any` 虽然能编译通过，但如果抛出的不是 Error（比如 `throw "string"`），`err.message` 会是 undefined，导致 alert 显示 "undefined"。

**修复建议**：
```typescript
catch (err: unknown) {
  const message = err instanceof Error ? err.message : "未知错误";
  alert("失败：" + message);
}
```

---

### 2.3 API 响应类型缺失
**文件**：`frontend/src/api/client.ts`（全部函数）
**示例**：
```typescript
export async function fetchProjects() {
  const res = await fetch(`${API_BASE}/projects`);
  return (await checkRes(res)).json();
}
```

**问题**：
所有 API 函数都返回 `Promise<any>`，调用方无法知道返回数据的结构。

**修复建议**：
```typescript
export interface Project {
  id: string;
  title: string;
  status: string;
  // ...
}

export async function fetchProjects(): Promise<Project[]> {
  const res = await fetch(`${API_BASE}/projects`);
  return (await checkRes(res)).json();
}
```

---

### 2.4 重复的三段式异步逻辑
**文件**：`frontend/src/App.tsx:468-511`
**代码**：`handleGenerateVisual`, `handleGeneratePrompts`, `handleStartGeneration` 三个函数结构几乎完全相同。

**问题**：
DRY 原则 violation。如果未来需要统一添加 loading 状态、错误上报或重试逻辑，需要修改三处。

**修复建议**：
提取通用 Hook：
```typescript
function useProjectAction<T extends (...args: any[]) => Promise<any>>(
  action: T,
  options: { onSuccess?: () => void; errorMessage: string }
) {
  const [loading, setLoading] = useState(false);
  const execute = useCallback(async (...args: Parameters<T>) => {
    setLoading(true);
    try {
      await action(...args);
      options.onSuccess?.();
    } catch (err) {
      alert(options.errorMessage + ": " + (err instanceof Error ? err.message : "未知错误"));
    } finally {
      setLoading(false);
    }
  }, [action]);
  return { loading, execute };
}
```

---

### 2.5 复杂的 JSX 条件渲染难以维护
**文件**：`frontend/src/App.tsx:1115-1191`（header 部分）

**问题**：
header 区域根据 `currentStatus` 渲染不同的按钮组合，条件嵌套很深，超过 70 行 JSX 内联逻辑。这导致：
- 难以一眼看出每个状态下有什么按钮
- 容易在修改时破坏某个分支的逻辑

**修复建议**：
提取为配置表 + 映射组件：
```typescript
const ACTION_BUTTONS: Record<string, React.FC<{ project: Project; isBusy: boolean }>> = {
  planning: PlanningActions,
  visual_ready: VisualReadyActions,
  prompt_ready: PromptReadyActions,
  // ...
};

// 在 header 中
const ActionsComponent = ACTION_BUTTONS[currentStatus];
return ActionsComponent ? <ActionsComponent project={selectedProject} isBusy={isBusy} /> : null;
```

---

### 2.6 模块内延迟导入 — 代码异味
**文件**：`backend/app/services/visual_plan.py:28-29`, `149`
**代码**：
```python
def _load_style(...):
    # ...
    import yaml
    meta = yaml.safe_load(meta_text) or {}
```

**问题**：
在函数内部 `import yaml` 和 `import re` 是不必要的。Python 的模块导入是幂等的，延迟导入不会带来性能优势，反而：
- 降低代码可读性
- 如果模块不存在，**错误会在运行时而不是启动时暴露**
- 静态分析工具（如 pylint/mypy）难以追踪

**修复建议**：
将所有 import 移到文件顶部。

---

### 2.7 硬编码的视觉风格参数
**文件**：`backend/app/services/visual_plan.py:68-69`
**代码**：
```python
theme = "Modern business presentation"
mood = "Professional, clean, confident"
```

**问题**：
主题和氛围是硬编码的，无法根据不同风格模板调整。深海商务和科技未来的 mood 显然不应该相同。

**修复建议**：
从 style 模板的 meta 中读取：
```python
theme = style["meta"].get("theme", "Modern business presentation")
mood = style["meta"].get("mood", "Professional, clean, confident")
```

---

### 2.8 混乱的 JSX 布尔表达式
**文件**：`frontend/src/App.tsx:1481-1498`
**代码**：
```tsx
{text.body && (
  (typeof text.body === "string" && text.body.trim()) ||
  (Array.isArray(text.body) && text.body.length > 0)
) && (
  <div className="...">
```

**问题**：
这个条件表达式虽然不会导致崩溃，但**可读性极差**，维护者很难一眼看出它在判断什么。而且依赖 JavaScript 的隐式类型转换（truthy/falsy）容易出错。

**修复建议**：
提取为清晰的辅助函数：
```typescript
function hasBodyContent(body: unknown): boolean {
  if (typeof body === "string") return body.trim().length > 0;
  if (Array.isArray(body)) return body.length > 0;
  return false;
}

// JSX 中
{hasBodyContent(text.body) && (
  <div className="...">...</div>
)}
```

---

### 2.9 URL 参数未编码
**文件**：`frontend/src/api/client.ts:124-126`
**代码**：
```typescript
if (slideId) {
  url += `?slide_id=${slideId}`;
}
```

**问题**：
如果 `slideId` 包含特殊字符（如 `&`、`=`、`?`），会破坏 URL 结构。

**修复建议**：
```typescript
const params = new URLSearchParams();
if (slideId) params.append("slide_id", slideId);
const query = params.toString();
const url = `${API_BASE}/projects/${projectId}/reference-images${query ? "?" + query : ""}`;
```

---

### 2.10 模板推荐数据未使用后端返回结果
**文件**：`frontend/src/App.tsx:1336-1348`
**代码**：
```tsx
<TemplateRecommender
  pages={templatePages}
  recommendations={{
    cover: templatePages[0] || null,
    toc: templatePages[1] || null,
    content: templatePages[Math.floor(templatePages.length / 2)] || null,
    ending: templatePages[templatePages.length - 1] || null,
  }}
```

**问题**：
后端 `extract-template` API 已经返回了智能推荐结果（`recommendations` 字段，基于 content_plan 的启发式规则），但前端**完全忽略了这些数据**，而是硬编码了自己的简单规则。这意味着如果后端改进了推荐算法，前端不会受益。

**修复建议**：
将后端返回的 recommendations 保存到 state 并使用：
```typescript
const [templateRecommendations, setTemplateRecommendations] = useState<Recommendations | null>(null);
// 在 extract-template API 调用成功后
setTemplateRecommendations(data.recommendations);
```

---

### 2.11 SSE 解析器鲁棒性不足
**文件**：`frontend/src/api/client.ts:165-191`
**代码**：
```typescript
for (const line of lines) {
  if (line.startsWith("data: ")) {
    try {
      const data = JSON.parse(line.slice(6));
      yield data;
    } catch {
      // ignore malformed lines
    }
  }
}
```

**问题**：
1. 如果服务端发送了 `event: error` 或其他 event type，会被**静默忽略**。
2. 如果 JSON 数据跨越多行（包含换行符），当前逻辑只按 `\n` 分割，可能导致 JSON 不完整而被忽略。
3. 如果服务端发送了 `data: [DONE]`（OpenAI 风格），也会被当作 JSON 解析失败而忽略。

**修复建议**：
```typescript
// 支持多行 data 字段
interface SSEEvent {
  event?: string;
  data: string;
}

function* parseSSE(buffer: string): Generator<SSEEvent> {
  const blocks = buffer.split("\n\n");
  for (const block of blocks) {
    const lines = block.split("\n");
    let event: string | undefined;
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event: ")) event = line.slice(7);
      if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (dataLines.length > 0) {
      yield { event, data: dataLines.join("\n") };
    }
  }
}
```

---

### 2.12 删除 Slide 时未同步更新其他 JSON 字段中的 page_num
**文件**：`backend/app/api/slides.py:600-634`
**代码**：
删除 slide 后只更新了 `content_json.page_num`，但：
- `visual_json` 中可能也有 `page_num`
- `prompt_text` 中可能通过模板插入了 `page_num`

**问题**：
虽然 `visual_json` 中的 `page_num` 目前只在 `generate_visual_plan` 时使用（重新生成时会覆盖），但如果未来有功能依赖 `visual_json.page_num` 进行匹配，会导致**数据不一致**。

**修复建议**：
在删除/重排序时，统一更新所有包含 page_num 的字段：
```python
def _sync_page_num(slide: Slide, new_page_num: int):
    slide.page_num = new_page_num
    for field in [slide.content_json, slide.visual_json]:
        if field and isinstance(field, dict):
            updated = copy.deepcopy(field)
            updated["page_num"] = new_page_num
            # 根据字段类型赋值回对应属性
```

---

### 2.13 后台任务异常处理中的二次异常吞没
**文件**：`backend/app/api/slides.py:82-89`
**代码**：
```python
except Exception:
    pass
```

**问题**：
在回滚失败后，尝试更新项目状态。如果这一步也失败，异常被**完全吞没**，没有任何日志记录。这意味着如果数据库连接断开，开发者完全不知道发生了什么。

**修复建议**：
```python
except Exception as inner_e:
    logger.critical(f"无法将项目标记为失败状态: {inner_e}")
```

---

### 2.14 LibreOffice 输出文件名不确定性
**文件**：`backend/app/services/template_extractor.py:31-35`
**代码**：
```python
generated_pdf = os.path.join(output_dir, os.path.splitext(os.path.basename(ppt_path))[0] + ".pdf")
if not os.path.exists(generated_pdf):
    raise RuntimeError("PDF 文件未生成")
```

**问题**：
LibreOffice 对非 ASCII 文件名可能有特殊处理（如编码转换、截断等），导致输出的 PDF 文件名与预期不符。当前的检查是脆弱的。

**修复建议**：
使用临时目录 + 重命名：
```python
with tempfile.TemporaryDirectory() as tmpdir:
    # 复制文件到临时目录，使用纯 ASCII 文件名
    temp_ppt = os.path.join(tmpdir, "input.pptx")
    shutil.copy2(ppt_path, temp_ppt)
    convert_ppt_to_pdf(temp_ppt, tmpdir)
    # 转换后目录中应该只有一个 PDF
    pdf_files = [f for f in os.listdir(tmpdir) if f.endswith(".pdf")]
    if not pdf_files:
        raise RuntimeError("PDF 未生成")
    return os.path.join(tmpdir, pdf_files[0])
```

---

### 2.15 fetch 无超时控制
**文件**：`frontend/src/api/client.ts`（全部函数）

**问题**：
原生 `fetch` **没有内置超时**，如果网络异常导致请求挂起，会永远等待。

**修复建议**：
封装带超时的 fetch：
```typescript
async function fetchWithTimeout(url: string, options: RequestInit & { timeout?: number } = {}) {
  const { timeout = 30000, ...fetchOptions } = options;
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(url, { ...fetchOptions, signal: controller.signal });
    return res;
  } finally {
    clearTimeout(id);
  }
}
```

---

## 3. Nice-to-have Improvements（可选优化）

### 3.1 使用数组索引作为 React Key
**文件**：`frontend/src/App.tsx:1776`
**代码**：`{chatMessages.map((msg, i) => (<div key={i}>...</div>))}`

**问题**：
当消息被删除或重新排序时，使用索引作为 key 会导致 React 的 reconciliation 效率降低，可能引发 UI 状态错乱（如输入框内容错位）。

**建议**：
为每条消息分配唯一 ID（UUID 或时间戳 + 随机数）：
```typescript
interface ChatMessage {
  id: string; // 新增
  role: "user" | "agent";
  content: string;
  // ...
}

// 创建消息时
const newMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: "..." };
```

---

### 3.2 状态管理可以进一步解耦
**文件**：`frontend/src/App.tsx`

**问题**：
App.tsx 目前超过 2600 行，包含：
- 项目列表管理
- Slide 管理
- 聊天交互
- 文件上传
- 编辑器的撤销/重做
- 拖放排序
- 轮询逻辑

这是一个典型的"God Component"。虽然对于 MVP 阶段可以接受，但长期维护会变得困难。

**建议**：
按功能拆分为自定义 Hook：
```typescript
// hooks/useProject.ts
function useProject(projectId: string) { ... }

// hooks/useSlides.ts
function useSlides(projectId: string) { ... }

// hooks/useChat.ts
function useChat(projectId: string) { ... }

// hooks/usePolling.ts
function useGenerationPolling(projectId: string, status: string) { ... }
```

---

### 3.3 进度条除以零风险
**文件**：`frontend/src/App.tsx:1760`
**代码**：
```tsx
style={{ width: `${(projectStatus.completed_slides / projectStatus.total_slides) * 100}%` }}
```

**问题**：
如果 `total_slides` 为 0，会产生 `NaN`，CSS width 变成 `"NaN%"`。

**建议**：
```tsx
const progress = projectStatus.total_slides > 0
  ? (projectStatus.completed_slides / projectStatus.total_slides) * 100
  : 0;
```

---

### 3.4 硬编码的风格提案
**文件**：`frontend/src/App.tsx:1297-1323`

**问题**：
风格提案（深海商务、暖调极简、科技未来）是硬编码在 JSX 中的，后端无法动态更新。

**建议**：
将风格定义提取为配置文件或数据库表，前端通过 API 获取。

---

### 3.5 `_infer_seed_family` 可以使用 Set 提高可读性
**文件**：`backend/app/services/visual_plan.py:54-62`
**代码**：
```python
def _infer_seed_family(page_type: str) -> str:
    if page_type in ("cover", "ending"):
        return "bookend"
    if page_type in ("hero",):
        return "hero"
    # ...
```

**建议**：
```python
SEED_FAMILY_MAP = {
    "bookend": {"cover", "ending"},
    "hero": {"hero"},
    "section": {"toc"},
}

def _infer_seed_family(page_type: str) -> str:
    for family, types in SEED_FAMILY_MAP.items():
        if page_type in types:
            return family
    return "content"
```

---

### 3.6 API endpoint 命名可以更加 RESTful
**文件**：`backend/app/api/slides.py`

**当前**：
- `POST /projects/{id}/set-seed` — 动词式
- `POST /projects/{id}/retry-failed` — 动词式

**建议**：
- `POST /projects/{id}/slides/{slide_id}/seed`（设置种子）/ `DELETE`（取消）
- `POST /projects/{id}/failed-slides/retry`

---

### 3.7 图片缓存策略可以优化
**文件**：`frontend/src/App.tsx:77-80`
**代码**：
```typescript
function getSlideImageUrl(imagePath: string, status?: string) {
  const base = `${API_BASE}${imagePath.replace("./outputs", "/outputs")}`;
  const cacheBuster = status ? `?v=${status}` : `?t=${Date.now()}`;
  return `${base}${cacheBuster}`;
}
```

**问题**：
使用 `Date.now()` 作为 cache buster 意味着**每次 render 都会生成不同的 URL**，浏览器无法利用缓存。

**建议**：
使用基于文件修改时间或内容哈希的缓存策略：
```typescript
function getSlideImageUrl(imagePath: string, version?: string) {
  const base = `${API_BASE}${imagePath.replace("./outputs", "/outputs")}`;
  // 使用后端返回的版本号或文件哈希
  return version ? `${base}?v=${version}` : base;
}
```

---

## 总结

| 类别 | 数量 | 核心关注点 |
|------|------|-----------|
| Critical | 10 | 并发安全、内存泄漏、类型安全、DOM 操作、异常处理 |
| Medium | 15 | 代码复用、DRY 原则、类型定义、API 设计、鲁棒性 |
| Nice-to-have | 7 | 性能优化、代码组织、RESTful 命名 |

**最优先修复的前 3 个**：
1. **全局内存字典迁移到 Redis**（影响多 Worker 部署和数据持久化）
2. **乐观锁并发防护**（影响数据一致性，可能产生重复生成）
3. **React 直接操作 DOM 改为 state 驱动**（可能导致 React 运行时错误）
