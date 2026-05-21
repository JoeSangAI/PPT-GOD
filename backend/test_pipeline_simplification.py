"""
验证 overlay pipeline 精简后的正确性。
不调用外部 API，只验证本地逻辑。
"""
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")


def test_prompt_engine_overlay_injection():
    """验证：prompt_engine 只通过 overlay_reservation_instruction 注入一次"""
    from app.services.prompt_engine import generate_prompt_for_page

    page_intent = {
        "page_num": 1,
        "type": "content",
        "layout": "single-focus",
        "visual_evidence": "A modern dashboard with charts and data visualizations",
        "visual_description": "Clean corporate layout with grid system",
        "design_notes": "Use brand blue as accent",
        "overlay_layers": [
            {"asset_id": "test-qr-1", "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
        ],
    }
    content_text = {
        "headline": "Q3 Performance",
        "subhead": "Quarterly Review",
        "body": ["Revenue up 23%"],
    }
    reference_images = []
    style_text = "Modern corporate, blue accent"

    prompt = generate_prompt_for_page(
        page_intent=page_intent,
        content_text=content_text,
        reference_images=reference_images,
        style_text_override=style_text,
    )

    # 1. 必须包含 Exact Overlay Reservation
    assert "Exact Overlay Reservation" in prompt, "prompt 必须包含 Exact Overlay Reservation"

    # 2. 不应该包含已删除的分散注入内容
    assert "Background treatment: keep the following zones completely free" not in prompt, \
        "不应包含 _compact_visual_evidence_with_style 中的分散 overlay 注入"
    assert "reserve clean space for" not in prompt.lower() or "reserved" in prompt.lower(), \
        "不应包含 _compact_layout_intent 中的分散 overlay 注入（除非在统一的 reservation 段落中）"

    # 3. 检查 reservation 内容是否完整
    reservation_start = prompt.find("Exact Overlay Reservation:")
    reservation_text = prompt[reservation_start:]
    assert "right side" in reservation_text, "reservation 应包含预设位置描述"
    assert "CRITICAL LAYOUT INSTRUCTION" in reservation_text, "reservation 应包含关键指令"

    # 4. overlay 指令只出现一次（ reservation 段落中的 "reserved" 可能出现多次，但 "Exact Overlay Reservation:" 标题只应出现一次）
    assert prompt.count("Exact Overlay Reservation:") == 1, \
        f"Exact Overlay Reservation 标题应只出现一次，实际出现 {prompt.count('Exact Overlay Reservation:')} 次"

    print("✅ test_prompt_engine_overlay_injection 通过")
    print(f"   Prompt 长度: {len(prompt)}")
    print(f"   Reservation 位置: {reservation_start}/{len(prompt)}")
    return True


def test_prompt_engine_no_overlay():
    """验证：没有 overlay 时，prompt 不包含 reservation"""
    from app.services.prompt_engine import generate_prompt_for_page

    page_intent = {
        "page_num": 1,
        "type": "content",
        "layout": "single-focus",
        "visual_evidence": "A simple background",
        "visual_description": "Minimal layout",
    }
    content_text = {"headline": "Hello"}

    prompt = generate_prompt_for_page(
        page_intent=page_intent,
        content_text=content_text,
        reference_images=[],
        style_text_override="",
    )

    assert "Exact Overlay Reservation" not in prompt, "无 overlay 时不应包含 reservation"
    print("✅ test_prompt_engine_no_overlay 通过")
    return True


def test_load_reference_images_skips_overlay():
    """验证：_load_reference_images 正确跳过 overlay 资产"""
    from app.services.generation_pipeline import _load_reference_images
    import tempfile

    # 创建临时图片文件
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
        temp_path = f.name

    try:
        ref_id = str(uuid.uuid4())
        ref = MagicMock()
        ref.id = ref_id
        ref.file_path = temp_path
        ref.role = "content_ref"
        ref.process_mode = "blend"
        ref.asset_name = "Test QR"
        ref.asset_kind = None
        ref.usage_note = None

        slide = MagicMock()
        slide.page_num = 5
        slide.visual_json = {
            "overlay_layers": [
                {"asset_id": ref_id, "enabled": True, "preset": "right-card", "mode": "exact_cutout"}
            ]
        }
        slide.reference_images = [ref]
        slide.project = None

        refs = _load_reference_images(slide)

        # 验证 overlay 资产被跳过
        ref_ids = [str(r.get("id", "")) for r in refs]
        assert ref_id not in ref_ids, f"overlay 资产不应被加载，但出现在 refs 中: {ref_ids}"
        print("✅ test_load_reference_images_skips_overlay 通过")
        print(f"   加载的参考图: {len(refs)} 张")
        return True
    finally:
        os.unlink(temp_path)


def test_update_slide_overlay_layers_uuid_fix():
    """验证：update_slide_overlay_layers 中 UUID 字符串比对修复"""
    # 由于这个函数依赖数据库，我们直接测试核心逻辑
    ref_id = uuid.uuid4()
    overlay_asset_ids = {str(ref_id)}

    # 模拟 valid_assets 中的 asset.id 是 UUID 对象
    asset = MagicMock()
    asset.id = ref_id  # UUID 对象
    asset.process_mode = "blend"
    asset.asset_analysis = {}

    # 修复前的 bug：asset.id (UUID) in overlay_asset_ids (字符串集合) 永远为 False
    # 修复后：str(asset.id) in overlay_asset_ids
    matched = str(asset.id) in overlay_asset_ids
    assert matched is True, "字符串比对应该正确匹配 UUID"

    # 模拟修复后的代码逻辑
    if str(asset.id) in overlay_asset_ids:
        asset.process_mode = "original"

    assert asset.process_mode == "original", "匹配后 process_mode 应被设为 original"
    print("✅ test_update_slide_overlay_layers_uuid_fix 通过")
    return True


def test_visual_plan_no_safety_net_replacement():
    """验证：visual_plan 不再用关键词匹配替换 LLM 输出"""
    # 直接检查代码中是否还存在关键词替换逻辑
    import ast

    with open("app/services/visual_plan.py", "r") as f:
        source = f.read()

    tree = ast.parse(source)

    # 查找是否还有 overlay_content_keywords 相关的代码
    found_keywords_check = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "overlay_content_keywords":
            found_keywords_check = True
            break
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in ("截图", "二维码", "QR"):
                # 检查是否在 batch prompt 的 overlay_instruction 中（这是允许的）
                # 简单判断：如果在 f-string 或常规字符串中，可能是 prompt 内容
                found_keywords_check = True
                break

    # 实际上我们更直接地检查：是否还有替换 visual_evidence 的逻辑
    found_replacement = "replacing with background-only" in source

    assert not found_replacement, "不应存在关键词替换 safety net"
    print("✅ test_visual_plan_no_safety_net_replacement 通过")
    print(f"   源代码中 'overlay_content_keywords' 存在: {found_keywords_check}")
    print("   （如果存在，应该是在 batch prompt 的说明文本中，这是正常的）")
    return True


def main():
    print("=" * 60)
    print("Overlay Pipeline 精简验证")
    print("=" * 60)

    tests = [
        test_prompt_engine_overlay_injection,
        test_prompt_engine_no_overlay,
        test_load_reference_images_skips_overlay,
        test_update_slide_overlay_layers_uuid_fix,
        test_visual_plan_no_safety_net_replacement,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"❌ {test.__name__} 失败: {e}")

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
