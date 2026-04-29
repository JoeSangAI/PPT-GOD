"""
测试 slides.py 中的 reorder_slides 和 delete_slide 函数。

使用 mock 模拟 SQLAlchemy Session 和 ORM 查询链，
验证重排序和删除后的页码压缩逻辑是否正确。
"""

from unittest.mock import MagicMock

import pytest

from app.api.slides import reorder_slides, delete_slide
from app.models.models import Project, Slide


def _create_mock_db(project, slides):
    """创建一个模拟的 SQLAlchemy Session。"""
    db = MagicMock()
    state = {"project": project, "slides": slides, "deleted": [], "committed": False}

    def _make_query_mock(model_name):
        q = MagicMock()

        def _filter_func(*criteria):
            f = MagicMock()
            o = MagicMock()
            crits = list(criteria)

            def _first():
                if model_name == "Project":
                    return state["project"]
                if model_name == "Slide":
                    for s in state["slides"]:
                        match = True
                        for c in crits:
                            attr = str(c.left).split(".")[-1]
                            val = c.right.value
                            op = c.operator.__name__
                            actual = getattr(s, attr)
                            if op == "eq" and actual != val:
                                match = False
                                break
                            elif op == "gt" and not (actual > val):
                                match = False
                                break
                        if match:
                            return s
                    return None
                return None

            def _all():
                if model_name == "Slide":
                    result = list(state["slides"])
                    for c in crits:
                        attr = str(c.left).split(".")[-1]
                        val = c.right.value
                        op = c.operator.__name__
                        if op == "eq":
                            result = [s for s in result if getattr(s, attr) == val]
                        elif op == "gt":
                            result = [s for s in result if getattr(s, attr) > val]
                    return sorted(result, key=lambda s: s.page_num)
                return []

            f.first.side_effect = _first
            o.all.side_effect = _all
            f.order_by.return_value = o
            return f

        q.filter.side_effect = _filter_func
        return q

    def _query_func(model):
        return _make_query_mock(model.__name__)

    db.query.side_effect = _query_func

    def _delete(obj):
        state["deleted"].append(obj)

    db.delete.side_effect = _delete

    def _commit():
        state["committed"] = True

    db.commit.side_effect = _commit

    return db, state


class FakeSlide:
    """简化的 Slide 替身，用于单元测试。"""

    def __init__(self, sid, page_num, project_id="proj1"):
        self.id = sid
        self.page_num = page_num
        self.project_id = project_id
        self.content_json = {"page_num": page_num, "type": "content"}
        self.status = "pending"


class FakeProject:
    """简化的 Project 替身。"""

    def __init__(self, pid="proj1"):
        self.id = pid
        self.status = "draft"


class TestReorderSlides:
    """测试 reorder_slides 的重排序逻辑。"""

    def test_reorder_updates_page_num(self):
        """重排序后，每个 slide 的 page_num 应按新顺序更新。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
            FakeSlide("s3", 3),
        ]
        db, state = _create_mock_db(project, slides)

        body = MagicMock()
        body.page_nums = [3, 1, 2]  # 把第3页放第1位，第1页放第2位，第2页放第3位

        result = reorder_slides("proj1", body, db)

        assert result["message"] == "Slides reordered"
        assert result["new_order"] == [3, 1, 2]
        assert slides[2].page_num == 1  # 原3号 → 新1号
        assert slides[0].page_num == 2  # 原1号 → 新2号
        assert slides[1].page_num == 3  # 原2号 → 新3号
        assert state["committed"] is True

    def test_reorder_updates_content_json_page_num(self):
        """重排序时，content_json 中的 page_num 字段也应同步更新。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
        ]
        db, state = _create_mock_db(project, slides)

        body = MagicMock()
        body.page_nums = [2, 1]

        reorder_slides("proj1", body, db)

        assert slides[1].content_json["page_num"] == 1
        assert slides[0].content_json["page_num"] == 2

    def test_reorder_wrong_count_raises_400(self):
        """传入的页码数量与项目 slide 数量不符时应报错。"""
        project = FakeProject("proj1")
        slides = [FakeSlide("s1", 1), FakeSlide("s2", 2)]
        db, _ = _create_mock_db(project, slides)

        body = MagicMock()
        body.page_nums = [2, 1, 3]  # 3个页码但只有2个slide

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            reorder_slides("proj1", body, db)
        assert exc_info.value.status_code == 400

    def test_reorder_invalid_page_num_raises_400(self):
        """传入不存在的页码时应报错。"""
        project = FakeProject("proj1")
        slides = [FakeSlide("s1", 1), FakeSlide("s2", 2)]
        db, _ = _create_mock_db(project, slides)

        body = MagicMock()
        body.page_nums = [2, 99]  # 99不存在

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            reorder_slides("proj1", body, db)
        assert exc_info.value.status_code == 400

    def test_reorder_project_not_found(self):
        """项目不存在时应返回 404。"""
        project = None
        slides = []
        db, _ = _create_mock_db(project, slides)

        body = MagicMock()
        body.page_nums = []

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            reorder_slides("nonexist", body, db)
        assert exc_info.value.status_code == 404


class TestDeleteSlide:
    """测试 delete_slide 的删除和页码压缩逻辑。"""

    def test_delete_slide_removes_target(self):
        """删除指定 slide 后，该 slide 应被标记为删除。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
            FakeSlide("s3", 3),
        ]
        db, state = _create_mock_db(project, slides)

        result = delete_slide("proj1", "s2", db)

        assert result["message"] == "Slide deleted"
        assert result["slide_id"] == "s2"
        assert result["deleted_page_num"] == 2
        assert len(state["deleted"]) == 1
        assert state["deleted"][0].id == "s2"
        assert state["committed"] is True

    def test_delete_slide_compresses_later_page_nums(self):
        """删除后，后续 slide 的 page_num 应减 1。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
            FakeSlide("s3", 3),
            FakeSlide("s4", 4),
        ]
        db, state = _create_mock_db(project, slides)

        delete_slide("proj1", "s2", db)

        # s1 不受影响
        assert slides[0].page_num == 1
        # s3 (原3号) → 2号
        assert slides[2].page_num == 2
        # s4 (原4号) → 3号
        assert slides[3].page_num == 3

    def test_delete_slide_updates_content_json(self):
        """删除后，后续 slide 的 content_json 中的 page_num 也应同步更新。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
            FakeSlide("s3", 3),
        ]
        db, state = _create_mock_db(project, slides)

        delete_slide("proj1", "s1", db)

        assert slides[1].content_json["page_num"] == 1
        assert slides[2].content_json["page_num"] == 2

    def test_delete_last_slide_no_compression_needed(self):
        """删除最后一页时，没有后续页面需要压缩。"""
        project = FakeProject("proj1")
        slides = [
            FakeSlide("s1", 1),
            FakeSlide("s2", 2),
        ]
        db, state = _create_mock_db(project, slides)

        delete_slide("proj1", "s2", db)

        assert slides[0].page_num == 1
        assert slides[0].content_json["page_num"] == 1

    def test_delete_slide_not_found(self):
        """删除不存在的 slide 时应返回 404。"""
        project = FakeProject("proj1")
        slides = [FakeSlide("s1", 1)]
        db, _ = _create_mock_db(project, slides)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_slide("proj1", "nonexist", db)
        assert exc_info.value.status_code == 404

    def test_delete_project_not_found(self):
        """项目不存在时应返回 404。"""
        project = None
        slides = []
        db, _ = _create_mock_db(project, slides)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            delete_slide("proj1", "s1", db)
        assert exc_info.value.status_code == 404
