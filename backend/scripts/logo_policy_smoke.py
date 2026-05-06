import json
import logging
import os
from types import SimpleNamespace

from app.api import slides as slides_api
from app.services.generation_pipeline import _generate_one_slide, _load_reference_images
from app.services.prompt_engine import generate_prompt_for_page
from app.services.visual_plan import generate_visual_plan


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT_DIR = os.path.join(ROOT, "outputs", "logo_policy_smoke")
LOGO_PATH = os.path.join(ROOT, "outputs", "visual_asset_smoke", "logo.png")
PRODUCT_PATH = os.path.join(ROOT, "outputs", "visual_asset_smoke", "product.png")


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    content_plan = [
        {
            "page_num": 6,
            "type": "content",
            "text_content": {
                "headline": "终端货架：把古法香变成可见的购买理由",
                "subhead": "胡姬花花生油年度整合营销计划",
                "body": (
                    "统一货架陈列、导购话术和试闻体验台\n"
                    "让消费者在3秒内看到古法工艺、闻到香气记忆、理解高端溢价\n"
                    "核心物料：胡姬花花生油瓶、古法香标准卡、终端导购 Brief"
                ),
            },
        }
    ]

    style_text = "\n".join(
        [
            "Style: 胡姬花古法香商业提案",
            "Palette: #FFFFFF, #B01622, #F4C542, #2B2B2B",
            "Mood: 可信、温暖、高端、传统工艺感、商业提案感",
            "Visual rhythm: 内容页浅底高可读，红金只做强调，主视觉服务证据而不是抢正文。",
        ]
    )
    style_override = {
        "meta": {
            "theme": "胡姬花古法香商业提案",
            "style_name": "胡姬花古法香商业提案",
            "palette": ["#FFFFFF", "#B01622", "#F4C542", "#2B2B2B"],
            "mood": "可信、温暖、高端、传统工艺感、商业提案感",
        },
        "body": style_text,
    }

    global_visual_assets = [
        {
            "id": "asset-product-1",
            "name": "胡姬花花生油瓶",
            "kind": "product",
            "process_mode": "crop",
            "usage_note": "用于终端货架和产品露出的页面",
            "analysis_summary": "keywords=胡姬花、花生油、油瓶、包装、货架、终端、古法香",
        }
    ]

    visual_plan = generate_visual_plan(
        content_plan=content_plan,
        style_id="default",
        reference_image_ids=["logo-1", "asset-product-1"],
        style_override=style_override,
        global_visual_assets=global_visual_assets,
        progress_callback=lambda progress: print(f"VISUAL_PROGRESS {progress}", flush=True),
    )
    intent = visual_plan[0]

    logo_ref = SimpleNamespace(
        id="logo-1",
        role="logo",
        slide_id=None,
        file_path=LOGO_PATH,
        process_mode="blend",
        asset_name="HJF logo",
        asset_kind=None,
        usage_note=None,
        asset_analysis=None,
    )
    product_ref = SimpleNamespace(
        id="asset-product-1",
        role="visual_asset",
        slide_id=None,
        file_path=PRODUCT_PATH,
        process_mode="crop",
        asset_name="胡姬花花生油瓶",
        asset_kind="product",
        usage_note="用于终端货架和产品露出的页面",
        asset_analysis={
            "suggested_keywords": ["胡姬花", "花生油", "油瓶", "包装", "货架", "终端"],
            "fidelity_note": "Use uploaded image as identity source.",
        },
    )
    project = SimpleNamespace(reference_images=[logo_ref, product_ref], selected_template_recommendations=None)

    prompt_refs = slides_api._project_refs_for_prompt(project, intent.get("visual_asset_ids") or [], intent)
    prompt = generate_prompt_for_page(
        page_intent=intent,
        content_text=content_plan[0]["text_content"],
        style_id="default",
        reference_images=prompt_refs or None,
        style_text_override=style_text,
    )

    slide = SimpleNamespace(
        id="smoke-slide-6",
        page_num=6,
        type="content",
        visual_json=intent,
        prompt_text=prompt,
        content_json=content_plan[0],
        reference_images=[],
        project=project,
    )

    ref_data = _load_reference_images(slide)
    result = _generate_one_slide(slide, "logo_policy_smoke", OUTPUT_DIR, ref_data)

    payload = {
        "content_plan": content_plan,
        "visual_intent": intent,
        "prompt_refs": [
            {
                "id": ref.get("id"),
                "role": ref.get("role"),
                "process_mode": ref.get("process_mode"),
                "asset_name": ref.get("asset_name"),
                "asset_kind": ref.get("asset_kind"),
            }
            for ref in prompt_refs
        ],
        "loaded_ref_roles": [ref.get("role") for ref in ref_data],
        "loaded_ref_modes": [ref.get("process_mode") for ref in ref_data],
        "prompt": prompt,
        "result": result,
    }
    meta_path = os.path.join(OUTPUT_DIR, "smoke_result.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    print("SMOKE_META", meta_path, flush=True)
    print("SMOKE_IMAGE", result.get("image_path"), flush=True)
    print("SMOKE_ERROR", result.get("error"), flush=True)


if __name__ == "__main__":
    main()
