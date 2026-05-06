import json
import logging
import os
import shutil
from types import SimpleNamespace

from PIL import Image

from app.api import slides as slides_api
from app.services.generation_pipeline import _generate_one_slide, _load_reference_images
from app.services.logo_assets import prepare_logo_overlay_image
from app.services.logo_policy import LOGO_HEIGHT_RATIOS, LOGO_WIDTH_RATIOS, normalize_logo_placement, should_show_logo
from app.services.pptx_assembler import assemble_pptx
from app.services.prompt_engine import generate_prompt_for_page


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUTPUT_DIR = os.path.join(ROOT, "outputs", "logo_overlay_pipeline_e2e")
PROJECT_ID = "logo_overlay_pipeline_e2e"
LOGO_PATH = os.path.join(
    ROOT,
    "backend",
    "uploads",
    "c55e9919-fd8b-4aae-b4ae-fece6932813a",
    "logo_58b54880b0e78b13c5e85c9d793c6296.png",
)
PRODUCT_PATH = os.path.join(ROOT, "outputs", "visual_asset_smoke", "product.png")


STYLE_TEXT = "\n".join(
    [
        "Style: 胡姬花古法香商业提案",
        "Palette: #FFFFFF, #B01622, #F4C542, #2B2B2B",
        "Mood: 可信、温暖、高端、传统工艺感、商业提案感",
        "Visual rhythm: 内容页浅底高可读；金句页更沉浸；红金只做品牌记忆和关键信息强调。",
    ]
)


CONTENT_PLAN = [
    {
        "page_num": 1,
        "type": "cover",
        "text_content": {
            "headline": "胡姬花古法香年度整合营销计划",
            "subhead": "让古法工艺成为可见、可闻、可选择的品牌理由",
            "body": "2026 Brand Growth Proposal",
        },
    },
    {
        "page_num": 2,
        "type": "content",
        "text_content": {
            "headline": "终端货架：把古法香变成可见的购买理由",
            "subhead": "统一陈列、导购话术和试闻体验",
            "body": "消费者在3秒内看到古法工艺\n闻到香气记忆\n理解高端溢价",
        },
    },
    {
        "page_num": 3,
        "type": "hero",
        "text_content": {
            "headline": "香气先被记住，品牌才会被选择",
            "subhead": "场景 A：Logo 不参与页面",
            "body": "",
        },
    },
    {
        "page_num": 4,
        "type": "hero",
        "text_content": {
            "headline": "把品牌做成消费者愿意走近的招牌",
            "subhead": "场景 B：Logo 作为画面元素 blend 进去",
            "body": "",
        },
    },
    {
        "page_num": 5,
        "type": "hero",
        "text_content": {
            "headline": "越高级的品牌，越懂得克制露出",
            "subhead": "场景 C：金句页保留右上角小 Logo",
            "body": "",
        },
    },
    {
        "page_num": 6,
        "type": "hero",
        "text_content": {
            "headline": "统一不是僵硬，而是让每一页都有同一种秩序",
            "subhead": "场景 D：金句页使用左下角小 Logo",
            "body": "",
        },
    },
]


VISUAL_INTENTS = [
    {
        "page_num": 1,
        "type": "cover",
        "layout": "cover",
        "visual_evidence": "古法木榨工坊与现代品牌提案封面",
        "visual_summary": "红金东方质感封面",
        "visual_description": "浅暖背景中呈现古法木榨工坊、花生颗粒与红金品牌提案气质，右侧保留大面积清爽留白。",
        "visual_asset_ids": [],
        "visual_asset_usage": {},
        "logo_policy": {"show_logo": True, "placement": "title-block-center", "scale": "large", "use_as_scene_asset": False},
    },
    {
        "page_num": 2,
        "type": "content",
        "layout": "content_split",
        "visual_evidence": "终端货架、试闻体验台与胡姬花花生油瓶",
        "visual_summary": "货架和体验台内容页",
        "visual_description": "左侧保持标题与三条要点清晰，右侧组织终端货架、试闻体验台和产品露出，整体浅底高可读。",
        "visual_asset_ids": ["asset-product-1"],
        "visual_asset_usage": {"asset-product-1": "Place the uploaded product image on the right-side retail display area, medium size and unobstructed."},
        "logo_policy": {"show_logo": True, "placement": "top-right", "scale": "small", "use_as_scene_asset": False},
    },
    {
        "page_num": 3,
        "type": "hero",
        "layout": "hero",
        "visual_evidence": "一束温暖香气从花生与油滴之间升起",
        "visual_summary": "无 Logo 沉浸金句",
        "visual_description": "全幅沉浸式浅金色氛围，花生、油滴、香气流线围绕金句形成安静中心，不出现任何品牌标识。",
        "visual_asset_ids": [],
        "visual_asset_usage": {},
        "logo_policy": {"show_logo": False, "placement": "top-right", "scale": "small", "use_as_scene_asset": False},
    },
    {
        "page_num": 4,
        "type": "hero",
        "layout": "hero",
        "visual_evidence": "古法工坊门头招牌与暖光品牌场景",
        "visual_summary": "Logo 融入招牌场景",
        "visual_description": "沉浸式古法工坊场景，木质门头招牌承载上传 Logo 的形象，暖光、花生与木榨器具营造可走近的品牌空间。",
        "visual_asset_ids": [],
        "visual_asset_usage": {},
        "logo_policy": {"show_logo": False, "placement": "top-right", "scale": "small", "use_as_scene_asset": True},
    },
    {
        "page_num": 5,
        "type": "hero",
        "layout": "hero",
        "visual_evidence": "大留白、单束红金光线与金句",
        "visual_summary": "右上角小 Logo 金句",
        "visual_description": "极简留白金句页，中心是克制排版的文字，红金光线只作为细微品牌记忆，右上角预留干净角标区。",
        "visual_asset_ids": [],
        "visual_asset_usage": {},
        "logo_policy": {"show_logo": True, "placement": "top-right", "scale": "small", "use_as_scene_asset": False},
    },
    {
        "page_num": 6,
        "type": "hero",
        "layout": "hero",
        "visual_evidence": "品牌秩序网格、留白与沉稳金句",
        "visual_summary": "左下角小 Logo 金句",
        "visual_description": "沉稳的版式网格和留白支撑金句，画面右侧有非常克制的红金线条节奏，左下角预留干净角标区。",
        "visual_asset_ids": [],
        "visual_asset_usage": {},
        "logo_policy": {"show_logo": True, "placement": "bottom-left", "scale": "small", "use_as_scene_asset": False},
    },
]


def _logo_preview_geometry(bg_size, logo_size, page_type, placement, scale="small"):
    w, h = bg_size
    lw, lh = logo_size
    is_large = scale == "large" or page_type in {"cover", "ending"}
    size_key = "large" if is_large else "small"
    max_w = int(w * LOGO_WIDTH_RATIOS[size_key])
    max_h = int(h * LOGO_HEIGHT_RATIOS[size_key])
    ratio = lh / max(lw, 1)
    out_w = max_w
    out_h = int(out_w * ratio)
    if out_h > max_h:
        out_h = max_h
        out_w = int(out_h / max(ratio, 0.01))
    margin_x = int(w * 0.028)
    margin_y = int(h * 0.028)
    placement = normalize_logo_placement(placement)
    if placement == "center":
        x = int((w - out_w) / 2)
        y = int((h - out_h) / 2)
    elif placement == "lower-center":
        x = int((w - out_w) / 2)
        y = int(h * 0.68)
    elif placement == "title-block-center":
        x = int(w * 0.68 - out_w / 2)
        y = int(h * 0.70)
    else:
        x = margin_x if placement.endswith("left") else w - margin_x - out_w
        y = margin_y if placement.startswith("top") else h - margin_y - out_h
    return x, y, out_w, out_h


def make_overlay_preview(image_path: str, logo_path: str, slide_data: dict, output_path: str) -> None:
    bg = Image.open(image_path).convert("RGBA")
    if should_show_logo(slide_data):
        logo = Image.open(prepare_logo_overlay_image(logo_path)).convert("RGBA")
        policy = slide_data.get("visual_json", {}).get("logo_policy", {})
        x, y, w, h = _logo_preview_geometry(
            bg.size,
            logo.size,
            slide_data.get("type", "content"),
            policy.get("placement"),
            policy.get("scale") or "small",
        )
        logo = logo.resize((w, h), Image.Resampling.LANCZOS)
        bg.alpha_composite(logo, (x, y))
    bg.convert("RGB").save(output_path, "PNG")


def main() -> None:
    resume = os.getenv("RESUME") == "1"
    if os.path.exists(OUTPUT_DIR) and not resume:
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logo_ref = SimpleNamespace(
        id="logo-1",
        role="logo",
        slide_id=None,
        file_path=LOGO_PATH,
        process_mode="blend",
        logo_anchor="top-right",
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
    content_by_page = {p["page_num"]: p for p in CONTENT_PLAN}

    slide_images = []
    meta = {"slides": []}
    for intent in VISUAL_INTENTS:
        page_num = intent["page_num"]
        content = content_by_page[page_num]
        prompt_refs = slides_api._project_refs_for_prompt(project, intent.get("visual_asset_ids") or [], intent)
        prompt = generate_prompt_for_page(
            page_intent=intent,
            content_text=content["text_content"],
            style_id="default",
            reference_images=prompt_refs or None,
            style_text_override=STYLE_TEXT,
        )
        slide = SimpleNamespace(
            id=f"slide-{page_num}",
            page_num=page_num,
            type=intent["type"],
            visual_json=intent,
            prompt_text=prompt,
            content_json=content,
            reference_images=[],
            project=project,
        )
        expected_image_path = os.path.join(OUTPUT_DIR, PROJECT_ID, f"slide_{page_num:02d}.png")
        if resume and os.path.exists(expected_image_path):
            ref_data = _load_reference_images(slide)
            result = {"image_path": expected_image_path, "error": None}
            print(f"PAGE_REUSE {page_num} refs={[ref.get('role') for ref in ref_data]} image={expected_image_path}", flush=True)
        else:
            ref_data = _load_reference_images(slide)
            result = _generate_one_slide(slide, PROJECT_ID, OUTPUT_DIR, ref_data)
            if result.get("error"):
                raise RuntimeError(f"page {page_num} failed: {result['error']}")
        slide_data = {
            "page_num": page_num,
            "type": intent["type"],
            "visual_json": intent,
            "image_path": result["image_path"],
            "speaker_notes": "",
        }
        slide_images.append(slide_data)
        preview_path = os.path.join(OUTPUT_DIR, f"preview_{page_num:02d}.png")
        make_overlay_preview(result["image_path"], LOGO_PATH, slide_data, preview_path)
        meta["slides"].append({
            "page_num": page_num,
            "type": intent["type"],
            "scenario": content["text_content"].get("subhead"),
            "logo_policy": intent.get("logo_policy"),
            "prompt_refs": [
                {
                    "role": ref.get("role"),
                    "process_mode": ref.get("process_mode"),
                    "asset_name": ref.get("asset_name"),
                }
                for ref in prompt_refs
            ],
            "loaded_ref_roles": [ref.get("role") for ref in ref_data],
            "image_path": result["image_path"],
            "preview_path": preview_path,
            "prompt": prompt,
        })
        print(f"PAGE_DONE {page_num} refs={[ref.get('role') for ref in ref_data]} preview={preview_path}", flush=True)

    pptx_path = os.path.join(OUTPUT_DIR, "logo_overlay_pipeline_e2e.pptx")
    assemble_pptx(
        slide_images=slide_images,
        output_path=pptx_path,
        logo_config={"file_path": LOGO_PATH, "anchor": "top-right"},
    )
    meta["pptx_path"] = pptx_path
    meta_path = os.path.join(OUTPUT_DIR, "logo_overlay_pipeline_e2e.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    print("E2E_META", meta_path, flush=True)
    print("E2E_PPTX", pptx_path, flush=True)


if __name__ == "__main__":
    main()
