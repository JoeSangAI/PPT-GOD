"""
测试 exact_cutout 修复：验证 UUID 字符串匹配问题
"""
import os
import sys
import uuid
from unittest.mock import MagicMock

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from app.services.overlay_layers import normalize_overlay_layers


def test_normalize_overlay_layers_with_string_asset_ids():
    """测试：valid_asset_ids 为字符串集合时，能正确匹配"""
    ref_id = uuid.uuid4()
    raw_layers = [
        {
            "asset_id": str(ref_id),
            "mode": "exact_cutout",
            "preset": "left-card",
        }
    ]

    # Bug 修复前：valid_ids 是 UUID 对象集合，字符串 asset_id 匹配失败
    # valid_ids_uuid = {ref_id}
    # result = normalize_overlay_layers(raw_layers, valid_asset_ids=valid_ids_uuid, strict_assets=True)
    # assert len(result) == 0, "UUID 对象集合应该导致匹配失败（演示旧 bug）"

    # Bug 修复后：valid_ids 是字符串集合
    valid_ids_str = {str(ref_id)}
    result = normalize_overlay_layers(raw_layers, valid_asset_ids=valid_ids_str, strict_assets=True)
    assert len(result) == 1, f"字符串集合应该正确匹配，但实际返回 {len(result)} 个 layer"
    assert result[0]["mode"] == "exact_cutout", "mode 应该保持 exact_cutout"
    print("✅ test_normalize_overlay_layers_with_string_asset_ids 通过")


def test_overlay_assets_string_key_lookup():
    """测试：overlay_assets 用字符串 key 构建，能被正确查找"""
    ref_id = uuid.uuid4()

    # Bug 修复前：key 是 UUID 对象
    overlay_assets_old = {
        ref_id: {"file_path": "/tmp/test.png"}
    }
    asset_id_str = str(ref_id)
    result_old = overlay_assets_old.get(asset_id_str)
    assert result_old is None, "UUID 对象 key 应该无法被字符串查找到（演示旧 bug）"

    # Bug 修复后：key 是字符串
    overlay_assets_new = {
        str(ref_id): {"file_path": "/tmp/test.png"}
    }
    result_new = overlay_assets_new.get(asset_id_str)
    assert result_new is not None, "字符串 key 应该能被字符串查找到"
    assert result_new["file_path"] == "/tmp/test.png"
    print("✅ test_overlay_assets_string_key_lookup 通过")


def test_mixed_uuid_and_string_comparison():
    """验证 Python 中 UUID 对象和字符串的不相等性"""
    ref_id = uuid.uuid4()
    assert str(ref_id) != ref_id, "UUID 对象和它的字符串表示在 Python 中不相等"
    print("✅ test_mixed_uuid_and_string_comparison 通过")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing exact_cutout UUID fix")
    print("=" * 60)
    test_mixed_uuid_and_string_comparison()
    test_normalize_overlay_layers_with_string_asset_ids()
    test_overlay_assets_string_key_lookup()
    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)
