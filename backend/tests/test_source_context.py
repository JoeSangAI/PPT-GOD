from app.services.source_context import (
    SourceScopeRequired,
    build_brief_source_pack,
    build_source_context,
)


def _pack(filename: str, chapters: list[dict], token_count: int = 1000) -> dict:
    pages = []
    for chapter in chapters:
        for page_num in range(chapter["start_page"], chapter["end_page"] + 1):
            pages.append({
                "page_num": page_num,
                "text": f"{chapter['title']} 第{page_num}页正文",
                "estimated_tokens": 80,
            })
    return {
        "document": {"filename": filename, "kind": "pdf"},
        "stats": {
            "pages": len(pages),
            "chapters": len(chapters),
            "images": 0,
            "text_chars": token_count,
            "estimated_tokens": token_count,
        },
        "pages": pages,
        "chapters": chapters,
        "images": [],
    }


def _pack_with_images() -> dict:
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 51},
            {"chapter_id": "c2", "title": "第2章 客户痴迷", "start_page": 52, "end_page": 67},
        ],
        token_count=30000,
    )
    pack["images"] = [
        {
            "id": "fig-p47",
            "source_type": "pdf",
            "source_document": "创新.pdf",
            "source_page_num": 47,
            "pdf_source_page_num": 47,
            "chapter_id": "c1",
            "bbox": [10, 20, 30, 40],
            "nearby_text": "第一章内的医院愿景图",
        },
        {
            "id": "fig-p67",
            "source_type": "pdf",
            "source_document": "创新.pdf",
            "source_page_num": 67,
            "pdf_source_page_num": 67,
            "chapter_id": "c2",
            "bbox": [11, 21, 31, 41],
            "nearby_text": "第二章内的客户反馈图",
        },
    ]
    pack["stats"]["images"] = 2
    return pack


def test_brief_source_pack_wraps_short_prompt_as_source():
    pack = build_brief_source_pack("帮我做一份品牌策略 PPT")

    assert pack["document"]["kind"] == "brief"
    assert pack["pages"][0]["text"] == "帮我做一份品牌策略 PPT"
    assert pack["stats"]["estimated_tokens"] > 0


def test_source_context_uses_requested_first_chapter_only():
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 51},
            {"chapter_id": "c2", "title": "第2章 客户痴迷", "start_page": 52, "end_page": 67},
        ],
        token_count=30000,
    )

    context = build_source_context(
        brief="将第一章做成 20-30 页左右的讲课 PPT",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert context.status == "ready"
    assert "第1章 创新使命 第33页正文" in context.text
    assert "第2章 客户痴迷" not in context.text
    assert context.selected_scopes[0]["chapter_id"] == "c1"


def test_source_context_includes_only_figures_inside_selected_scope():
    context = build_source_context(
        brief="将第一章做成 20-30 页左右的讲课 PPT",
        source_packs=[_pack_with_images()],
        token_budget=120_000,
    )

    assert "--- AVAILABLE_FIGURES ---" in context.text
    assert 'figure_id="fig-p47"' in context.text
    assert 'source_page_num="47"' in context.text
    assert "第一章内的医院愿景图" in context.text
    assert "fig-p67" not in context.text
    assert "第二章内的客户反馈图" not in context.text


def test_source_context_includes_requested_intro_with_first_chapter_and_figures():
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "part1", "title": "第一部分 利他", "start_page": 32, "end_page": 32},
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 51},
            {"chapter_id": "c2", "title": "第2章 客户痴迷", "start_page": 52, "end_page": 67},
        ],
        token_count=50000,
    )
    pack["pages"].extend([
        {"page_num": 17, "text": "绪论\n看清挑战\n大企业为什么会失去创新能力", "estimated_tokens": 80},
        {"page_num": 20, "text": "绪论配图页\n永续创新的真谛", "estimated_tokens": 80},
        {"page_num": 31, "text": "绪论收束\n保持开放和乐观的心态", "estimated_tokens": 80},
    ])
    pack["pages"].sort(key=lambda page: page["page_num"])
    pack["images"] = [
        {
            "id": "fig-p20",
            "source_type": "pdf",
            "source_document": "创新.pdf",
            "source_page_num": 20,
            "pdf_source_page_num": 20,
            "chapter_id": "",
            "bbox": [10, 20, 30, 40],
            "nearby_text": "绪论里的永续创新示意图",
        },
        {
            "id": "fig-p47",
            "source_type": "pdf",
            "source_document": "创新.pdf",
            "source_page_num": 47,
            "pdf_source_page_num": 47,
            "chapter_id": "c1",
            "bbox": [11, 21, 31, 41],
            "nearby_text": "第一章内的医院愿景图",
        },
    ]
    pack["stats"]["images"] = 2

    context = build_source_context(
        brief="把绪论和第一章提取出来做成 40 页左右 PPT，参考图片尽可能放进去",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert context.status == "ready"
    assert "绪论" in context.text
    assert "第1章 创新使命 第33页正文" in context.text
    assert "第2章 客户痴迷" not in context.text
    assert [scope.get("title") for scope in context.selected_scopes] == ["绪论", "第1章 创新使命"]
    assert 'figure_id="fig-p20"' in context.text
    assert 'figure_id="fig-p47"' in context.text


def test_source_context_keeps_front_matter_scope_boundaries_by_requested_role():
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 34},
            {"chapter_id": "c2", "title": "第2章 客户痴迷", "start_page": 52, "end_page": 53},
        ],
        token_count=50000,
    )
    pack["pages"].extend([
        {"page_num": 10, "text": "前言\n作者为什么写这本书", "estimated_tokens": 80},
        {"page_num": 11, "text": "前言续页\n研究方法说明", "estimated_tokens": 80},
        {"page_num": 17, "text": "绪论\n看清挑战\n大企业为什么会失去创新能力", "estimated_tokens": 80},
        {"page_num": 31, "text": "阅读指南\n保持开放和乐观的心态", "estimated_tokens": 80},
    ])
    pack["pages"].sort(key=lambda page: page["page_num"])

    preface_context = build_source_context(
        brief="把前言和第一章做成 30 页 PPT",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert [scope.get("title") for scope in preface_context.selected_scopes] == ["前言", "第1章 创新使命"]
    assert "作者为什么写这本书" in preface_context.text
    assert "看清挑战" not in preface_context.text
    assert "保持开放和乐观的心态" not in preface_context.text

    intro_context = build_source_context(
        brief="把前言、绪论和第一章做成 40 页 PPT",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert [scope.get("title") for scope in intro_context.selected_scopes] == ["前言", "绪论", "第1章 创新使命"]
    assert "作者为什么写这本书" in intro_context.text
    assert "看清挑战" in intro_context.text
    assert "保持开放和乐观的心态" in intro_context.text
    assert "第2章 客户痴迷" not in intro_context.text


def test_source_context_uses_source_structure_when_front_matter_heading_is_not_repeated():
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 34},
            {"chapter_id": "c2", "title": "第2章 客户痴迷", "start_page": 52, "end_page": 53},
        ],
        token_count=50000,
    )
    pack["pages"].extend([
        {"page_num": 17, "text": "看清挑战\n大企业为什么会失去创新能力", "estimated_tokens": 80},
        {"page_num": 18, "text": "永续创新的真谛\n情感与使命支撑组织变革", "estimated_tokens": 80},
        {"page_num": 31, "text": "保持开放和乐观的心态，将从后续章节中获益良多。", "estimated_tokens": 80},
    ])
    pack["pages"].sort(key=lambda page: page["page_num"])
    pack["source_structure"] = [
        {
            "section_id": "front-intro-17",
            "section_role": "intro",
            "title": "绪论",
            "start_page": 17,
            "end_page": 30,
        },
        {
            "section_id": "front-guide-31",
            "section_role": "guide",
            "title": "阅读指南",
            "start_page": 31,
            "end_page": 32,
        },
        {
            "section_id": "c1",
            "section_role": "chapter",
            "title": "第1章 创新使命",
            "start_page": 33,
            "end_page": 34,
        },
        {
            "section_id": "c2",
            "section_role": "chapter",
            "title": "第2章 客户痴迷",
            "start_page": 52,
            "end_page": 53,
        },
    ]

    context = build_source_context(
        brief="把绪论和第一章做成 40 页 PPT，参考图片尽可能放进去",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert [scope.get("title") for scope in context.selected_scopes] == ["绪论", "第1章 创新使命"]
    assert "大企业为什么会失去创新能力" in context.text
    assert "保持开放和乐观的心态" in context.text
    assert "第2章 客户痴迷" not in context.text


def test_source_context_does_not_match_parts_or_body_mentions_for_first_chapter():
    pack = _pack(
        "创新.pdf",
        [
            {"chapter_id": "part1", "title": "第一部分 利他", "start_page": 32, "end_page": 32},
            {"chapter_id": "c1", "title": "第1章 创新使命", "start_page": 33, "end_page": 51},
            {"chapter_id": "mention", "title": "第1章）在案例回顾里再次出现", "start_page": 170, "end_page": 178},
        ],
        token_count=30000,
    )

    context = build_source_context(
        brief="将第一章做成 20-30 页左右的讲课 PPT",
        source_packs=[pack],
        token_budget=120_000,
    )

    assert [scope["chapter_id"] for scope in context.selected_scopes] == ["c1"]
    assert "第33页正文" in context.text
    assert "第32页正文" not in context.text
    assert "第170页正文" not in context.text


def test_source_context_requires_scope_when_all_sources_exceed_budget():
    packs = [
        _pack(
            f"book-{idx}.pdf",
            [{"chapter_id": f"c{idx}", "title": f"第{idx}章", "start_page": 1, "end_page": 50}],
            token_count=20_000,
        )
        for idx in range(1, 21)
    ]

    try:
        build_source_context(
            brief="把这些书完整做成课程 PPT",
            source_packs=packs,
            token_budget=120_000,
        )
    except SourceScopeRequired as exc:
        payload = exc.payload
    else:
        raise AssertionError("expected SourceScopeRequired")

    assert payload["status"] == "needs_scope"
    assert payload["source_stats"]["documents"] == 20
    assert payload["suggested_scopes"]
