import json

from app.services.content_plan import (
    build_direct_ppt_replicate_outline,
    build_ppt_page_preserve_source_draft,
    build_document_driven_long_deck_draft,
    infer_page_count_from_single_ppt,
    sanitize_ppt_recovery_text_for_content,
)


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

POLLUTED_OCR_DOCS = '''--- PPT_SOURCE filename="source.pptx" pages=2 ---

--- 第1页 ---
【截图识别文字】
你好，我是 PPT Agent 的读图助手。以下是截图内容的详细解读：

### 1. OCR文字
* **左侧大标题：** 团队一起用
* **右侧卡片标题：** 个人版是工具，团队版是环境

### 2. 图像内容
这是一张产品介绍页截图。

### 4. 视觉参考
* **动感线条：** 闪电图形和底部运动场线条赋予了静态页面一种“速度感”。

【识别置信度】0.60

--- 第2页 ---
【截图识别文字】
### 1. OCR文字
* **主标题：** 协作知识库
* **正文内容：** 让团队资料沉淀为可复用资产
'''


def test_direct_replicate_requires_replicate_contract():
    polish = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.7,
        "evidence": [],
    }
    replicate = {
        "task_type": "replicate",
        "rewrite_level": "none",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "verbatim",
        "visual_source_use": "page_reference",
        "confidence": 0.9,
        "evidence": ["1:1"],
    }

    assert build_direct_ppt_replicate_outline(DOCS, "帮我优化", intent_contract=polish) == []
    outline = build_direct_ppt_replicate_outline(DOCS, "请 1:1 复刻", intent_contract=replicate)
    assert [page["page_num"] for page in outline] == [1, 2]
    assert outline[0]["source_refs"][0]["source_page_num"] == 1


def test_single_ppt_page_count_respects_preserve_contract():
    polish = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.7,
        "evidence": [],
    }
    restructure = {
        "task_type": "restructure",
        "rewrite_level": "moderate",
        "page_order_policy": "can_reorder",
        "page_count_policy": "target_count",
        "source_fidelity": "optimized",
        "visual_source_use": "page_reference",
        "confidence": 0.8,
        "evidence": ["12页"],
    }

    assert infer_page_count_from_single_ppt(DOCS, "帮我优化", intent_contract=polish) == 2
    assert infer_page_count_from_single_ppt(DOCS, "提炼成 12 页", intent_contract=restructure) is None


def test_sanitizes_polluted_ppt_page_recovery_text_before_content_planning():
    clean = sanitize_ppt_recovery_text_for_content(POLLUTED_OCR_DOCS)

    assert "团队一起用" in clean
    assert "个人版是工具，团队版是环境" in clean
    assert "协作知识库" in clean
    assert "视觉参考" not in clean
    assert "识别置信度" not in clean
    assert "动感线条" not in clean
    assert "PPT Agent 的读图助手" not in clean


def test_single_ppt_preserve_draft_uses_source_pages_not_polluted_markdown_headings():
    polish = {
        "task_type": "polish",
        "rewrite_level": "light",
        "page_order_policy": "preserve",
        "page_count_policy": "same",
        "source_fidelity": "faithful",
        "visual_source_use": "page_reference",
        "confidence": 0.7,
        "evidence": [],
    }

    outline = build_ppt_page_preserve_source_draft(
        POLLUTED_OCR_DOCS,
        "帮我优化，页码保持不变",
        intent_contract=polish,
    )
    text = json.dumps(outline, ensure_ascii=False)

    assert [page["page_num"] for page in outline] == [1, 2]
    assert "团队一起用" in text
    assert "个人版是工具，团队版是环境" in text
    assert "协作知识库" in text
    assert "视觉参考" not in text
    assert "识别置信度" not in text
    assert "动感线条" not in text


def test_long_deck_source_draft_does_not_inject_legacy_course_frame():
    outline = build_document_driven_long_deck_draft(
        topic="疯火轮AI——营销人的AI工作台",
        documents=DOCS,
        target_count=6,
        min_pages=6,
        max_pages=6,
    )
    text = json.dumps(outline, ensure_ascii=False)

    assert "道、法、术、器" not in text
    assert "今天这 90 分钟" not in text
    assert "企业营与销" not in text
    assert "大连" not in text
    assert "消费者决策中介" not in text
    assert "课程型" not in text
    assert "课程总览" not in text
    assert "课程内容" not in text
    assert "课堂" not in text
    assert "学员" not in text
