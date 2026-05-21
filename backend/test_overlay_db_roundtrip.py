"""
验证 overlay_layers 的数据库往返：
1. 创建 mock slide
2. 模拟 update_slide_overlay_layers 保存 overlay_layers
3. 验证 db.commit() + db.refresh() 后 overlay_layers 是否还在
4. 模拟 create_visual_plan 的 _merge_manual_pins_into_visual_json 是否保留 overlay_layers
"""
import os
import sys
import uuid

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.models.models import Base, Slide, Project, ReferenceImage
from app.services.overlay_layers import normalize_overlay_layers, overlay_reservation_instruction, enabled_overlay_layers
from app.api.slides import _merge_manual_pins_into_visual_json

DB_PATH = "./test_overlay_roundtrip.db"
engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)
Base.metadata.create_all(bind=engine)


def test_overlay_layers_roundtrip():
    db = SessionLocal()
    try:
        # 1. 创建 mock project + slide
        project = Project(id=str(uuid.uuid4()), title="Test", status="planning")
        db.add(project)
        db.commit()

        slide = Slide(
            id=str(uuid.uuid4()),
            project_id=project.id,
            page_num=1,
            type="content",
            content_json={"text_content": {"headline": "Test"}},
            visual_json={"visual_asset_ids": []},
            status="visual_ready",
        )
        db.add(slide)
        db.commit()
        db.refresh(slide)
        print(f"✅ 创建 slide，初始 visual_json: {slide.visual_json}")

        # 2. 创建 mock reference image (content_ref)
        ref = ReferenceImage(
            id=str(uuid.uuid4()),
            project_id=project.id,
            slide_id=slide.id,
            file_path="/tmp/mock_screenshot.png",
            role="content_ref",
        )
        db.add(ref)
        db.commit()

        # 3. 模拟 update_slide_overlay_layers 的逻辑
        raw_layers = [
            {
                "asset_id": str(ref.id),
                "mode": "exact_cutout",
                "preset": "right-card",
                "enabled": True,
            }
        ]
        valid_ids = {str(ref.id)}
        normalized = normalize_overlay_layers(raw_layers, valid_asset_ids=valid_ids, strict_assets=True)
        print(f"normalized_layers: {normalized}")

        visual = slide.visual_json if isinstance(slide.visual_json, dict) else {}
        visual["overlay_layers"] = normalized
        slide.visual_json = visual
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(slide, "visual_json")
        db.commit()
        db.refresh(slide)
        print(f"✅ 保存后 visual_json: {slide.visual_json}")

        # 4. 验证 overlay_layers 是否还在
        saved_layers = (slide.visual_json or {}).get("overlay_layers", [])
        assert len(saved_layers) == 1, f"❌ 保存后 overlay_layers 丢失: {saved_layers}"
        assert saved_layers[0]["mode"] == "exact_cutout", f"❌ mode 不对: {saved_layers[0]}"
        print("✅ 数据库往返测试通过：overlay_layers 已正确保存")

        # 5. 模拟 create_visual_plan 的 _merge_manual_pins_into_visual_json
        new_visual_plan = {"visual_asset_ids": ["some-other-id"], "type": "content"}
        merged = _merge_manual_pins_into_visual_json(new_visual_plan, slide.visual_json)
        print(f"✅ merge 后 visual_json: {merged}")

        merged_layers = (merged or {}).get("overlay_layers", [])
        assert len(merged_layers) == 1, f"❌ merge 后 overlay_layers 丢失: {merged_layers}"
        print("✅ merge 测试通过：overlay_layers 被正确保留")

        # 6. 验证 overlay_reservation_instruction 能正确读取
        instruction = overlay_reservation_instruction(merged)
        assert "CRITICAL LAYOUT INSTRUCTION" in instruction, f"❌ reservation instruction 为空: {instruction}"
        print(f"✅ overlay_reservation_instruction 非空，长度={len(instruction)}")

        # 7. 验证 enabled_overlay_layers
        enabled = enabled_overlay_layers(merged)
        assert len(enabled) == 1, f"❌ enabled_overlay_layers 返回空: {enabled}"
        print("✅ enabled_overlay_layers 测试通过")

        print("\n🎉 所有测试通过！overlay_layers 的数据库往返和 merge 逻辑正确。")

    finally:
        db.close()
        os.remove(DB_PATH)


if __name__ == "__main__":
    test_overlay_layers_roundtrip()
