import pytest

from app.models.models import Slide
from app.services.generation_pipeline import _map_slide_type_to_template_key, _slide_family
from app.services.slide_types import (
    CANONICAL_SLIDE_TYPES,
    UnsupportedSlideTypeError,
    normalize_slide_type,
)
from app.services.visual_plan import _assign_layout


EXPECTED_CANONICAL_TYPES = (
    "cover",
    "toc",
    "section",
    "content",
    "data",
    "hero",
    "quote",
    "ending",
)


def test_canonical_slide_type_contract_is_stable():
    assert CANONICAL_SLIDE_TYPES == EXPECTED_CANONICAL_TYPES
    assert [normalize_slide_type(value) for value in EXPECTED_CANONICAL_TYPES] == list(EXPECTED_CANONICAL_TYPES)


@pytest.mark.parametrize(
    ("legacy_value", "canonical_value"),
    [
        ("agenda", "toc"),
        ("chart", "data"),
        ("table", "data"),
        ("content_dense", "content"),
        ("content_hero", "content"),
        ("content_split", "content"),
        ("content_top", "content"),
    ],
)
def test_legacy_stored_types_are_explicit_migration_aliases(legacy_value, canonical_value):
    with pytest.raises(UnsupportedSlideTypeError):
        normalize_slide_type(legacy_value)
    assert normalize_slide_type(legacy_value, allow_legacy_stored_aliases=True) == canonical_value


def test_unknown_slide_type_never_silently_falls_back_to_content():
    with pytest.raises(UnsupportedSlideTypeError):
        normalize_slide_type("content_magic")
    with pytest.raises(UnsupportedSlideTypeError):
        _assign_layout("content_magic")
    with pytest.raises(UnsupportedSlideTypeError):
        _map_slide_type_to_template_key("content_magic")


@pytest.mark.parametrize(
    ("slide_type", "layout", "template_key", "family"),
    [
        ("cover", "cover", "cover", "cover"),
        ("toc", "toc", "toc", "toc"),
        ("section", "section", "section", "section"),
        ("content", "content", "content", "content"),
        ("data", "data", "data", "data"),
        ("hero", "hero", "quote", "hero"),
        ("quote", "hero", "quote", "hero"),
        ("ending", "ending", "ending", "ending"),
    ],
)
def test_visual_and_generation_mappings_cover_every_canonical_type(slide_type, layout, template_key, family):
    assert _assign_layout(slide_type) == layout
    assert _map_slide_type_to_template_key(slide_type) == template_key
    assert _slide_family(Slide(type=slide_type)) == family
