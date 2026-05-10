ALLOWED_VISUAL_ASSET_KINDS = {"product", "person", "scene", "material", "other"}


def normalize_visual_asset_kind(kind: str | None) -> str:
    kind = (kind or "").strip().lower()
    return kind if kind in ALLOWED_VISUAL_ASSET_KINDS else "other"


def default_visual_asset_process_mode(kind: str | None) -> str:
    kind = normalize_visual_asset_kind(kind)
    if kind in {"product", "material"}:
        return "crop"
    if kind in {"person", "scene"}:
        return "blend"
    return "blend"


def reference_process_mode_instruction(mode: str | None) -> str:
    mode = mode or "blend"
    if mode == "blend":
        return (
            "Blend mode: extract the main subject/style from the reference image and integrate it naturally into the slide. "
            "You may adjust scale, lighting, angle, and background so it fits the layout."
        )
    if mode == "crop":
        return (
            "High-fidelity crop-integrate mode: use the reference image as the authoritative identity source. "
            "You may crop only empty margins/background and adjust scale or placement for the slide composition. "
            "Do not crop away, redraw, reinterpret, rotate, restyle, or replace the identity-bearing subject itself; design around its visible core."
        )
    if mode == "original":
        return (
            "Original-reference mode: preserve the reference image as faithfully as the image model allows. "
            "Do not intentionally crop, stretch, rotate, or alter it. If exact pixel-level preservation is required, "
            "the page should use an exact overlay layer instead of relying on image generation."
        )
    return f"Respect custom reference mode: {mode}."
