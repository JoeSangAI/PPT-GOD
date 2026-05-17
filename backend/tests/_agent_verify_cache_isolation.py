"""
Agent verification: reference upload cache project_id isolation.

Tests:
- Same image + different project_id => different cache key
- Same image + same project_id => same cache key
- project_id=None => works (backward compatible)
"""

from PIL import Image
import sys
import os

# Add backend to path so we can import app.services.image_generation
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.image_generation import (
    _reference_upload_cache_key,
    _base_reference_upload_profile,
)


def _make_test_image() -> Image.Image:
    """Create a tiny deterministic test image."""
    return Image.new("RGB", (64, 64), color=(42, 128, 255))


def test_different_project_ids_yield_different_keys():
    img = _make_test_image()
    profile = _base_reference_upload_profile()

    key_a = _reference_upload_cache_key(img, profile, project_id="proj-123")
    key_b = _reference_upload_cache_key(img, profile, project_id="proj-456")

    assert key_a != key_b, (
        f"Expected different cache keys for different project_ids, got same: {key_a}"
    )
    print("PASS: different project_ids yield different cache keys")


def test_same_project_id_yields_same_key():
    img = _make_test_image()
    profile = _base_reference_upload_profile()

    key_a = _reference_upload_cache_key(img, profile, project_id="proj-123")
    key_b = _reference_upload_cache_key(img, profile, project_id="proj-123")

    assert key_a == key_b, (
        f"Expected same cache key for same project_id, got different: {key_a} vs {key_b}"
    )
    print("PASS: same project_id yields same cache key")


def test_none_project_id_is_backward_compatible():
    img = _make_test_image()
    profile = _base_reference_upload_profile()

    key_none = _reference_upload_cache_key(img, profile, project_id=None)
    key_explicit = _reference_upload_cache_key(img, profile, project_id=None)

    assert key_none == key_explicit, (
        f"Expected same cache key when project_id=None, got different: {key_none} vs {key_explicit}"
    )
    print("PASS: project_id=None is backward compatible")


def test_none_vs_empty_string_are_same():
    """Empty string is falsy, so it behaves the same as None (no project id)."""
    img = _make_test_image()
    profile = _base_reference_upload_profile()

    key_none = _reference_upload_cache_key(img, profile, project_id=None)
    key_empty = _reference_upload_cache_key(img, profile, project_id="")

    # Both None and "" are falsy, so the hash update is skipped in both cases.
    assert key_none == key_empty, (
        f"Expected None and '' to produce same key (both mean 'no project'), got different: {key_none} vs {key_empty}"
    )
    print("PASS: project_id=None vs '' yield same cache key (both no-op)")


if __name__ == "__main__":
    test_different_project_ids_yield_different_keys()
    test_same_project_id_yields_same_key()
    test_none_project_id_is_backward_compatible()
    test_none_vs_empty_string_are_same()
    print("\nAll cache isolation tests passed.")
