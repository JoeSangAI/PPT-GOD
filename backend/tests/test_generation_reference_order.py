import os

from PIL import Image

from app.services.generation_pipeline import _load_reference_images


class FakeRef:
    def __init__(self, file_path, role="content_ref", process_mode="blend"):
        self.file_path = file_path
        self.role = role
        self.process_mode = process_mode


class FakeProject:
    def __init__(self, global_ref):
        self.reference_images = [global_ref]
        self.selected_template_recommendations = None


class FakeSlide:
    def __init__(self, project, page_refs):
        self.page_num = 1
        self.project = project
        self.reference_images = page_refs
        self.type = "content"


def test_page_reference_images_are_first_and_labeled(tmp_path):
    page_ref = tmp_path / "page.png"
    global_ref = tmp_path / "style.png"
    Image.new("RGB", (10, 10), "red").save(page_ref)
    Image.new("RGB", (10, 10), "blue").save(global_ref)

    slide = FakeSlide(
        project=FakeProject(FakeRef(str(global_ref), role="style_ref")),
        page_refs=[FakeRef(str(page_ref), role="content_ref", process_mode="crop")],
    )

    refs = _load_reference_images(slide)

    assert refs[0]["label"] == "Reference Image 1"
    assert refs[0]["role"] == "content_ref"
    assert refs[0]["process_mode"] == "crop"
    assert os.path.basename(refs[0]["file_path"]) == "page.png"
    assert refs[1]["label"] == "Global Style Reference"

