import json

from app.services import content_plan as content_plan_module
from app.services.content_plan import (
    build_long_deck_skeleton,
    build_document_driven_long_deck_draft,
    content_plan_from_page_map,
    generate_content_plan,
    generate_content_page_map,
    _document_preservation_mode,
    _document_preservation_policy,
    _enforce_requested_page_range,
    _extend_outline_to_target_count,
    _generate_deck_blueprint,
    _generate_outline_from_blueprint_in_chunks,
    _is_general_transform_request,
    _should_generate_deck_blueprint,
    generate_long_deck_outline_chunk,
    parse_page_map_markdown,
    parse_exported_content_plan_markdown,
    resolve_content_plan_page_target,
    should_generate_incremental_long_deck,
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


def test_page_count_expansion_is_general_transform_request():
    assert _is_general_transform_request("以这个为基础扩成 60-80 页培训课")
    assert _is_general_transform_request("做成60页到80页的PPT")


def test_long_page_target_uses_deck_blueprint():
    assert _should_generate_deck_blueprint((60, 80), 80, "课程材料")
    assert _should_generate_deck_blueprint(None, 60, "课程材料")
    assert _should_generate_deck_blueprint((60, 80), 80, "")
    assert not _should_generate_deck_blueprint((20, 30), 30, "课程材料")
    assert not _should_generate_deck_blueprint(None, 12, "课程材料")


def test_long_deck_target_uses_incremental_generation():
    assert resolve_content_plan_page_target("做成 60 到 80 页课程", 80) == (80, 60, 80)
    sparse_material = "# 主题\n\n- 只有一个观点"
    assert resolve_content_plan_page_target("做成 60 到 80 页课程", 80, sparse_material)[0] < 60
    assert should_generate_incremental_long_deck("做成 60 到 80 页课程", 80, "")
    assert not should_generate_incremental_long_deck("做成 60 到 80 页课程", 80, sparse_material)
    assert not should_generate_incremental_long_deck("做成 12 页课程", 12, "课程材料")


def test_requested_range_trim_preserves_closing_page():
    outline = [
        {
            "page_num": idx,
            "type": "cover" if idx == 1 else "ending" if idx == 8 else "content",
            "text_content": {"headline": f"第 {idx} 页", "body": "- 内容"},
        }
        for idx in range(1, 9)
    ]
    outline[-1]["text_content"]["headline"] = "结束与行动"

    trimmed = _enforce_requested_page_range(outline, (4, 5))

    assert len(trimmed) == 7
    assert trimmed[0]["type"] == "cover"
    assert trimmed[-1]["type"] == "ending"
    assert trimmed[-1]["page_num"] == 7
    assert trimmed[-1]["text_content"]["headline"] == "结束与行动"


def test_long_deck_skeleton_creates_all_pages_before_llm():
    skeleton = build_long_deck_skeleton(
        topic="我要制作一份在大连演讲的 PPT，听众是中小企业老板",
        target_count=80,
        min_pages=60,
        max_pages=80,
    )

    assert len(skeleton) == 80
    assert [page["page_num"] for page in skeleton] == list(range(1, 81))
    assert skeleton[0]["type"] == "cover"
    assert skeleton[-1]["type"] == "ending"
    assert all(page["generation_status"] == "skeleton" for page in skeleton)


def test_document_driven_long_deck_draft_uses_uploaded_material():
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道（为什么必须变）

### 第一部分：时代已经变了

- ChatGPT 达到 1 亿规模只用了 2 个月
- 中国 AI 原生 App 月活已达 4.4 亿

### 第二部分：变的是什么

- 消费者问 AI，AI 替他做选择
- 不被 AI 推荐约等于不存在

## 模块三：术（企业怎么布局）

### 零售

- 工厂实拍、产地溯源、试吃视频
- 不要用 AI 生成用户评价
"""

    draft = build_document_driven_long_deck_draft(
        topic="做成 60 到 80 页课程",
        documents=documents,
        target_count=10,
        min_pages=10,
        max_pages=10,
    )

    bodies = "\n".join(str(page.get("text_content", {}).get("body") or "") for page in draft)
    headlines = "\n".join(str(page.get("text_content", {}).get("headline") or "") for page in draft)
    assert len(draft) == 10
    assert all(page["generation_status"] == "source_draft" for page in draft)
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in bodies
    assert "不被 AI 推荐约等于不存在" in bodies
    assert "零售" in headlines or "零售" in bodies
    assert "系统会继续根据 Brief" not in bodies


def test_page_map_markdown_parses_and_structures_to_content_plan():
    markdown = """P1｜cover｜封面｜面向AI时代，企业营与销该如何布局
备注：开场定调，说明这不是工具课。
视觉：课程主视觉

P2｜content｜道｜为什么必须变
- ChatGPT 达到 1 亿规模只用了 2 个月
- 消费者问 AI，AI 替他做选择
备注：把速度和行为迁移连起来。
视觉：速度对比图
来源：模块一"""

    page_map = parse_page_map_markdown(markdown)
    outline = content_plan_from_page_map(page_map)

    assert len(outline) == 2
    assert outline[0]["type"] == "cover"
    assert outline[0]["text_content"]["body"] == ""
    assert outline[1]["type"] == "ending"
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in outline[1]["text_content"]["body"]
    assert "把速度和行为迁移连起来" in outline[1]["speaker_notes"]


def test_generate_content_plan_uses_model_page_map_before_json(monkeypatch):
    class FakeMessage:
        content = """P1｜cover｜封面｜品牌增长课
备注：封面开场。

P2｜content｜背景｜增长为什么变难
- 流量红利变薄
- 用户决策链路变长
备注：解释经营问题。

P3｜ending｜总结｜下一步
- 回到增长动作
备注：收束。"""

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "逐页内容地图" in prompt
            assert "不要输出 JSON" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="做一份 3 页品牌增长课",
        documents="增长材料",
        page_count=3,
    )

    assert [page["page_num"] for page in outline] == [1, 2, 3]
    assert outline[1]["text_content"]["headline"] == "增长为什么变难"
    assert "流量红利变薄" in outline[1]["text_content"]["body"]
    assert outline[1]["generation_status"] == "page_map_model"


def test_generate_content_plan_trims_page_map_to_strict_requested_count(monkeypatch):
    page_map = "\n\n".join(
        [
            "P1｜cover｜封面｜夏日水果和甜品大探险\n备注：开场。\n视觉：封面主视觉",
            *[
                f"P{idx}｜content｜内容｜第 {idx} 页主题\n- 具体内容 {idx}\n备注：讲解第 {idx} 页。\n视觉：内容画面 {idx}"
                for idx in range(2, 16)
            ],
            "P16｜ending｜结束｜甜品冰箱贴完成啦\n- 展示大家做好的甜品冰箱贴\n备注：收束并表扬孩子。\n视觉：甜品冰箱贴作品墙",
        ]
    )

    class FakeMessage:
        content = page_map

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "逐页内容地图" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(content_plan_module.get_knowledge_augmenter(), "augment", lambda *_args, **_kwargs: "")

    outline = generate_content_plan(
        topic="我只要 8 页，最后的手工是甜品冰箱贴",
        page_count=8,
    )

    assert len(outline) == 8
    assert [page["page_num"] for page in outline] == list(range(1, 9))
    assert outline[0]["type"] == "cover"
    assert outline[-1]["type"] == "ending"
    assert "甜品冰箱贴" in outline[-1]["text_content"]["headline"]


def test_page_map_falls_back_to_source_draft_when_model_fails(monkeypatch):
    documents = """# 面向AI时代，企业营与销该如何布局

## 模块一：道

- ChatGPT 达到 1 亿规模只用了 2 个月
- AI 正在成为消费决策的新中介
"""

    class FakeCompletions:
        def create(self, **_kwargs):
            raise TimeoutError("timeout")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    page_map = generate_content_page_map(
        topic="做成 10 页课程",
        documents=documents,
        page_count=10,
    )

    rendered = "\n".join(
        "\n".join(str(item) for item in (page.get("bullets") or []))
        for page in page_map
    )
    assert len(page_map) == 10
    assert "ChatGPT 达到 1 亿规模只用了 2 个月" in rendered
    assert all(page["generation_status"] == "page_map_source" for page in page_map)


def test_generate_deck_blueprint_uses_global_page_ranges(monkeypatch):
    class FakeMessage:
        content = "## 全局蓝图\n- P1-P4：开场\n- P5-P80：主体与收束"

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "60-80 页" in prompt
            assert "P1-P80" in prompt
            assert "只输出可读的中文 Markdown 蓝图" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    blueprint = _generate_deck_blueprint(
        topic="做成 60-80 页课程",
        audience="企业老板",
        documents="课程材料",
        min_pages=60,
        max_pages=80,
        target_count=80,
    )

    assert "P1-P4" in blueprint


def test_short_explicit_range_outline_is_extended(monkeypatch):
    class FakeMessage:
        content = json.dumps([
            {
                "page_num": 2,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": "补充一", "subhead": "", "body": "扩展讲解"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 3,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": "补充二", "subhead": "", "body": "扩展案例"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 4,
                "type": "ending",
                "section_title": "",
                "text_content": {"headline": "收束", "subhead": "", "body": ""},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
        ])

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "只续写第 2 页到第 4 页" in prompt
            assert "【全局蓝图（必须遵守）】" in prompt
            assert "P1-P4：整体课程结构" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    outline = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "",
            "text_content": {"headline": "课程封面", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
        {
            "page_num": 2,
            "type": "ending",
            "section_title": "",
            "text_content": {"headline": "旧结尾", "subhead": "", "body": ""},
            "speaker_notes": "",
            "visual_suggestion": "",
            "source_refs": [],
        },
    ]

    extended = _extend_outline_to_target_count(
        outline,
        topic="做成 4 页课程",
        documents="课程材料",
        deck_blueprint="P1-P4：整体课程结构",
        target_count=4,
        min_pages=4,
        max_pages=4,
    )

    assert [page["page_num"] for page in extended] == [1, 2, 3, 4]
    assert extended[-1]["type"] == "ending"
    assert extended[1]["text_content"]["headline"] == "补充一"


def test_long_deck_outline_generates_in_blueprint_chunks(monkeypatch):
    calls: list[str] = []

    def make_pages(start: int, end: int):
        pages = []
        for page_num in range(start, end + 1):
            pages.append({
                "page_num": page_num,
                "type": "content",
                "section_title": "课程主体",
                "text_content": {"headline": f"第 {page_num} 页", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            })
        return pages

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        def create(self, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            calls.append(prompt)
            assert kwargs["timeout"] == 90.0
            if len(calls) == 1:
                assert "只生成第 1 页到第 12 页" in prompt
                return FakeResponse(json.dumps(make_pages(1, 12)))
            assert "只生成第 13 页到第 14 页" in prompt
            assert "【已生成页面摘要】" in prompt
            return FakeResponse(json.dumps(make_pages(13, 14)))

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    outline = _generate_outline_from_blueprint_in_chunks(
        topic="做成 14 页课程",
        documents="课程材料",
        deck_blueprint="P1-P14：整体课程结构",
        target_count=14,
        min_pages=14,
        max_pages=14,
    )

    assert len(outline) == 14
    assert [page["page_num"] for page in outline] == list(range(1, 15))
    assert outline[0]["type"] == "cover"
    assert outline[-1]["type"] == "ending"
    assert len(calls) == 2


def test_long_deck_single_chunk_normalizes_page_numbers(monkeypatch):
    class FakeMessage:
        content = json.dumps([
            {
                "page_num": 99,
                "type": "ending",
                "section_title": "课程主体",
                "text_content": {"headline": "第一组", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
            {
                "page_num": 100,
                "type": "ending",
                "section_title": "课程主体",
                "text_content": {"headline": "第二组", "subhead": "", "body": "讲解内容"},
                "speaker_notes": "",
                "visual_suggestion": "",
                "source_refs": [],
            },
        ])

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["timeout"] == 60.0
            prompt = kwargs["messages"][1]["content"]
            assert "只生成第 13 页到第 14 页" in prompt
            assert "【本组已有骨架】" in prompt
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    monkeypatch.setattr(content_plan_module, "get_llm_client", lambda: FakeClient())

    chunk = generate_long_deck_outline_chunk(
        topic="做成 14 页课程",
        documents="课程材料",
        deck_blueprint="P1-P14：整体课程结构",
        existing_outline=[],
        skeleton_chunk=build_long_deck_skeleton(topic="课程", target_count=14, min_pages=14, max_pages=14)[12:14],
        target_count=14,
        start_page=13,
        end_page=14,
    )

    assert [page["page_num"] for page in chunk] == [13, 14]
    assert chunk[0]["type"] == "content"
    assert chunk[1]["type"] == "ending"
    assert all(page["generation_status"] == "drafted" for page in chunk)


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
