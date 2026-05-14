import pytest

from app.services import template_extractor


def test_recommend_template_pages_includes_extended_categories():
    pages = [
        {"page_num": 1, "category": "cover"},
        {"page_num": 2, "category": "toc"},
        {"page_num": 3, "category": "section"},
        {"page_num": 4, "category": "data"},
        {"page_num": 5, "category": "quote"},
        {"page_num": 6, "category": "content"},
        {"page_num": 7, "category": "ending"},
    ]

    recs = template_extractor.recommend_template_pages(pages)

    assert recs["cover"]["page_num"] == 1
    assert recs["toc"]["page_num"] == 2
    assert recs["section"]["page_num"] == 3
    assert recs["data"]["page_num"] == 4
    assert recs["quote"]["page_num"] == 5
    assert recs["ending"]["page_num"] == 7


def test_sparse_content_placeholder_is_not_overclassified_as_section():
    assert template_extractor._infer_page_category(
        4,
        7,
        "内容页标题\n这里是正文占位，展示左右分栏和信息层级。",
    ) == "content"
    assert template_extractor._infer_page_category(3, 7, "章节 01\n增长机会") == "section"


def test_failed_ppt_conversion_does_not_delete_existing_template_dir(tmp_path, monkeypatch):
    project_id = "project-1"
    final_dir = tmp_path / project_id / "templates"
    final_dir.mkdir(parents=True)
    existing = final_dir / "page_001.png"
    existing.write_bytes(b"old-template")
    source = tmp_path / "template.pptx"
    source.write_bytes(b"not-a-real-pptx")

    def fail_convert(_ppt_path: str, _output_dir: str) -> str:
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(template_extractor, "convert_ppt_to_pdf", fail_convert)

    with pytest.raises(RuntimeError):
        template_extractor.extract_template_package(str(source), project_id, str(tmp_path))

    assert existing.read_bytes() == b"old-template"
