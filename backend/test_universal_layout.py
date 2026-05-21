"""
泛化版多图智能排版：模型自己分析页面主题和素材关系，自动推断布局。
不针对任何特定场景（如"案例演示"），适用于任意页面类型。
"""
import os
import sys
import base64
import requests

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("ENV", "dev")

from PIL import Image, ImageDraw, ImageFont
from app.services.image_generation import generate_slide_image
from app.services.overlay_layers import overlay_reservation_instruction

OUTPUT_DIR = "./test_outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

ASSETS_DIR = "/Users/Joe_1/Desktop/Development/ppt-god/backend/uploads/78a21fde-1857-48ca-9047-8494612e26f8/pptx_assets"

COMET_KEY = "sk-wX2r2x7Df0i0NIvf1nC3UD38xQctsnbNHo0owagvH4QaBj3g"
COMET_URL = "https://api.cometapi.com/v1/chat/completions"


def encode_image(path, max_size=512):
    """压缩图片后转 base64。"""
    from PIL import Image
    import io
    img = Image.open(path)
    img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def universal_layout(background_path, asset_paths):
    """
    泛化布局：模型自动分析页面主题、素材内容、关系，推断最优布局。
    不预设任何页面类型（如"案例页""产品页"），完全由模型自主判断。
    """
    content = [
        {
            "type": "text",
            "text": """You are an expert PPT layout designer. The FIRST image is the background slide. ALL subsequent images are overlay assets that MUST be placed onto this background.

## CRITICAL RULES
1. You MUST place EVERY provided asset. The number of placements must exactly equal the number of assets.
2. Assets are numbered in order: asset_index 0 = first asset image, asset_index 1 = second asset image, etc.
3. NEVER place any asset over background text (title, subtitle, body text).
4. NEVER place assets in the top area if the title is there — use the middle and bottom areas instead.

## Analysis Steps
1. **Background Analysis**: Identify title, subtitle, body text positions, and overall visual style.
2. **Asset Analysis**: For each asset, determine its content type (screenshot, chart, photo, product image, etc.) and visual importance.
3. **Relationship Analysis**: Determine how assets relate to each other:
   - "parallel" — equal importance, should be similar sizes
   - "primary-secondary" — one main asset + supporting assets
   - "sequence" — left-to-right or top-to-bottom flow
   - "comparison" — side-by-side contrast
4. **Intent Inference**: Based on background text + asset content, infer what message this slide conveys and what the audience should focus on.

## Layout Principles
- NEVER overlap assets with background text (title, subtitle, body)
- Give more space to visually important or detail-rich assets
- Parallel assets should have balanced sizes (difference < 30%)
- Maintain consistent spacing and alignment
- Respect the slide's visual style (don't break the design harmony)

## Output Format
Return ONLY a JSON object with placements for ALL assets:
```json
{
  "reasoning": "Brief explanation of your layout decisions",
  "placements": [
    {"asset_index": 0, "x": 0.05, "y": 0.25, "width": 0.4, "height": 0.5, "reason": "Left screenshot"},
    {"asset_index": 1, "x": 0.55, "y": 0.25, "width": 0.4, "height": 0.5, "reason": "Right screenshot"},
    {"asset_index": 2, "x": 0.25, "y": 0.78, "width": 0.5, "height": 0.15, "reason": "Bottom screenshot"}
  ]
}
```
Coordinates are normalized (0-1) based on 1792x1024 slide."""
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encode_image(background_path)}"}
        },
    ]

    for i, apath in enumerate(asset_paths):
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{encode_image(apath)}"}
        })

    resp = requests.post(
        COMET_URL,
        headers={
            "Authorization": f"Bearer {COMET_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4.1-nano",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.3,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    raw = data["choices"][0]["message"]["content"]

    import json
    result = json.loads(raw)
    return result


def apply_layout(bg_path, asset_paths, layout_result, output_path):
    """根据模型输出的布局，合成最终图片。"""
    bg = Image.open(bg_path).convert("RGBA")
    placements = layout_result.get("placements", [])

    # 绘制布局框
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for i, p in enumerate(placements):
        if i >= len(asset_paths):
            continue
        apath = asset_paths[i]
        asset = Image.open(apath).convert("RGBA")

        left = int(float(p["x"]) * 1792)
        top = int(float(p["y"]) * 1024)
        width = int(float(p["width"]) * 1792)
        height = int(float(p["height"]) * 1024)

        # contain 缩放
        scale = min(width / asset.width, height / asset.height)
        pic_w = int(asset.width * scale)
        pic_h = int(asset.height * scale)
        pic_left = left + (width - pic_w) // 2
        pic_top = top + (height - pic_h) // 2

        # 画框
        draw.rectangle([left, top, left + width, top + height],
                      outline=(0, 100, 255, 200), width=2)

        # 贴图
        asset_resized = asset.resize((pic_w, pic_h), Image.Resampling.LANCZOS)
        bg.paste(asset_resized, (pic_left, pic_top), asset_resized)

        # 标注
        reason = p.get("reason", "")
        label = f"[{i+1}] {reason[:20]}"
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rectangle([left, top - th - 2, left + tw + 6, top], fill=(0, 0, 0, 180))
        draw.text((left + 3, top - th), label, fill=(255, 255, 255), font=font)

    bg = Image.alpha_composite(bg, overlay)
    bg.save(output_path, "PNG")
    return output_path


def main():
    print("=" * 60)
    print("Universal Layout: GPT-4.1-nano auto-analysis")
    print("=" * 60)

    # 用同样的背景和素材测试泛化能力
    bg_path = os.path.join(OUTPUT_DIR, "bg_gpt41_layout.png")
    if not os.path.exists(bg_path):
        print("Generating background...")
        visual = {
            "overlay_layers": [
                {"asset_id": "s1", "enabled": True, "preset": "left-card", "mode": "exact_cutout"},
                {"asset_id": "s2", "enabled": True, "preset": "right-card", "mode": "exact_cutout"},
                {"asset_id": "s3", "enabled": True, "preset": "bottom-band", "mode": "exact_cutout"},
            ]
        }
        prompt = (
            "Create one polished widescreen landscape presentation slide.\n\n"
            "Visible Text:\n"
            '- Headline: "案例演示"\n'
            "Style: Modern corporate, soft blue gradient, professional typography.\n\n"
            "Visual: Clean atmospheric background with subtle depth.\n\n"
            f"Exact Overlay Reservation:\n{overlay_reservation_instruction(visual)}"
        )
        img = generate_slide_image(prompt, resolution="1K", aspect_ratio="16:9")
        img.save(bg_path, "PNG")

    assets = [
        os.path.join(ASSETS_DIR, "2026.05_5_p020_3524386527.png"),
        os.path.join(ASSETS_DIR, "2026.05_5_p020_63fbd7601f.png"),
        os.path.join(ASSETS_DIR, "2026.05_5_p020_f7d45a97d7.png"),
    ]

    print("\n[Step 1] GPT-4.1-nano analyzing background + assets...")
    layout = universal_layout(bg_path, assets)
    print(f"Reasoning: {layout.get('reasoning', 'N/A')}")
    for p in layout.get("placements", []):
        print(f"  Asset {p.get('asset_index')}: ({p['x']:.3f}, {p['y']:.3f}, {p['width']:.3f}, {p['height']:.3f}) — {p.get('reason', '')}")

    print("\n[Step 2] Applying layout...")
    output = os.path.join(OUTPUT_DIR, "final_universal_layout.png")
    apply_layout(bg_path, assets, layout, output)
    print(f"Saved: {output}")

    print("\n" + "=" * 60)
    print("Done! Check final_universal_layout.png")
    print("=" * 60)


if __name__ == "__main__":
    main()
