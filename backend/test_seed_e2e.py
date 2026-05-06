"""
端到端测试脚本：验证种子页机制 + 字体锁。

Step 1: 把 seed_family / is_seed_recommended 写进 slide.visual_json（兼容老数据）。
Step 2: 重生成 page 9/10/11/12 的 prompt_text（注入新 typography lock）。
Step 3: 把 page 9/10/11/12 标记为 prompt_ready。
Step 4: 跑 run_generation_pipeline，让它走两阶段（其实 page 4 已是种子，直接进 Stage 2）。
Step 5: 打印生成结果路径。
"""
import os
import sys

os.environ.setdefault("MAX_REAL_IMAGES_PER_RUN", "5")
os.environ.setdefault("IMAGE_GEN_MODE", "real")

sys.path.insert(0, ".")

from app.models.base import SessionLocal
from app.models.models import Project, Slide
from app.services.visual_plan import _annotate_seed_family
from app.services.prompt_engine import generate_prompt_for_page
from app.services.generation_pipeline import (
    run_generation_pipeline,
    _slide_family,
    _collect_existing_seeds,
)
from sqlalchemy.orm.attributes import flag_modified


PROJECT_ID = "b695b249-4b93-44a1-bbb4-8468c388f10d"
TARGET_PAGES = [9, 10, 11, 12]  # 4 张图，刚好用完用户给的预算


def _slides_to_intent_dicts(slides):
    """把 slide 转成 visual_plan intent dict（仅保留 _annotate_seed_family 需要的字段）。"""
    return [
        {
            "page_num": s.page_num,
            "type": s.type or "content",
            "layout": (s.visual_json or {}).get("layout") if isinstance(s.visual_json, dict) else None,
        }
        for s in slides
    ]


def step1_backfill_seed_family(db):
    print("\n=== Step 1: backfill seed_family / is_seed_recommended ===")
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == PROJECT_ID)
        .order_by(Slide.page_num)
        .all()
    )
    intents = _slides_to_intent_dicts(slides)
    _annotate_seed_family(intents)
    by_page = {it["page_num"]: it for it in intents}

    for s in slides:
        info = by_page.get(s.page_num)
        if not info:
            continue
        existing = dict(s.visual_json or {})
        existing["seed_family"] = info.get("seed_family")
        existing["is_seed_recommended"] = info.get("is_seed_recommended", False)
        s.visual_json = existing
        flag_modified(s, "visual_json")
        seed_mark = "★" if info.get("is_seed_recommended") else " "
        print(f"  page {s.page_num:>2} type={s.type:<8} family={info.get('seed_family'):<8} {seed_mark}seed")
    db.commit()


def step2_regenerate_prompts(db):
    print(f"\n=== Step 2: regenerate prompts for pages {TARGET_PAGES} (with new TYPOGRAPHY LOCK) ===")
    from app.api.slides import (
        _project_visual_assets_for_planning,  # noqa: F401  (reuse internal helper if needed)
        _project_refs_for_prompt,
        _derive_project_style_pack,
        _build_slide_reference_contexts,
    )

    project = db.query(Project).filter(Project.id == PROJECT_ID).first()
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == PROJECT_ID)
        .order_by(Slide.page_num)
        .all()
    )
    target_slides = [s for s in slides if s.page_num in TARGET_PAGES]

    style_text_override = _derive_project_style_pack(project, [
        {"page_num": s.page_num, "text_content": s.content_json or {}} for s in slides
    ])
    print(f"  style_text_override has {len(style_text_override or '')} chars")

    ref_contexts, ref_user_hints = _build_slide_reference_contexts(target_slides)

    for s in target_slides:
        content_text = dict((s.content_json or {}).get("text_content") or s.content_json or {})
        if ref_contexts.get(s.page_num):
            content_text["reference_context"] = "\n".join(ref_contexts[s.page_num])
        if ref_user_hints.get(s.page_num):
            content_text["reference_user_hint"] = ref_user_hints[s.page_num]

        page_intent = dict(s.visual_json or {})
        page_intent["page_num"] = s.page_num
        page_intent["type"] = s.type or "content"

        ref_images = _project_refs_for_prompt(
            project,
            page_intent.get("visual_asset_ids") if isinstance(page_intent, dict) else [],
        )

        new_prompt = generate_prompt_for_page(
            page_intent=page_intent,
            content_text=content_text,
            style_id=project.style_id or "default",
            reference_images=ref_images,
            style_text_override=style_text_override,
        )
        s.prompt_text = new_prompt
        print(f"  page {s.page_num:>2}: prompt regenerated ({len(new_prompt)} chars)")
        if "TYPOGRAPHY LOCK" in new_prompt:
            print(f"          ✓ contains TYPOGRAPHY LOCK")
        if "Family seed layout" in new_prompt:
            print(f"          ✓ contains Family seed layout note")
        if "PROTECTED BRAND ASSETS" in new_prompt:
            print(f"          ✓ contains PROTECTED BRAND ASSETS")

    db.commit()


def step3_reset_target_pages(db):
    print(f"\n=== Step 3: reset image status for {TARGET_PAGES} ===")
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == PROJECT_ID, Slide.page_num.in_(TARGET_PAGES))
        .all()
    )
    for s in slides:
        # 不删旧图，只把 status 标回 prompt_ready，让 pipeline 当作待生成
        s.status = "prompt_ready"
        s.error_msg = None
    db.commit()
    print(f"  reset {len(slides)} slides to prompt_ready")


def step4_run_pipeline(db):
    print(f"\n=== Step 4: run_generation_pipeline for pages {TARGET_PAGES} ===")
    print(f"  expected behavior:")
    print(f"    - Stage 0: discover existing seeds (page 4 should be in 'content' family)")
    print(f"    - Stage 1: empty (no missing seeds — all targets are non-seeds)")
    print(f"    - Stage 2: pages {TARGET_PAGES} regenerate WITH page 4 as seed_ref")

    # Verify expected seeds before run
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == PROJECT_ID)
        .order_by(Slide.page_num)
        .all()
    )
    seeds = _collect_existing_seeds(slides)
    print(f"  pre-run seeds: {seeds}")

    run_generation_pipeline(
        project_id=PROJECT_ID,
        db=db,
        page_nums=TARGET_PAGES,
        prototype=False,
        run_id=None,
    )
    print(f"  pipeline finished")


def step5_report(db):
    print(f"\n=== Step 5: report ===")
    slides = (
        db.query(Slide)
        .filter(Slide.project_id == PROJECT_ID, Slide.page_num.in_(TARGET_PAGES))
        .order_by(Slide.page_num)
        .all()
    )
    for s in slides:
        size = ""
        if s.image_path and os.path.exists(s.image_path):
            size = f"{os.path.getsize(s.image_path)/1024:.0f}KB"
        print(f"  page {s.page_num:>2}: status={s.status:<10} {s.image_path or '(no path)'} {size}")
        if s.error_msg:
            print(f"          ERROR: {s.error_msg[:200]}")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        step1_backfill_seed_family(db)
        step2_regenerate_prompts(db)
        step3_reset_target_pages(db)
        step4_run_pipeline(db)
        step5_report(db)
    finally:
        db.close()
