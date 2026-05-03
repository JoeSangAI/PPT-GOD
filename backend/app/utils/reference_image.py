def reference_process_mode_instruction(mode: str | None) -> str:
    mode = mode or "blend"
    if mode == "blend":
        return (
            "Blend mode: extract the main subject/style from the reference image and integrate it naturally into the slide. "
            "You may adjust scale, lighting, angle, and background so it fits the layout."
        )
    if mode == "crop":
        return (
            "Crop mode: preserve the reference image content as a recognizable visual block, but crop it to fit the slide composition. "
            "Do not reinterpret the subject; design around its visible core."
        )
    if mode == "original":
        return (
            "Original mode: preserve the reference image exactly as-is. Do not crop, stretch, rotate, or alter it. "
            "Reserve intact layout space for it and do not composite other visuals on top."
        )
    return f"Respect custom reference mode: {mode}."
