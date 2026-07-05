"""SourceAdapter / RawItem 契约测试（TECH_SPEC §5.1）。

覆盖：
  - RawItem 是 frozen dataclass（修改字段抛 FrozenInstanceError）
  - SourceAdapter 抽象类不能直接实例化
  - 子类实现 fetch() 后可实例化；未实现仍不可
  - SourceError 从 utils.errors 导入并继承 PipelineError（TECH_SPEC §7 唯一源）
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pipeline.sources.base import RawItem, SourceAdapter
from pipeline.utils.errors import PipelineError, SourceError


def test_rawitem_is_frozen() -> None:
    """RawItem 是不可变数据（全局 coding-style 不可变规则）。"""
    item = RawItem(
        title="hello",
        url="https://example.com/x",
        summary="sum",
        published_at="2026-07-05T10:00:00+00:00",
    )
    with pytest.raises(FrozenInstanceError):
        item.title = "mutated"  # type: ignore[misc]


def test_rawitem_accepts_none_optional() -> None:
    """url/summary/published_at 均可为 None（尽力解析，未知字段诚实置空）。"""
    item = RawItem(title="t", url=None, summary=None, published_at=None)
    assert item.url is None
    assert item.summary is None
    assert item.published_at is None


def test_sourceadapter_cannot_instantiate_directly() -> None:
    """SourceAdapter 抽象类：未实现 fetch() 的子类不可实例化。"""
    with pytest.raises(TypeError):
        SourceAdapter()  # type: ignore[abstract]


def test_sourceadapter_subclass_must_implement_fetch() -> None:
    """未实现 fetch() 的子类仍然不能实例化（抽象方法仍然抽象）。"""

    class Incomplete(SourceAdapter):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_sourceadapter_subclass_with_fetch_works() -> None:
    """完整实现 fetch() 后可实例化并调用。"""

    class Fake(SourceAdapter):
        name = "fake"

        def fetch(self) -> list[RawItem]:
            return [
                RawItem(
                    title="t", url=None, summary=None,
                    published_at=None,
                )
            ]

    s = Fake()
    assert s.name == "fake"
    items = s.fetch()
    assert len(items) == 1
    assert items[0].title == "t"


def test_sourceerror_is_pipeline_error() -> None:
    """SourceError 必须继承 PipelineError（编排层 except PipelineError 通用捕获）。"""
    assert issubclass(SourceError, PipelineError)
    err = SourceError("boom")
    assert str(err) == "boom"


def test_sourceerror_singleton_with_utils() -> None:
    """base 暴露的 SourceError 必须与 utils.errors 同对象（§7 唯一源）。
    否则编排层 except 一处却捕不到另一处，状态机语义错乱。"""
    from pipeline.sources.base import SourceError as BaseSE
    from pipeline.utils.errors import SourceError as UtilSE

    assert BaseSE is UtilSE