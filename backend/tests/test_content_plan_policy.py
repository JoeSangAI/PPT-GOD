from app.services.content_plan import (
    _document_preservation_mode,
    _document_preservation_policy,
    parse_exported_content_plan_markdown,
)


def test_short_uploaded_text_defaults_to_faithful_mode():
    documents = "这是用户写好的完整段落。\n\n关键判断：保持原文表达，不要重写。"

    assert _document_preservation_mode(documents, "帮我做成 PPT") == "faithful"
    policy = _document_preservation_policy(documents, "帮我做成 PPT")
    assert "整理成 PPT，而不是重写" in policy
    assert "尽量保留原文" in policy


def test_transform_request_allows_restructure_but_preserves_facts():
    documents = "第一章 很长的材料\n" * 200

    assert _document_preservation_mode(documents, "请总结提炼成 10 页") == "transform"
    assert "不得改变事实" in _document_preservation_policy(documents, "请总结提炼成 10 页")


def test_very_long_material_uses_synthesis_mode():
    documents = "完整书稿内容\n" * 8000

    assert _document_preservation_mode(documents, "帮我做成 PPT") == "synthesis"
    assert "材料过长" in _document_preservation_policy(documents, "帮我做成 PPT")


def test_pptgod_markdown_export_parses_back_to_pages():
    documents = """
--- 文档: 非凡产研战略框架-内容规划.md ---
# 非凡产研战略框架 - 内容规划导出

<!--
PPTGOD_EXPORT_KIND: content_plan_markdown
-->

---
<!-- PPTGOD_PAGE_START page_num=1 type=cover section_title="" -->
## P1 · cover

### 标题

非凡产研战略框架

### 副标题

成就你的非凡

### 正文

<!-- 留空 -->

### 备注

封面备注

<!-- PPTGOD_PAGE_END page_num=1 -->

---
<!-- PPTGOD_PAGE_START page_num=2 type=content section_title="使命愿景" -->
## P2 · content · 使命愿景

### 标题

服务 AI 创业者

### 副标题

使命与愿景

### 正文

- 数据
- 资源

### 备注

正文备注

<!-- PPTGOD_PAGE_END page_num=2 -->
"""

    pages = parse_exported_content_plan_markdown(documents)

    assert len(pages) == 2
    assert pages[0]["type"] == "cover"
    assert pages[0]["text_content"]["headline"] == "非凡产研战略框架"
    assert pages[0]["text_content"]["body"] == ""
    assert pages[0]["speaker_notes"] == "封面备注"
    assert pages[1]["section_title"] == "使命愿景"
    assert pages[1]["text_content"]["body"] == "- 数据\n- 资源"
