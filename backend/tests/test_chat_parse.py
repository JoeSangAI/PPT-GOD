"""
测试 chat.py 中的 _try_parse JSON 解析函数。

由于 _try_parse 是嵌套在 _stream_intent 内部的函数，无法直接导入，
这里复制其核心逻辑进行单元测试。
"""

import json
import re

import json_repair
import pytest


def _try_parse(text: str):
    """从 chat.py 复制的核心 JSON 解析逻辑。"""
    text = text.strip()
    # 去掉 think 标签（可能被截断或不完整）
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()

    # 1. 优先用 json_repair 自动修复 LLM 常见的 JSON 错误
    try:
        return json_repair.loads(text)
    except Exception:
        pass

    # 2. 提取第一个 JSON 对象/数组后再次尝试 json_repair
    start_obj = text.find("{")
    start_arr = text.find("[")
    start = start_obj if start_obj != -1 and (start_arr == -1 or start_obj < start_arr) else start_arr
    if start != -1:
        end = text.rfind("}") if text[start] == "{" else text.rfind("]")
        if end != -1 and end > start:
            snippet = text[start:end + 1]
            try:
                return json_repair.loads(snippet)
            except Exception:
                pass

    # 兜底：返回 None
    return None


class TestTryParse:
    """测试 _try_parse 对各种输入的处理。"""

    def test_normal_json_object(self):
        """正常 JSON 对象应直接解析。"""
        raw = '{"action": "diagnose", "response": "你好"}'
        result = _try_parse(raw)
        assert result == {"action": "diagnose", "response": "你好"}

    def test_normal_json_array(self):
        """正常 JSON 数组应直接解析。"""
        raw = '[{"page": 1}, {"page": 2}]'
        result = _try_parse(raw)
        assert result == [{"page": 1}, {"page": 2}]

    def test_json_with_think_tags(self):
        """think 标签包裹的 JSON 应被清理后解析。"""
        raw = '<think>让我想想</think>{"action": "collect_content", "response": "OK"}'
        result = _try_parse(raw)
        assert result == {"action": "collect_content", "response": "OK"}

    def test_json_with_think_tags_multiline(self):
        """多行 think 标签应被完整清理。"""
        raw = """<think>
这里有一些思考过程
多行内容
</think>
{"action": "propose_plan", "response": "准备好了"}"""
        result = _try_parse(raw)
        assert result == {"action": "propose_plan", "response": "准备好了"}

    def test_json_in_markdown_code_block_with_json_label(self):
        """```json 标记的 markdown 代码块应被正确解析。"""
        raw = '```json\n{"action": "answer", "response": "收到"}\n```'
        result = _try_parse(raw)
        assert result == {"action": "answer", "response": "收到"}

    def test_json_in_plain_markdown_code_block(self):
        """无 json 标记的 markdown 代码块也应被正确解析。"""
        raw = '```\n{"action": "answer", "response": "收到"}\n```'
        result = _try_parse(raw)
        assert result == {"action": "answer", "response": "收到"}

    def test_broken_json_trailing_comma(self):
        """trailing comma 的破损 JSON 应被 json_repair 修复。"""
        raw = '{"action": "diagnose", "response": "你好",}'
        result = _try_parse(raw)
        assert result == {"action": "diagnose", "response": "你好"}

    def test_broken_json_unescaped_quotes(self):
        """字符串内未转义的双引号应被 json_repair 修复。"""
        raw = '{"text": "增加了"爱"这一维度"}'
        result = _try_parse(raw)
        assert result == {"text": '增加了"爱"这一维度'}

    def test_truncated_json(self):
        """截断的 JSON（缺少闭合括号）应被 json_repair 修复。"""
        raw = '{"action": "collect_content", "response": "还没写完"'
        result = _try_parse(raw)
        assert result == {"action": "collect_content", "response": "还没写完"}

    def test_json_with_extra_text_before(self):
        """JSON 前面有额外文本时，应提取 JSON 部分解析。"""
        raw = '这里是一些说明文字 {"action": "answer", "response": "OK"}'
        result = _try_parse(raw)
        assert result == {"action": "answer", "response": "OK"}

    def test_json_with_extra_text_after(self):
        """JSON 后面有额外文本时，应提取 JSON 部分解析。"""
        raw = '{"action": "answer", "response": "OK"} 后面还有一些内容'
        result = _try_parse(raw)
        assert result == {"action": "answer", "response": "OK"}

    def test_completely_non_json_text(self):
        """完全非 JSON 内容应返回空值（json_repair 对无法解析的文本返回空字符串）。"""
        raw = "这只是一个普通的句子，没有任何 JSON。"
        result = _try_parse(raw)
        assert result == ""

    def test_empty_string(self):
        """空字符串应返回空值。"""
        result = _try_parse("")
        assert result == ""

    def test_whitespace_only(self):
        """只有空白字符应返回空值。"""
        result = _try_parse("   \n\t  ")
        assert result == ""

    def test_nested_json_in_think_and_markdown(self):
        """think 标签 + markdown 代码块双重包裹应被正确处理。"""
        raw = '<think>思考中...</think>\n```json\n{"action": "generate_plan", "topic": "测试"}\n```'
        result = _try_parse(raw)
        assert result == {"action": "generate_plan", "topic": "测试"}

    def test_broken_json_multiple_errors(self):
        """同时包含 trailing comma 和未转义引号的 JSON 应被修复。"""
        raw = '{"action": "diagnose", "response": "他说"好的"",}'
        result = _try_parse(raw)
        assert result == {"action": "diagnose", "response": '他说"好的"'}
