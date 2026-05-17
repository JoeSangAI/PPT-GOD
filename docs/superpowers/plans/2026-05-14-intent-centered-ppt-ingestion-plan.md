# Intent-Centered PPT Ingestion Implementation Plan

> **For Joe_1:** This plan is the execution path for `docs/superpowers/specs/2026-05-14-intent-centered-ppt-ingestion-design.md`.
> **Goal:** Recover OCR/page text from image-based PPTs, persist the user's source-treatment intent, and make content planning plus Agent behavior use that intent instead of assuming every uploaded PPT should be 1:1 replicated.

## Constraints From Current Code

- `backend/app/services/document_parser.py` emits `PPT_SOURCE` and page markers, but image-only slides produce empty page blocks.
- `backend/app/services/pptx_asset_extractor.py` already extracts screenshot/reference images and stores page metadata in `ReferenceImage.asset_analysis`.
- `backend/app/services/content_plan.py` currently treats a single uploaded PPT with no transform keywords as direct 1:1 replicate through `build_direct_ppt_replicate_outline()`.
- `backend/app/api/slides.py` calls `load_project_documents()` and `generate_content_plan()` from `_generate_content_plan_bg()`.
- `backend/app/api/chat.py` builds a `project_context` dict for Agent prompts but has no durable source-treatment contract.
- `DEVELOPMENT_MEMORY.md` says user-facing copy should focus on jobs-to-be-done, not internal terms such as pipeline or process modes.

## Desired Behavior

- If the user says "1:1", "复刻", "原样", "内容不要动", the deck uses `replicate`: same page count, same page order, minimal cleanup.
- If the user says "优化", "美化", "做得更好", or gives no explicit source-treatment instruction for a finished PPT, the deck uses `polish`: page order preserved, page count same or similar, headings/body may be lightly rewritten.
- If the user says "重组", "提炼", "压缩", "扩展", "融合", or gives a target page count, the deck uses `restructure` or `merge`.
- If intent is ambiguous in a way that materially changes the artifact, Agent asks one narrow question in outcome language.
- OCR/page understanding is material recovery and runs regardless of whether the user wants replicate, polish, or restructure.

## Task 1: Add Intent Contract Unit Tests

Create `backend/tests/test_source_intent.py` first. These tests define the source-treatment contract independently from content planning.

```python
from app.services.source_intent import (
    contract_to_planning_policy,
    infer_intent_contract,
    normalize_intent_contract,
)


SINGLE_PPT_DIAGNOSTICS = {
    "ppt_source_count": 1,
    "source_page_count": 35,
    "editable_text_density": "sparse",
    "image_only_page_count": 35,
}


def test_replicate_cues_lock_verbatim_same_order():
    contract = infer_intent_contract(
        "请 1:1 复刻这份 PPT，内容不要动，页序不要乱",
        source_diagnostics=SINGLE_PPT_DIAGNOSTICS,
    )

    assert contract["task_type"] == "replicate"
    assert contract["rewrite_level"] == "none"
    assert contract["page_order_policy"] == "preserve"
    assert contract["page_count_policy"] == "same"
    assert contract["source_fidelity"] == "verbatim"
    assert contract["visual_source_use"] == "page_reference"
    assert contract["confidence"] >= 0.8


def test_finished_ppt_default_is_light_polish_not_replicate():
    contract = infer_intent_contract(
        "帮我把这个 PPT 做得更好",
        source_diagnostics=SINGLE_PPT_DIAGNOSTICS,
    )

    assert contract["task_type"] == "polish"
    assert contract["rewrite_level"] == "light"
    assert contract["page_order_policy"] == "preserve"
    assert contract["source_fidelity"] == "faithful"


def test_restructure_cues_allow_reordering_and_target_count():
    contract = infer_intent_contract(
        "把这几份材料提炼成 12 页，结构可以重组",
        source_diagnostics={"ppt_source_count": 2, "source_page_count": 60},
    )

    assert contract["task_type"] in {"restructure", "merge"}
    assert contract["rewrite_level"] in {"moderate", "free"}
    assert contract["page_order_policy"] == "can_reorder"
    assert contract["page_count_policy"] == "target_count"


def test_normalize_rejects_unknown_values_and_preserves_evidence():
    contract = normalize_intent_contract({
        "task_type": "magic",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 2,
        "evidence": ["页序不要乱"],
    })

    assert contract["task_type"] == "polish"
    assert contract["confidence"] == 1.0
    assert contract["evidence"] == ["页序不要乱"]


def test_planning_policy_maps_contract_to_runtime_flags():
    policy = contract_to_planning_policy({
        "task_type": "replicate",
        "rewrite_level": "none",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "verbatim",
        "visual_source_use": "page_reference",
        "confidence": 0.9,
        "evidence": ["1:1"],
    })

    assert policy["allow_direct_ppt_replicate"] is True
    assert policy["preserve_source_page_order"] is True
    assert policy["preserve_source_page_count"] is True
    assert policy["rewrite_instruction"] == "verbatim"
```

Run:

```bash
pytest backend/tests/test_source_intent.py -q
```

Expected result before implementation: fails because `app.services.source_intent` does not exist.

## Task 2: Implement Source Intent Service

Create `backend/app/services/source_intent.py`.

Implementation requirements:

- Export `DEFAULT_INTENT_CONTRACT`, `normalize_intent_contract()`, `infer_intent_contract()`, `merge_intent_contract()`, and `contract_to_planning_policy()`.
- Keep this deterministic and cheap. It should not call an LLM.
- Use Chinese and English cue dictionaries for replicate, polish, restructure, merge, extract, and template-reference requests.
- Treat a single uploaded PPT as `polish` by default unless the brief contains explicit replicate cues.
- Return `confidence` and `evidence` so Agent can explain why it chose a treatment.
- Keep all internal enum values out of user-facing copy.

Core shape:

```python
ALLOWED_VALUES = {
    "task_type": {"replicate", "polish", "restructure", "extract", "merge", "template_reference"},
    "rewrite_level": {"none", "light", "moderate", "free"},
    "page_order_policy": {"preserve", "mostly_preserve", "can_reorder"},
    "page_count_policy": {"same", "similar", "target_count", "free"},
    "source_fidelity": {"verbatim", "faithful", "optimized", "synthesized"},
    "visual_source_use": {"page_reference", "style_reference", "asset_library", "ignore"},
}

DEFAULT_INTENT_CONTRACT = {
    "task_type": "polish",
    "rewrite_level": "light",
    "page_order_policy": "preserve",
    "page_count_policy": "same",
    "source_fidelity": "faithful",
    "visual_source_use": "page_reference",
    "confidence": 0.55,
    "evidence": [],
}
```

Planning policy mapping:

```python
def contract_to_planning_policy(contract: dict | None) -> dict:
    normalized = normalize_intent_contract(contract)
    task_type = normalized["task_type"]
    return {
        "task_type": task_type,
        "allow_direct_ppt_replicate": task_type == "replicate" and normalized["rewrite_level"] == "none",
        "preserve_source_page_order": normalized["page_order_policy"] in {"preserve", "mostly_preserve"},
        "preserve_source_page_count": normalized["page_count_policy"] == "same",
        "requires_clarification": normalized["confidence"] < 0.5,
        "rewrite_instruction": normalized["source_fidelity"],
    }
```

Run:

```bash
pytest backend/tests/test_source_intent.py -q
```

Expected result: all tests in this file pass.

## Task 3: Persist Intent Contract On Project

Add database, model, schema, and API support.

Files:

- `backend/app/models/models.py`
- `backend/app/schemas/project.py`
- `backend/app/api/projects.py`
- `backend/alembic/versions/202605140001_add_project_intent_contract.py`
- `frontend/src/api/client.ts`

Model change:

```python
intent_contract = Column(JSON, nullable=True)
```

Schema change:

```python
class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    style_id: Optional[str] = None
    content_plan_confirmed: Optional[bool] = None
    intent_contract: Optional[dict] = None


class ProjectResponse(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    style_proposal: Optional[dict] = None
    selected_style: Optional[dict] = None
    selected_template_recommendations: Optional[dict] = None
    intent_contract: Optional[dict] = None
    has_unread_notification: bool = False
    unread_notification_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
```

API update:

```python
from app.services.source_intent import normalize_intent_contract

if payload.intent_contract is not None:
    project.intent_contract = normalize_intent_contract(payload.intent_contract)
```

Migration:

```python
"""add project intent contract

Revision ID: 202605140001
Revises: 202605060002
Create Date: 2026-05-14 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "202605140001"
down_revision = "202605060002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("intent_contract", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("projects", "intent_contract")
```

Frontend client type:

```ts
export async function updateProject(
  projectId: string,
  data: { title?: string; content_plan_confirmed?: boolean; intent_contract?: Record<string, any> }
) {
  const res = await apiFetch(`${API_BASE}/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return (await checkRes(res)).json();
}
```

Run:

```bash
pytest backend/tests/test_source_intent.py -q
```

Then run an Alembic syntax check:

```bash
python -m compileall backend/alembic/versions/202605140001_add_project_intent_contract.py
```

Expected result: tests pass and migration compiles.

## Task 4: Add PPT Page Recovery Tests

Extend `backend/tests/test_document_parser.py` and add focused tests for image-only slide recovery without calling a real OCR/VLM service.

Add helper in the test file:

```python
from io import BytesIO
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def _pptx_with_full_slide_picture(label: str = "截图里的标题") -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    image = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), label, fill="black")
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    slide.shapes.add_picture(
        image_buffer,
        0,
        0,
        width=prs.slide_width,
        height=prs.slide_height,
    )
    output = BytesIO()
    prs.save(output)
    return output.getvalue()
```

Test image-only recovery:

```python
def test_parse_pptx_recovers_text_from_image_only_slide(monkeypatch):
    from app.services import pptx_page_recovery
    from app.services.document_parser import parse_document

    def fake_reader(image_path: str, *, page_num: int, source_filename: str) -> dict:
        return {
            "ocr_text": "疯火轮 AI 营销平台\n核心能力：智能投放、素材管理、数据复盘",
            "page_intent": "介绍平台核心能力",
            "key_facts": ["智能投放", "素材管理", "数据复盘"],
            "confidence": 0.91,
        }

    monkeypatch.setattr(pptx_page_recovery, "read_ppt_page_image", fake_reader)

    text = parse_document(_pptx_with_full_slide_picture(), "平台介绍.pptx")

    assert '--- PPT_SOURCE filename="平台介绍.pptx" pages=1 ---' in text
    assert "--- 第1页 ---" in text
    assert "【截图识别文字】" in text
    assert "疯火轮 AI 营销平台" in text
    assert "【页面意图】介绍平台核心能力" in text
```

Test dense editable text skips recovery:

```python
def test_parse_pptx_does_not_call_reader_for_dense_editable_text(monkeypatch):
    from app.services import pptx_page_recovery
    from app.services.document_parser import parse_document

    called = False

    def fake_reader(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(pptx_page_recovery, "read_ppt_page_image", fake_reader)

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    box.text = "这是足够长的可编辑正文。" * 10
    output = BytesIO()
    prs.save(output)

    text = parse_document(output.getvalue(), "editable.pptx")

    assert "这是足够长的可编辑正文" in text
    assert "【截图识别文字】" not in text
    assert called is False
```

Run:

```bash
pytest backend/tests/test_document_parser.py -q
```

Expected result before implementation: new tests fail because page recovery is not integrated.

## Task 5: Implement PPT Page Text Recovery

Create `backend/app/services/pptx_page_recovery.py`.

Implementation requirements:

- Detect sparse slide text using normalized character count. Default threshold: fewer than 20 non-whitespace characters.
- Pick the largest picture on the slide when it covers at least 35 percent of slide area, or when the slide has no editable text.
- Save the picture blob to a temporary PNG/JPEG file for the existing image reader.
- Default reader should call `describe_context_image()` with purpose text that asks for readable text, UI labels, and page intent.
- Tests should monkeypatch `read_ppt_page_image()` so no network or provider credentials are required.
- Return markdown sections that content planning can parse as normal page text.

Service outline:

```python
def slide_text_is_sparse(lines: list[str], min_chars: int = 20) -> bool:
    text = re.sub(r"\s+", "", "\n".join(lines or ""))
    return len(text) < min_chars


def read_ppt_page_image(image_path: str, *, page_num: int, source_filename: str) -> dict:
    description = describe_context_image(
        image_path,
        f"{source_filename} 第{page_num}页",
        "原 PPT 页面截图",
        "恢复这页 PPT 中可读文字、界面标签、关键事实和页面意图",
    )
    return parse_page_recovery_description(description)


def recover_sparse_slide_text(slide, *, page_num: int, source_filename: str, existing_lines: list[str]) -> list[str]:
    if not slide_text_is_sparse(existing_lines):
        return []
    image_path = extract_largest_slide_picture_to_tempfile(slide)
    if not image_path:
        return []
    recovered = read_ppt_page_image(image_path, page_num=page_num, source_filename=source_filename)
    return render_recovered_page_sections(recovered)
```

Modify `backend/app/services/document_parser.py`:

```python
from app.services.pptx_page_recovery import recover_sparse_slide_text

def _parse_pptx(file_bytes: bytes, filename: str = "") -> str:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(BytesIO(file_bytes))
    text_parts = [f'--- PPT_SOURCE filename="{filename}" pages={len(prs.slides)} ---']
    for i, slide in enumerate(prs.slides, 1):
        slide_texts = _extract_shape_texts(slide.shapes, MSO_SHAPE_TYPE)
        slide_texts.extend(
            text for text in recover_sparse_slide_text(
                slide,
                page_num=i,
                source_filename=filename,
                existing_lines=slide_texts,
            )
            if text
        )
        notes = _extract_notes_text(slide)
        if notes:
            slide_texts.append("【备注】\n" + notes)
        text_parts.append(f"--- 第{i}页 ---\n" + "\n".join(slide_texts))
    return "\n\n".join(text_parts)
```

Run:

```bash
pytest backend/tests/test_document_parser.py backend/tests/test_source_intent.py -q
```

Expected result: both test files pass.

## Task 6: Make Content Planning Consume Intent Contract

Add tests in `backend/tests/test_content_plan_intent_contract.py`.

Test direct replicate is gated by contract:

```python
from app.services.content_plan import build_direct_ppt_replicate_outline, infer_page_count_from_single_ppt


DOCS = '''--- PPT_SOURCE filename="source.pptx" pages=2 ---

--- 第1页 ---
【截图识别文字】
平台介绍
一句话说明

--- 第2页 ---
【截图识别文字】
核心能力
智能投放
素材管理
'''


def test_direct_replicate_requires_replicate_contract():
    polish = {"task_type": "polish", "rewrite_level": "light", "page_order_policy": "preserve", "page_count_policy": "same", "source_fidelity": "faithful", "visual_source_use": "page_reference", "confidence": 0.7, "evidence": []}
    replicate = {"task_type": "replicate", "rewrite_level": "none", "page_order_policy": "preserve", "page_count_policy": "same", "source_fidelity": "verbatim", "visual_source_use": "page_reference", "confidence": 0.9, "evidence": ["1:1"]}

    assert build_direct_ppt_replicate_outline(DOCS, "帮我优化", intent_contract=polish) == []
    outline = build_direct_ppt_replicate_outline(DOCS, "请 1:1 复刻", intent_contract=replicate)
    assert [page["page_num"] for page in outline] == [1, 2]
    assert outline[0]["source_refs"][0]["source_page_num"] == 1


def test_single_ppt_page_count_respects_preserve_contract():
    polish = {"task_type": "polish", "rewrite_level": "light", "page_order_policy": "preserve", "page_count_policy": "same", "source_fidelity": "faithful", "visual_source_use": "page_reference", "confidence": 0.7, "evidence": []}
    restructure = {"task_type": "restructure", "rewrite_level": "moderate", "page_order_policy": "can_reorder", "page_count_policy": "target_count", "source_fidelity": "optimized", "visual_source_use": "page_reference", "confidence": 0.8, "evidence": ["12页"]}

    assert infer_page_count_from_single_ppt(DOCS, "帮我优化", intent_contract=polish) == 2
    assert infer_page_count_from_single_ppt(DOCS, "提炼成 12 页", intent_contract=restructure) is None
```

Implementation changes in `backend/app/services/content_plan.py`:

- Import `contract_to_planning_policy`, `infer_intent_contract`, and `normalize_intent_contract`.
- Add `intent_contract: dict | None = None` to:
  - `build_direct_ppt_replicate_outline()`
  - `infer_page_count_from_single_ppt()`
  - `_document_preservation_mode()`
  - `_document_preservation_policy()`
  - `_annotate_ppt_source_refs()`
  - `generate_content_page_map()`
  - `_generate_model_page_map()`
  - `generate_content_plan()`
- Replace direct calls to `_is_ppt_transform_request(topic)` for uploaded PPT behavior with planning policy from the contract.
- Keep `_is_ppt_transform_request()` as a fallback when no contract is supplied.
- For `polish`, do not use `build_direct_ppt_replicate_outline()`; use page map generation with recovered text as the source draft.
- For `polish`, keep `_annotate_ppt_source_refs()` same-page mapping when page order is preserved.
- For `replicate`, allow existing deterministic direct path.

Prompt injection for content planning:

```python
def _intent_contract_policy_text(intent_contract: dict | None) -> str:
    contract = normalize_intent_contract(intent_contract)
    policy = contract_to_planning_policy(contract)
    if policy["task_type"] == "replicate":
        return "【用户处理意图】按原 PPT 页序和页数整理，尽量保留原文，只做必要清理。"
    if policy["task_type"] == "polish":
        return "【用户处理意图】保留原 PPT 页序和主要事实，优化标题和正文表达，不重组叙事。"
    if policy["task_type"] in {"restructure", "merge", "extract"}:
        return "【用户处理意图】可按用户目标重组材料，但必须保留关键事实并标注来源页。"
    if policy["task_type"] == "template_reference":
        return "【用户处理意图】上传 PPT 主要作为视觉和版式参考，正文可依据 Brief 重新组织。"
    return ""
```

Run:

```bash
pytest backend/tests/test_content_plan_intent_contract.py backend/tests/test_source_intent.py -q
```

Expected result: tests pass.

## Task 7: Persist Contract During Content Plan Generation

Modify `backend/app/api/slides.py`.

In `_generate_content_plan_bg()`:

- Load documents as it already does.
- Infer or merge the project contract from the topic, current project contract, and PPT diagnostics.
- Persist `project.intent_contract`.
- Pass the contract into `infer_page_count_from_single_ppt()` and `generate_content_plan()`.

Add helper in `backend/app/services/source_intent.py`:

```python
def source_diagnostics_from_documents(documents: str) -> dict:
    sources = detect_ppt_sources(documents)
    return {
        "ppt_source_count": len(sources),
        "source_page_count": sum(int(item.get("pages") or 0) for item in sources),
        "has_ppt_source": bool(sources),
    }
```

Use in `_generate_content_plan_bg()`:

```python
from sqlalchemy.orm.attributes import flag_modified
from app.services.source_intent import (
    infer_intent_contract,
    merge_intent_contract,
    source_diagnostics_from_documents,
)

source_diagnostics = source_diagnostics_from_documents(documents)
inferred_contract = infer_intent_contract(topic, source_diagnostics=source_diagnostics)
project.intent_contract = merge_intent_contract(project.intent_contract, inferred_contract)
flag_modified(project, "intent_contract")
db.commit()

inferred_page_count = infer_page_count_from_single_ppt(documents, topic, intent_contract=project.intent_contract)

outline = generate_content_plan(
    topic=topic,
    audience="通用受众",
    page_count=page_count,
    documents=documents,
    on_progress=progress_cb,
    intent_contract=project.intent_contract,
)
```

Run:

```bash
pytest backend/tests/test_content_plan_intent_contract.py backend/tests/test_source_intent.py -q
```

Expected result: tests pass.

## Task 8: Make Agent Aware Of Source-Treatment Intent

Add tests in `backend/tests/test_chat_source_intent.py` for deterministic helper functions rather than full streaming.

Extract or add helpers in `backend/app/api/chat.py`:

- `_source_intent_context_text(project_context: dict) -> str`
- `_update_project_intent_from_message_if_needed(project: Project, user_message: str, documents: str, db: Session) -> dict`

Expected context text:

```python
def test_source_intent_context_uses_outcome_language():
    text = _source_intent_context_text({
        "intent_contract": {
            "task_type": "polish",
            "rewrite_level": "light",
            "page_order_policy": "preserve",
            "page_count_policy": "same",
            "source_fidelity": "faithful",
            "visual_source_use": "page_reference",
            "confidence": 0.7,
            "evidence": ["做得更好"],
        }
    })

    assert "保留原页顺序" in text
    assert "优化标题和正文" in text
    assert "task_type" not in text
```

Implementation changes:

- Add `intent_contract` to `context` in `chat_with_agent()`.
- Append `_source_intent_context_text(context)` to draft and normal content prompts.
- When a content Agent message contains new source-treatment cues, update `project.intent_contract` through `merge_intent_contract()`.
- If `contract_to_planning_policy(contract)["requires_clarification"]` is true and content plan has not started, instruct Agent to ask one narrow clarification:

```text
如果用户对上传 PPT 的处理方式不清楚，只问一个窄问题：更接近“按原页顺序轻优化”，还是“可以重组结构”？不要问泛泛的“你想做什么 PPT”。
```

Run:

```bash
pytest backend/tests/test_chat_source_intent.py backend/tests/test_source_intent.py -q
```

Expected result: tests pass.

## Task 9: Update UI Status Copy And Project Types

Keep UI changes small and outcome-oriented.

Files:

- `frontend/src/api/client.ts`
- `frontend/src/App.tsx`

Client type already changes in Task 3. In `App.tsx`, only adjust existing status text where it currently says materials are being added or content planning is starting.

Candidate copy replacements:

- Keep: `已加入 N 个文件，正在后台整理文字和图片素材`
- Add when content generation starts with uploaded docs: `正在读取原 PPT 的文字和页面截图，准备生成内容规划。`
- Content completion copy remains focused on checking `页数、标题和顺序`.

Avoid adding visible internal terms:

- Do not show `OCR`.
- Do not show `Intent Contract`.
- Do not show enum values such as `replicate` or `polish`.

Add lightweight frontend assertions in `frontend/src/project-isolation.test.mjs`, because that file already checks project-facing workflow behavior by reading `App.tsx`:

```js
assert.match(
  source,
  /正在读取原 PPT 的文字和页面截图，准备生成内容规划。/,
  "content-plan startup copy should tell users the app is reading PPT text and screenshots"
);
assert.doesNotMatch(
  source,
  /OCR pipeline|Intent Contract|classification=/,
  "user-facing workflow copy must not expose internal processing labels"
);
```

Run:

```bash
npm --prefix frontend test -- --runInBand
```

If this repo does not define that script, run the existing frontend checks listed by `cat frontend/package.json`.

Expected result: frontend checks pass or unsupported script is explicitly reported.

## Task 10: End-To-End Validation With The User's Sample PPT

Use the sample file path provided in the conversation:

```text
/Users/Joe_1/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/qq87302538_aaad/msg/file/2026-05/疯火轮平台介绍2026.05(3).pptx
```

Create a temporary verification script under `.superpowers/tmp/intent_ppt_check.py` during execution, or run a one-off Python command that:

- Reads the PPTX bytes.
- Calls `parse_document(file_bytes, filename)`.
- Counts page markers.
- Counts pages containing `【截图识别文字】` or recovered page text.
- Calls `infer_intent_contract("帮我把这个 PPT 做得更好", source_diagnostics=sample_diagnostics)`.
- Calls `infer_intent_contract("请 1:1 复刻，内容不要动", source_diagnostics=sample_diagnostics)`.

Expected checks:

- The parser still reports `pages=35`.
- Image-only pages no longer produce all-empty page blocks when the page reader is available.
- "做得更好" maps to `polish`.
- "1:1 复刻" maps to `replicate`.

Do not commit the temporary verification script.

## Task 11: Full Verification

Run backend focused tests:

```bash
pytest \
  backend/tests/test_source_intent.py \
  backend/tests/test_document_parser.py \
  backend/tests/test_content_plan_intent_contract.py \
  backend/tests/test_chat_source_intent.py \
  -q
```

Run broader backend tests if focused tests pass:

```bash
pytest backend/tests -q
```

Run frontend checks:

```bash
cat frontend/package.json
```

Then run the repo's available frontend test command from `scripts`, with `npm --prefix frontend` if the script exists.

Run static compile checks:

```bash
python -m compileall backend/app backend/alembic/versions
```

Expected final state:

- New tests pass.
- Existing relevant tests pass.
- The sample PPT produces recoverable per-page text when image reading is available.
- The content planning path no longer assumes a single uploaded PPT means deterministic 1:1 replication unless the intent contract says so.
- User-facing copy describes reading text and screenshots, not internal classifiers.

## Task 12: Review Before Completion

Before finalizing implementation:

- Inspect `git diff --stat`.
- Inspect all modified user-facing strings against `DEVELOPMENT_MEMORY.md`.
- Confirm no unrelated dirty files were reverted.
- Confirm no temporary verification files are staged.
- Summarize:
  - Intent contract behavior.
  - OCR/page recovery behavior.
  - Content planning behavior by intent.
  - Tests run and any unsupported commands.
