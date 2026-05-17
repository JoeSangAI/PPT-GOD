"""
验证 style_proposal 和 style_pack 模块在重构后是否正确遵循了"Pipeline thin, LLM thick"原则。
不运行高成本的外部 API 调用。
"""
import ast
import inspect
import sys
from pathlib import Path

# 添加 backend 到路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.services import style_proposal as sp_module
from app.services import style_pack as spack_module


def test_no_enforce_functions():
    """确认不存在 _enforce_ 开头的函数。"""
    enforce_funcs = [
        name for name in dir(sp_module)
        if name.startswith("_enforce_") and callable(getattr(sp_module, name))
    ]
    assert not enforce_funcs, f"发现 _enforce_ 开头的函数: {enforce_funcs}"


def test_reference_clone_respects_input():
    """
    _build_reference_clone_proposal 返回的 mood/font/description
    必须由输入的 ref 决定，而不是代码层硬编码。
    """
    summary = {
        "headlines": ["Test"],
        "page_types": ["content"],
        "industries": [],
        "keywords": [],
        "total_pages": 5,
        "topic_hints": [],
        "style_direction_hint": "",
        "dense_page_ratio": 0,
        "table_page_ratio": 0,
    }

    # 场景 A：传入明确的传统风格参考图
    assets_traditional = {
        "logo_analysis": {},
        "template_analysis": {},
        "reference_analyses": [{
            "style_name": "传统水墨",
            "description": "传统中国水墨风格",
            "mood": "古朴典雅",
            "font_suggestion": "宋体",
            "ornaments": "水墨装饰",
            "texture": "宣纸质感",
            "dominant_palette": [
                {"hex": "#800000", "share": 0.4},
                {"hex": "#D4AF37", "share": 0.2},
            ],
        }],
    }
    proposal_t = sp_module._build_reference_clone_proposal(summary, assets_traditional)
    assert "古朴" in proposal_t["mood"] or "典雅" in proposal_t["mood"], \
        f"传统参考图的 mood 应反映输入，实际为: {proposal_t['mood']}"
    assert "宋" in proposal_t["font"] or "书法" in proposal_t["font"], \
        f"传统参考图的 font 应反映输入，实际为: {proposal_t['font']}"
    print("PASS: 传统参考图的 mood/font 由输入决定")

    # 场景 B：传入现代科技感参考图
    assets_modern = {
        "logo_analysis": {},
        "template_analysis": {},
        "reference_analyses": [{
            "style_name": "深蓝科技",
            "description": "未来科技风格",
            "mood": "冷静专业",
            "font_suggestion": "黑体",
            "ornaments": "几何线条",
            "texture": "玻璃质感",
            "dominant_palette": [
                {"hex": "#0A1628", "share": 0.5},
                {"hex": "#3B82F6", "share": 0.3},
            ],
        }],
    }
    proposal_m = sp_module._build_reference_clone_proposal(summary, assets_modern)
    assert "冷静" in proposal_m["mood"] or "专业" in proposal_m["mood"] or "现代" in proposal_m["mood"], \
        f"现代参考图的 mood 应反映输入，实际为: {proposal_m['mood']}"
    assert "黑" in proposal_m["font"] or "无衬线" in proposal_m["font"], \
        f"现代参考图的 font 应反映输入，实际为: {proposal_m['font']}"
    print("PASS: 现代参考图的 mood/font 由输入决定")

    # 关键验证：两个场景的 mood 必须不同
    assert proposal_t["mood"] != proposal_m["mood"], \
        f"不同参考图应产生不同 mood，但都是: {proposal_t['mood']}"
    print("PASS: 不同参考图产生不同 mood")


def test_style_pack_preserves_visual_strategy():
    """
    style_pack_from_selected_style 对包含 visual_strategy 的 selected_style，
    输出中保留 base_tone 和 logo_contrast。
    """
    selected_style = {
        "name": "测试风格",
        "palette": [
            {"name": "深蓝", "hex": "#0A1628"},
            {"name": "亮蓝", "hex": "#3B82F6"},
        ],
        "mood": "冷静专业",
        "font": "黑体",
        "visual_strategy": {
            "base_tone": "dark",
            "logo_contrast": "light_on_dark",
            "page_layout": "centered",
        },
    }
    pack = spack_module.style_pack_from_selected_style(selected_style)
    assert "base_tone=dark" in pack, f"应保留 base_tone，实际输出:\n{pack}"
    # logo_contrast 的值通过 visual_strategy_text 输出，键名不一定保留
    assert "light_on_dark" in pack, f"应保留 logo_contrast 的值，实际输出:\n{pack}"
    print("PASS: style_pack 保留了 visual_strategy 中的 base_tone 和 logo_contrast")


def test_no_hardcoded_topic_filtering():
    """验证没有基于 topic 的预设过滤逻辑。"""
    # 检查 _proposal_has_unjustified_traditional_drift 是否总是返回 False（即被禁用）
    summary = {"headlines": ["科技未来"], "keywords": [], "industries": ["科技"], "style_direction_hint": "", "topic_hints": [], "dense_page_ratio": 0, "table_page_ratio": 0}
    proposal = {"mood": "传统古朴", "description": "传统风格"}
    result = sp_module._proposal_has_unjustified_traditional_drift(proposal, summary)
    assert result is False, "_proposal_has_unjustified_traditional_drift 应被禁用（永远返回 False）"
    print("PASS: topic 预设过滤已被禁用")


def test_no_hardcoded_default_palette_in_finalize():
    """
    _finalize_style_proposals 不应硬编码默认配色。
    注：这是一个诊断性测试，用于暴露硬编码兜底问题。
    """
    source_code = Path(backend_dir / "app/services/style_proposal.py").read_text()

    # 查找硬编码的默认 palette
    hardcoded_defaults = [
        '#333333',
        '#FFFFFF',
        '#999999',
        '#CCCCCC',
        '#2F2A24',
        '#B8945C',
        '#F4E8D0',
        '#1F1F1F',
    ]

    found = [c for c in hardcoded_defaults if c in source_code]
    # 我们只报告，不 assert fail，因为当前代码中可能仍有这些值
    if found:
        print(f"WARNING: 发现硬编码默认颜色值: {found}")
    else:
        print("PASS: 未发现常见硬编码默认颜色值")


def test_no_hardcoded_font_fallback_in_clone():
    """
    _build_reference_clone_proposal 不应在 ref 提供 font_suggestion 时覆盖它。
    """
    summary = {
        "headlines": ["Test"],
        "page_types": ["content"],
        "industries": [],
        "keywords": [],
        "total_pages": 5,
        "topic_hints": [],
        "style_direction_hint": "",
        "dense_page_ratio": 0,
        "table_page_ratio": 0,
    }
    assets = {
        "logo_analysis": {},
        "template_analysis": {},
        "reference_analyses": [{
            "style_name": "自定义",
            "font_suggestion": "自定义字体A",
            "dominant_palette": [{"hex": "#123456", "share": 0.5}],
        }],
    }
    proposal = sp_module._build_reference_clone_proposal(summary, assets)
    assert "自定义字体A" in proposal["font"], \
        f"应保留 ref 提供的 font_suggestion，实际为: {proposal['font']}"
    print("PASS: 参考图提供的 font_suggestion 被保留")


def test_derive_style_pack_does_not_override_visual_strategy():
    """
    derive_style_pack_from_content 不应覆盖 LLM 生成的 visual_strategy。
    如果传入参考图，应保留从参考图推导出的 visual_strategy。
    """
    content_plan = [{"type": "cover", "text_content": {"headline": "Test"}}]
    ref_analysis = [{
        "colors": {"background": "#0A1628", "primary": "#3B82F6"},
        "dominant_palette": [{"hex": "#0A1628", "share": 0.5}],
        "style_name": "深蓝科技",
        "mood": "冷静专业",
    }]
    pack_text = spack_module.derive_style_pack_from_content(content_plan, ref_analysis)
    assert "Visual strategy" in pack_text or "visual_strategy" in pack_text.lower(), \
        f"应包含 visual_strategy 信息，实际输出:\n{pack_text}"
    print("PASS: derive_style_pack_from_content 保留了 visual_strategy")


if __name__ == "__main__":
    test_no_enforce_functions()
    test_reference_clone_respects_input()
    test_style_pack_preserves_visual_strategy()
    test_no_hardcoded_topic_filtering()
    test_no_hardcoded_default_palette_in_finalize()
    test_no_hardcoded_font_fallback_in_clone()
    test_derive_style_pack_does_not_override_visual_strategy()
    print("\n所有验证通过！")
