"""
验证 _resolve_file_path 在极端工作目录下的正确性。
模拟 worker 不在 backend/ 目录下启动的场景。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")


def test_resolve_from_different_cwd():
    """模拟 worker 在不同目录下启动，验证仍能解析相对路径"""
    from app.services.generation_pipeline import _resolve_file_path

    # 找一个真实文件
    real_rel = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_rel):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_rel}")
        return True

    # 切换到完全不同的目录（模拟 worker 在其他目录启动）
    original_cwd = os.getcwd()
    different_dir = "/tmp"
    os.chdir(different_dir)

    try:
        # 测试各种相对路径格式（只测真实会出现的情况）
        variants = [
            real_rel,  # uploads/...
            "./" + real_rel,  # ./uploads/...
        ]

        for v in variants:
            result = _resolve_file_path(v)
            exists = os.path.exists(result)
            status = "✅" if exists else "❌"
            print(f"   {status} CWD={different_dir}, input={v}")
            print(f"      -> resolved={result}")
            print(f"      -> exists={exists}")
            assert exists, f"从不同 CWD 应仍能解析: {v} -> {result}"

        print("✅ test_resolve_from_different_cwd 通过")
        return True
    finally:
        os.chdir(original_cwd)


def test_pptx_assembler_from_different_cwd():
    """模拟 assembler 在不同目录下启动"""
    from app.services.pptx_assembler import _resolve_file_path

    real_rel = "uploads/2d86b82b-c6d3-4e6c-aa28-5111c8d53c4a/pptx_assets/pptx_p007_d2d7bba375.png"
    if not os.path.exists(real_rel):
        print(f"⚠️ 跳过：找不到真实测试文件 {real_rel}")
        return True

    original_cwd = os.getcwd()
    os.chdir("/tmp")

    try:
        result = _resolve_file_path("./" + real_rel)
        assert os.path.exists(result), f"assembler 从不同 CWD 应能解析: {result}"
        print("✅ test_pptx_assembler_from_different_cwd 通过")
        return True
    finally:
        os.chdir(original_cwd)


def main():
    print("=" * 60)
    print("路径解析极端场景验证（切换工作目录）")
    print("=" * 60)

    tests = [
        test_resolve_from_different_cwd,
        test_pptx_assembler_from_different_cwd,
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
