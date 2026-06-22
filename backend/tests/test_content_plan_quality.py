from app.services.content_plan_quality import (
    ContentPlanQualityCase,
    evaluate_page_map_quality,
    evaluate_page_map_quality_case,
)


SOURCE_PRESERVE_CONTRACT = {
    "task_type": "source_to_ppt",
    "source_use": "faithful",
    "coverage": "near_complete",
    "compression": "low",
    "depth": "deep",
    "page_budget_policy": "explicit",
    "structure_policy": "source_order",
    "confidence": 0.95,
    "evidence": ["保留原文结构和金句"],
}


def issue_codes(report):
    return {issue.code for issue in report.issues}


def test_quality_evaluator_flags_unusable_page_map():
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 品牌课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "章节",
            "headline": "同一个标题",
            "bullets": [],
            "speaker_notes": "讲解第 2 页",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 3,
            "type": "content",
            "section_title": "章节",
            "headline": "同一个标题",
            "bullets": ["bullet", "P4｜content｜错误内联页码"],
            "speaker_notes": "先复述本页判断",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 4,
            "type": "content",
            "section_title": "章节",
            "headline": "占位内容",
            "bullets": ["内容待细化"],
            "speaker_notes": "占位备注。",
            "visual_suggestion": "待内容细化后生成本页视觉建议。",
            "generation_status": "skeleton",
        },
    ]

    report = evaluate_page_map_quality(page_map, target_count=6, min_pages=5)

    codes = issue_codes(report)
    assert not report.passed
    assert "page_count_below_min" in codes
    assert "duplicate_headline" in codes
    assert "missing_body_bullets" in codes
    assert "format_placeholder" in codes
    assert "inline_page_marker" in codes
    assert "skeleton_placeholder" in codes
    assert "generic_speaker_notes" in codes


def test_quality_evaluator_flags_body_replay_speaker_notes():
    page_map = [
        {
            "page_num": 1,
            "type": "content",
            "section_title": "章节",
            "headline": "AI 重新改变消费者决策",
            "bullets": [
                "消费者开始把信息处理外包给 AI",
                "品牌要争取的不只是被看见，而是被理解成合适答案",
                "平台权力正在从分发流量走向定义答案",
            ],
            "speaker_notes": (
                "这一页口头展开：\n"
                "- 消费者开始把信息处理外包给 AI\n"
                "- 品牌要争取的不只是被看见，而是被理解成合适答案\n"
                "- 平台权力正在从分发流量走向定义答案"
            ),
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_source",
        }
    ]

    report = evaluate_page_map_quality(page_map, target_count=1, min_pages=1)

    codes = issue_codes(report)
    assert not report.passed
    assert "generic_speaker_notes" in codes
    assert "speaker_notes_repeat_body" in codes


def test_quality_evaluator_flags_how_only_speaker_notes():
    page_map = [
        {
            "page_num": 1,
            "type": "content",
            "section_title": "章节",
            "headline": "AI 重新改变消费者决策",
            "bullets": [
                "消费者开始把信息处理外包给 AI",
                "品牌要争取的不只是被看见，而是被理解成合适答案",
            ],
            "speaker_notes": "讲法：先抛出本页判断，再补充一个反问，最后自然转到下一页。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
    ]

    report = evaluate_page_map_quality(page_map, target_count=1, min_pages=1)

    assert not report.passed
    assert "speaker_notes_missing_talk_content" in issue_codes(report)


def test_quality_evaluator_flags_repetitive_label_bullet_templates():
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "增长复盘",
            "bullets": [],
            "speaker_notes": "用业务增长停滞的问题开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        *[
            {
                "page_num": idx,
                "type": "content",
                "section_title": "增长分析",
                "headline": f"第 {idx} 个增长问题",
                "bullets": [
                    f"背景：第 {idx} 个市场变化导致获客效率下降",
                    f"问题：第 {idx} 个转化断点让用户没有继续行动",
                    f"动作：第 {idx} 个改进方向是补齐证据和触达",
                ],
                "speaker_notes": "先讲业务现象，再讲断点，最后收束到行动。",
                "visual_suggestion": "内容页。",
                "generation_status": "page_map_model",
            }
            for idx in range(2, 7)
        ],
    ]

    report = evaluate_page_map_quality(page_map, target_count=6, min_pages=6)

    assert not report.passed
    assert "repetitive_bullet_labels" in issue_codes(report)


def test_quality_evaluator_flags_missing_source_preserve_coverage():
    source_draft = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "开场。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 2,
            "type": "section",
            "section_title": "第一章",
            "headline": "第一章：什么变了？决策不再只发生在人脑里",
            "bullets": ["消费者正在外包信息处理权"],
            "speaker_notes": "讲第一章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 3,
            "type": "section",
            "section_title": "第二章",
            "headline": "第二章：什么没变？人心仍然是终点",
            "bullets": ["人仍然为自己的任务而买"],
            "speaker_notes": "讲第二章。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 4,
            "type": "content",
            "section_title": "第六章",
            "headline": "第六章：企业 90 天行动清单",
            "bullets": ["查 -> 定 -> 建 -> 放"],
            "speaker_notes": "讲行动清单。",
            "visual_suggestion": "行动清单。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 5,
            "type": "ending",
            "section_title": "结语",
            "headline": "在人心里有位置，在平台里有流量，在 AI 里有推荐",
            "bullets": ["当客户的 AI 凝视你的品牌时，它到底能看到什么？"],
            "speaker_notes": "收束。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]
    page_map = [
        {
            "page_num": idx,
            "type": "content" if idx not in {1, 5} else ("cover" if idx == 1 else "ending"),
            "section_title": "模型章节",
            "headline": f"模型只讲前文第 {idx} 页",
            "bullets": ["消费者正在外包信息处理权", "平台权力正在重构"],
            "speaker_notes": "讲前文。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        }
        for idx in range(1, 6)
    ]

    report = evaluate_page_map_quality(
        page_map,
        target_count=5,
        min_pages=5,
        strict=True,
        source_draft=source_draft,
        intent_contract=SOURCE_PRESERVE_CONTRACT,
    )

    codes = issue_codes(report)
    assert not report.passed
    assert "missing_source_structure" in codes
    assert "missing_source_tail" in codes


def test_quality_evaluator_accepts_repaired_source_preserve_page_map():
    repaired = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "AI 时代品牌课",
            "bullets": [],
            "speaker_notes": "用一个客户开始雇用 AI 的问题开场，引出整场课的核心追问。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "section",
            "section_title": "第一章",
            "headline": "第一章：什么变了？决策不再只发生在人脑里",
            "bullets": ["消费者正在外包信息处理权", "AI 已经进入购买前的研究、比较和判断"],
            "speaker_notes": "先讲搜索到决策的变化，再转到信息处理权被外包。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 3,
            "type": "section",
            "section_title": "第二章",
            "headline": "第二章：什么没变？人心仍然是终点",
            "bullets": ["人仍然为自己的任务而买", "品牌最终仍要回答人为什么选择你"],
            "speaker_notes": "把技术变化拉回人心不变的终点，形成反差。",
            "visual_suggestion": "章节页。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 4,
            "type": "content",
            "section_title": "第六章",
            "headline": "第六章：企业 90 天行动清单",
            "bullets": ["查 -> 定 -> 建 -> 放", "没有建，AI 抓不到可引用的结构化证据"],
            "speaker_notes": "逐项解释 90 天行动清单，并强调每一步对应的业务动作。",
            "visual_suggestion": "行动清单。",
            "generation_status": "page_map_source",
        },
        {
            "page_num": 5,
            "type": "ending",
            "section_title": "结语",
            "headline": "在人心里有位置，在平台里有流量，在 AI 里有推荐",
            "bullets": ["当客户的 AI 凝视你的品牌时，它到底能看到什么？"],
            "speaker_notes": "用这一句收束整场课，把品牌、人心、平台和 AI 推荐连成闭环。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_source",
        },
    ]

    report = evaluate_page_map_quality(
        repaired,
        target_count=5,
        min_pages=5,
        strict=True,
        source_draft=repaired,
        intent_contract=SOURCE_PRESERVE_CONTRACT,
    )

    assert report.passed
    assert report.errors == []


def test_quality_case_checks_required_and_forbidden_terms():
    case = ContentPlanQualityCase(
        name="preserve_source_course",
        target_count=3,
        min_pages=3,
        strict=True,
        required_anchors=("第一部：战略设计", "第十部：资本杠杆"),
        required_gold_sentences=("经营不是局部优化，而是一套闭环",),
        forbidden_terms=("互动练习", "课后复盘"),
    )
    page_map = [
        {
            "page_num": 1,
            "type": "cover",
            "section_title": "封面",
            "headline": "企业经营闭环",
            "bullets": [],
            "speaker_notes": "用经营闭环的核心问题开场，说明今天要回答增长如何系统化。",
            "visual_suggestion": "封面。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 2,
            "type": "content",
            "section_title": "第一部：战略设计",
            "headline": "经营不是局部优化，而是一套闭环",
            "bullets": ["第一部：战略设计要先定义企业追求的世界级标准", "战略不是口号，而是所有动作的约束条件"],
            "speaker_notes": "先把战略设计讲成经营闭环的起点，再承接到后面的价值创造。",
            "visual_suggestion": "内容页。",
            "generation_status": "page_map_model",
        },
        {
            "page_num": 3,
            "type": "ending",
            "section_title": "第十部：资本杠杆",
            "headline": "第十部：资本杠杆让经营结果被市场定价",
            "bullets": ["资本不是单独一章，而是前九部经营动作的外部定价"],
            "speaker_notes": "收束到经营闭环和资本杠杆之间的关系，避免新增课后动作。",
            "visual_suggestion": "结束页。",
            "generation_status": "page_map_model",
        },
    ]

    report = evaluate_page_map_quality_case(page_map, case)

    assert report.passed

    bad_page_map = [{**page} for page in page_map]
    bad_page_map[2] = {
        **bad_page_map[2],
        "section_title": "总结",
        "headline": "安排互动练习",
        "bullets": ["课后复盘：请学员完成互动练习"],
    }

    bad_report = evaluate_page_map_quality_case(bad_page_map, case)
    codes = issue_codes(bad_report)

    assert not bad_report.passed
    assert "missing_required_anchor" in codes
    assert "forbidden_term" in codes
