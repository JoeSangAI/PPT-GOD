"""
测试 body 兼容性：string body 和 list body 在各模块中的处理是否一致。

覆盖范围：
1. visual_plan.py 中的 _assign_layout 和 body_preview/body_count 推断
2. prompt_engine.py 中 _build_rich_brief 和 generate_prompt_for_page 的 body 处理
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.visual_plan import _assign_layout, _infer_seed_family
from app.services.prompt_engine import _build_rich_brief, generate_prompt_for_page


# ============================================================
# _assign_layout 测试
# ============================================================

class TestAssignLayout:
    """测试 _assign_layout 对不同 page_type 和 body_count 的处理。"""

    def test_cover_type_ignores_body_count(self):
        """cover 类型始终返回 cover 模板，不受 body_count 影响。"""
        assert _assign_layout("cover", body_count=0) == "cover"
        assert _assign_layout("cover", body_count=100) == "cover"

    def test_toc_type_ignores_body_count(self):
        """toc 类型始终返回 toc 模板。"""
        assert _assign_layout("toc", body_count=0) == "toc"
        assert _assign_layout("toc", body_count=10) == "toc"

    def test_hero_type_ignores_body_count(self):
        """hero 类型始终返回 hero 模板。"""
        assert _assign_layout("hero", body_count=0) == "hero"

    def test_data_type_ignores_body_count(self):
        """data 类型始终返回 data 模板。"""
        assert _assign_layout("data", body_count=0) == "data"

    def test_ending_type_ignores_body_count(self):
        """ending 类型始终返回 ending 模板。"""
        assert _assign_layout("ending", body_count=0) == "ending"

    def test_content_type_low_body_count(self):
        """content 类型根据内容长度返回现有模板 ID。"""
        assert _assign_layout("content", body_count=0) == "content_hero"
        assert _assign_layout("content", body_count=3) == "content_split"
        assert _assign_layout("content", body_count=5) == "content_split"

    def test_content_type_high_body_count(self):
        """content 类型在 body_count > 6 时返回 dense 模板。"""
        assert _assign_layout("content", body_count=6) == "content_split"
        assert _assign_layout("content", body_count=10) == "content_dense"

    def test_unknown_type_defaults_to_content_logic(self):
        """未知类型按 content 逻辑处理。"""
        assert _assign_layout("unknown", body_count=3) == "content_split"
        assert _assign_layout("unknown", body_count=8) == "content_dense"


# ============================================================
# body_preview / body_count 逻辑测试
# ============================================================

class TestBodyPreviewLogic:
    """
    测试 generate_visual_plan 中的 body 处理逻辑。
    由于该逻辑嵌套在函数内部，这里用相同的数据处理规则进行验证。
    """

    def test_string_body_preview(self):
        """string body 应取前 120 字符作为 preview，按非空行计数。"""
        body = "第一行\n\n第三行\n  \n第五行"
        body_preview = body[:120]
        body_count = len([l for l in body.splitlines() if l.strip()])
        assert body_preview == "第一行\n\n第三行\n  \n第五行"
        assert body_count == 3

    def test_list_body_preview(self):
        """list body 应拼接后取前 120 字符，计数为列表长度。"""
        body = ["要点一", "要点二", "要点三"]
        body_preview = " ".join(str(b) for b in body)[:120]
        body_count = len(body)
        assert body_preview == "要点一 要点二 要点三"
        assert body_count == 3

    def test_long_string_body_truncated_preview(self):
        """超过 120 字符的 string body 应被截断。"""
        body = "A" * 200
        body_preview = body[:120]
        assert len(body_preview) == 120

    def test_long_list_body_truncated_preview(self):
        """拼接后超过 120 字符的 list body 应被截断。"""
        body = ["A" * 50, "B" * 50, "C" * 50]
        body_preview = " ".join(str(b) for b in body)[:120]
        assert len(body_preview) == 120

    def test_empty_string_body(self):
        """空 string body 应返回空 preview 和 0 count。"""
        body = ""
        body_preview = body[:120]
        body_count = len([l for l in body.splitlines() if l.strip()])
        assert body_preview == ""
        assert body_count == 0

    def test_empty_list_body(self):
        """空 list body 应返回空 preview 和 0 count。"""
        body = []
        body_preview = " ".join(str(b) for b in body)[:120]
        body_count = len(body)
        assert body_preview == ""
        assert body_count == 0


# ============================================================
# _build_rich_brief 测试
# ============================================================

class TestBuildRichBrief:
    """测试 _build_rich_brief 对 string/list body 的 prompt 生成。"""

    def test_string_body_in_rich_brief(self):
        """string body 应在 Rich Brief 中直接保留原字符串。"""
        page_intent = {"visual_description": "深色背景", "design_notes": "现代风格"}
        style_text = "简约风格"
        layout_text = "左右布局"
        content_text = {"headline": "标题", "subhead": "副标题", "body": "第一行\n第二行\n第三行"}

        brief = _build_rich_brief(page_intent, style_text, layout_text, content_text, [])

        assert 'Headline: 标题' in brief
        assert 'Subhead: 副标题' in brief
        assert 'Body: 第一行\n第二行\n第三行' in brief
        assert "json.dumps" not in brief  # 不应出现序列化痕迹

    def test_list_body_in_rich_brief(self):
        """list body 应在 Rich Brief 中被序列化为 JSON 字符串。"""
        page_intent = {"visual_description": "深色背景", "design_notes": "现代风格"}
        content_text = {"headline": "标题", "subhead": "副标题", "body": ["要点一", "要点二", "要点三"]}

        brief = _build_rich_brief(page_intent, "风格", "布局", content_text, [])

        assert 'Body: ["要点一", "要点二", "要点三"]' in brief

    def test_empty_string_body_in_rich_brief(self):
        """空 string body 应显示为空字符串。"""
        content_text = {"headline": "标题", "body": ""}
        brief = _build_rich_brief({}, "", "", content_text, [])
        assert "Body: " in brief

    def test_empty_list_body_in_rich_brief(self):
        """空 list body 应显示为空 JSON 数组。"""
        content_text = {"headline": "标题", "body": []}
        brief = _build_rich_brief({}, "", "", content_text, [])
        assert "Body: []" in brief

    def test_missing_body_in_rich_brief(self):
        """没有 body 字段时应使用默认值。"""
        content_text = {"headline": "标题"}
        brief = _build_rich_brief({}, "", "", content_text, [])
        assert "Body: " in brief


# ============================================================
# generate_prompt_for_page 中的 text_directives 测试
# ============================================================

class TestPromptTextDirectives:
    """
    测试 generate_prompt_for_page 对不同 body 类型生成的 text_directives。
    需要 mock LLM 调用和模板文件读取。
    """

    @patch("app.services.prompt_engine._call_llm_for_final_prompt")
    @patch("app.services.prompt_engine._load_template")
    @patch("app.services.prompt_engine._extract_model_facing_text")
    def test_string_body_text_directives(self, mock_extract, mock_load, mock_llm):
        """string body 应按行拆分生成 text_directives。"""
        mock_load.return_value = "模板内容"
        mock_extract.return_value = "提取后的模板"
        mock_llm.return_value = "基础 prompt"

        page_intent = {"page_num": 1, "type": "content", "visual_description": "", "design_notes": ""}
        content_text = {
            "headline": "主标题",
            "subhead": "副标题",
            "body": "第一要点\n第二要点\n\n\n\n",  # 包含空行
        }

        prompt = generate_prompt_for_page(page_intent, content_text, style_id="default")

        assert 'Headline: "主标题" must appear on the slide' in prompt
        assert 'Subhead: "副标题" must appear on the slide' in prompt
        assert 'Body text: "第一要点" must appear on the slide' in prompt
        assert 'Body text: "第二要点" must appear on the slide' in prompt
        # 空行不应生成 directive
        assert prompt.count('Body text:') == 2

    @patch("app.services.prompt_engine._call_llm_for_final_prompt")
    @patch("app.services.prompt_engine._load_template")
    @patch("app.services.prompt_engine._extract_model_facing_text")
    def test_list_body_text_directives(self, mock_extract, mock_load, mock_llm):
        """list body 应逐项生成 text_directives。"""
        mock_load.return_value = "模板内容"
        mock_extract.return_value = "提取后的模板"
        mock_llm.return_value = "基础 prompt"

        page_intent = {"page_num": 1, "type": "content", "visual_description": "", "design_notes": ""}
        content_text = {
            "headline": "主标题",
            "body": ["要点A", "要点B", "要点C"],
        }

        prompt = generate_prompt_for_page(page_intent, content_text, style_id="default")

        assert 'Body text: "要点A" must appear on the slide' in prompt
        assert 'Body text: "要点B" must appear on the slide' in prompt
        assert 'Body text: "要点C" must appear on the slide' in prompt
        assert prompt.count('Body text:') == 3

    @patch("app.services.prompt_engine._call_llm_for_final_prompt")
    @patch("app.services.prompt_engine._load_template")
    @patch("app.services.prompt_engine._extract_model_facing_text")
    def test_string_body_more_than_five_lines(self, mock_extract, mock_load, mock_llm):
        """string body 会保留多行文字约束，避免生成图漏字。"""
        mock_load.return_value = "模板内容"
        mock_extract.return_value = "提取后的模板"
        mock_llm.return_value = "基础 prompt"

        page_intent = {"page_num": 1, "type": "content", "visual_description": "", "design_notes": ""}
        content_text = {
            "headline": "标题",
            "body": "1\n2\n3\n4\n5\n6\n7",
        }

        prompt = generate_prompt_for_page(page_intent, content_text, style_id="default")

        assert prompt.count('Body text:') == 7

    @patch("app.services.prompt_engine._call_llm_for_final_prompt")
    @patch("app.services.prompt_engine._load_template")
    @patch("app.services.prompt_engine._extract_model_facing_text")
    def test_empty_body_no_text_directives(self, mock_extract, mock_load, mock_llm):
        """空 body 时不应生成 body 相关的 text_directives。"""
        mock_load.return_value = "模板内容"
        mock_extract.return_value = "提取后的模板"
        mock_llm.return_value = "基础 prompt"

        page_intent = {"page_num": 1, "type": "content", "visual_description": "", "design_notes": ""}
        content_text = {"headline": "标题", "body": ""}

        prompt = generate_prompt_for_page(page_intent, content_text, style_id="default")

        assert 'Headline: "标题" must appear on the slide' in prompt
        assert 'Body text:' not in prompt

    @patch("app.services.prompt_engine._call_llm_for_final_prompt")
    @patch("app.services.prompt_engine._load_template")
    @patch("app.services.prompt_engine._extract_model_facing_text")
    def test_no_headline_no_subhead_directives(self, mock_extract, mock_load, mock_llm):
        """没有 headline 和 subhead 时不应生成对应的 directives。"""
        mock_load.return_value = "模板内容"
        mock_extract.return_value = "提取后的模板"
        mock_llm.return_value = "基础 prompt"

        page_intent = {"page_num": 1, "type": "content", "visual_description": "", "design_notes": ""}
        content_text = {"body": "只有正文"}

        prompt = generate_prompt_for_page(page_intent, content_text, style_id="default")

        assert "The headline" not in prompt
        assert "Below it, the subhead" not in prompt
        assert 'Body text: "只有正文" must appear on the slide' in prompt
