from types import SimpleNamespace

from app.services.visual_plan import _do_generate_visual_plan


def test_visual_plan_uses_page_grounded_fallback_when_logo_placeholder_is_only_evidence(monkeypatch):
    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(
                                    content=(
                                        '{"29": {'
                                        '"visual_evidence": "右上角 Logo 预留区域", '
                                        '"visual_summary": "Logo 占位", '
                                        '"visual_description": "右上角预留品牌标识位置。", '
                                        '"visual_asset_ids": [], '
                                        '"visual_asset_usage": {}'
                                        '}}'
                                    )
                                )
                            )
                        ]
                    )

    import app.services.visual_plan as visual_plan_module

    monkeypatch.setattr(visual_plan_module, "get_llm_client", lambda: FakeClient())
    monkeypatch.setattr(visual_plan_module, "_load_style", lambda _style_id: {"meta": {}, "body": ""})

    plan = _do_generate_visual_plan(
        content_plan=[
            {
                "page_num": 29,
                "type": "content",
                "text_content": {
                    "headline": "品牌语言",
                    "body": (
                        "- 案例：Apple、Dyson、Bang & Olufsen、Jeep\n"
                        "- 互动：遮住Logo猜品牌\n"
                        "- 问题：客户还能认出来吗？"
                    ),
                },
                "visual_suggestion": "品牌产品图，Logo遮住。",
            }
        ],
        has_project_logo=False,
    )

    assert plan[0]["page_num"] == 29
    assert plan[0]["visual_evidence"]
    assert "预留" not in plan[0]["visual_evidence"]
    assert "品牌标识位置" not in plan[0]["visual_description"]
    assert plan[0]["logo_policy"]["show_logo"] is False
