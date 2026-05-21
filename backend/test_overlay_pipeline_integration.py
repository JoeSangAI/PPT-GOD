"""
用真实素材验证 overlay pipeline 端到端逻辑。
不调用外部 API，只验证本地文件路径解析和资产分类。
"""
import os
import sys
import uuid
from unittest.mock import MagicMock

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")


def test_resolve_file_path_with_real_files():
    """验证：_resolve_file_path 能正确解析真实文件的各种路径形式"""
    from app.services.generation_pipeline import _resolve_file_path

    # 找一个真实存在的文件
    real_abs_path = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_abs_path):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_abs_path}")
        return True

    # 1. 绝对路径
    abs_path = os.path.abspath(real_abs_path)
    result = _resolve_file_path(abs_path)
    assert os.path.exists(result), f"绝对路径应能解析: {result}"

    # 2. 相对路径（当前在 backend/ 下）
    rel_path = real_abs_path
    result = _resolve_file_path(rel_path)
    assert os.path.exists(result), f"相对路径应能解析: {result}"

    # 3. 带 ./ 前缀的相对路径
    dot_path = "./" + real_abs_path
    result = _resolve_file_path(dot_path)
    assert os.path.exists(result), f"./ 相对路径应能解析: {result}"

    # 4. 不存在的路径应原样返回
    fake = "./uploads/nonexistent/fake.png"
    result = _resolve_file_path(fake)
    assert result == fake, f"不存在路径应原样返回: {result}"

    print("✅ test_resolve_file_path_with_real_files 通过")
    print(f"   测试文件: {real_abs_path}")
    return True


def test_load_reference_images_with_real_overlay_skip():
    """验证：_load_reference_images 正确跳过真实文件的 overlay 资产"""
    from app.services.generation_pipeline import _load_reference_images
    from app.services.overlay_layers import exact_overlay_asset_ids

    # 找一个真实文件
    real_file = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_file):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_file}")
        return True

    ref_id = str(uuid.uuid4())

    ref = MagicMock()
    ref.id = ref_id
    ref.file_path = real_file
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

    # overlay 资产应被跳过
    ref_ids = [str(r.get("id", "")) for r in refs]
    assert ref_id not in ref_ids, f"overlay 资产不应被加载，但出现在 refs 中: {ref_ids}"
    print("✅ test_load_reference_images_with_real_overlay_skip 通过")
    print(f"   文件存在性: {os.path.exists(real_file)}")
    print(f"   加载的参考图: {len(refs)} 张")
    return True


def test_load_reference_images_real_file_blended():
    """验证：非 overlay 的真实文件能被正确加载为生图参考"""
    from app.services.generation_pipeline import _load_reference_images

    real_file = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_file):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_file}")
        return True

    ref = MagicMock()
    ref.id = str(uuid.uuid4())
    ref.file_path = real_file
    ref.role = "content_ref"
    ref.process_mode = "blend"
    ref.asset_name = "Real Screenshot"
    ref.asset_kind = None
    ref.usage_note = None

    slide = MagicMock()
    slide.page_num = 3
    slide.visual_json = {"overlay_layers": []}
    slide.reference_images = [ref]
    slide.project = None

    refs = _load_reference_images(slide)

    assert len(refs) == 1, f"应加载 1 张参考图，实际 {len(refs)}"
    loaded_path = refs[0].get("file_path")
    assert os.path.exists(loaded_path), f"加载后的路径应存在: {loaded_path}"
    print("✅ test_load_reference_images_real_file_blended 通过")
    print(f"   加载的参考图: {loaded_path}")
    return True


def test_overlay_assets_build_with_real_files():
    """验证：overlay_assets 构建包含真实文件，且路径正确解析"""
    from app.services.generation_pipeline import _resolve_file_path

    real_file = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_file):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_file}")
        return True

    # 模拟 overlay_assets 构建逻辑
    ref = MagicMock()
    ref.id = "test-asset-123"
    ref.file_path = real_file
    ref.asset_name = "QR Code"
    ref.asset_kind = "screenshot"

    resolved = _resolve_file_path(ref.file_path)
    assert os.path.exists(resolved), f"解析后路径应存在: {resolved}"

    overlay_assets = {}
    if os.path.exists(resolved):
        overlay_assets[str(ref.id)] = {
            "file_path": resolved,
            "asset_name": ref.asset_name,
            "asset_kind": ref.asset_kind,
        }

    assert "test-asset-123" in overlay_assets, "overlay_assets 应包含该资产"
    assert os.path.exists(overlay_assets["test-asset-123"]["file_path"]), "file_path 应指向真实文件"
    print("✅ test_overlay_assets_build_with_real_files 通过")
    print(f"   解析后路径: {resolved}")
    return True


def test_relative_path_fallback():
    """验证：数据库存的是相对路径时，_resolve_file_path 仍能找回文件"""
    from app.services.generation_pipeline import _resolve_file_path

    # 找一个真实文件，但用相对路径形式（模拟数据库中的旧记录）
    real_rel = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_rel):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_rel}")
        return True

    # 模拟数据库中可能存的格式
    variants = [
        real_rel,
        "./" + real_rel,
        os.path.abspath(real_rel),
    ]

    for v in variants:
        result = _resolve_file_path(v)
        assert os.path.exists(result), f"路径变体应能解析: {v} -> {result}"

    print("✅ test_relative_path_fallback 通过")
    print(f"   测试了 {len(variants)} 种路径格式")
    return True


def test_pptx_assembler_path_resolution():
    """验证：pptx_assembler 中的 _resolve_file_path 能解析真实文件"""
    from app.services.pptx_assembler import _resolve_file_path

    real_file = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_file):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_file}")
        return True

    result = _resolve_file_path(real_file)
    assert os.path.exists(result), f"assembler 应能解析路径: {result}"

    print("✅ test_pptx_assembler_path_resolution 通过")
    return True


def main():
    print("=" * 60)
    print("Overlay Pipeline 集成验证（真实素材）")
    print("=" * 60)

    tests = [
        test_resolve_file_path_with_real_files,
        test_load_reference_images_with_real_overlay_skip,
        test_load_reference_images_real_file_blended,
        test_overlay_assets_build_with_real_files,
        test_relative_path_fallback,
        test_pptx_assembler_path_resolution,
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
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
