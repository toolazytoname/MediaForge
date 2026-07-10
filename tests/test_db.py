"""db.py 与状态机单元测试（TECH_SPEC §3/§4 + HARD_PARTS §5 幂等性）。

覆盖：
  - connect() 启用 WAL + foreign_keys
  - init_db 幂等
  - 三个状态机的全转移矩阵（合法 / 非法）
  - transition() 乐观锁 StaleState
  - 内容唯一约束（topic.content_hash / content.topic_id / publication UNIQUE）
  - JSON 字段（formats / gate_scores）往返保真
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from pipeline import db
from pipeline.models import (
    Content,
    ContentStatus,
    Metric,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
    TOPIC_TRANSITIONS,
    CONTENT_TRANSITIONS,
    PUBLICATION_TRANSITIONS,
)
from pipeline.utils.errors import (
    IllegalTransition,
    PipelineError,
    StaleState,
)
from pipeline.utils.ids import new_id


# ── Fixtures & helpers ────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    """每个测试独立的临时 SQLite（tmp_path 文件系统，WAL 真生效）。"""
    p = tmp_path / "test.db"
    c = db.connect(p)
    db.init_db(c)
    yield c
    c.close()


def _topic(**kw) -> Topic:
    base = dict(
        id=new_id("t"),
        source="rss:test",
        title="Test Topic",
        url="https://example.com/a",
        summary="summary text",
        content_hash="h_" + kw.get("title", "x").replace(" ", "_"),
        pillar=None,
        score=None,
        score_reason=None,
        status=TopicStatus.RAW,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Topic(**base)


def _content(topic_id: str, **kw) -> Content:
    base = dict(
        id=new_id("c"),
        topic_id=topic_id,
        pillar="ai_daily",
        title="Test Content",
        canonical_path="output/2026-07-05/x/canonical.md",
        formats=("toutiao", "xiaohongshu", "x"),
        gate_score_total=None,
        gate_scores=None,
        gate_verdict=None,
        status=ContentStatus.DRAFT,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Content(**base)


def _pub(content_id: str, **kw) -> Publication:
    base = dict(
        id=new_id("p"),
        content_id=content_id,
        platform="x",
        account_id="main",
        scheduled_at="2026-07-05T10:00:00+00:00",
        published_at=None,
        platform_post_id=None,
        platform_url=None,
        error=None,
        retry_count=0,
        status=PublicationStatus.QUEUED,
        created_at="2026-07-05T00:00:00+00:00",
        updated_at="2026-07-05T00:00:00+00:00",
    )
    base.update(kw)
    return Publication(**base)


# ── 状态机全矩阵覆盖（补 D 项：每个 from_state 的全部非法 outgoing） ──
#
# 每个 status 的 outgoing 集合从契约表 *_TRANSITIONS 取，禁止手工列——改契约
# 自动同步。覆盖 from_state 全集 × to_state 全集 - 合法对 = 非法对全集。

_ALL_TOPIC_STATES = (
    TopicStatus.RAW, TopicStatus.SCORED, TopicStatus.SELECTED,
    TopicStatus.CONSUMED, TopicStatus.REJECTED,
)
_ALL_CONTENT_STATES = (
    ContentStatus.DRAFT, ContentStatus.GATED, ContentStatus.APPROVED,
    ContentStatus.REJECTED_BY_HUMAN, ContentStatus.DISCARDED,
    ContentStatus.FAILED, ContentStatus.DONE,
)
_ALL_PUBLICATION_STATES = (
    PublicationStatus.QUEUED, PublicationStatus.PUBLISHING,
    PublicationStatus.PUBLISHED, PublicationStatus.FAILED,
    PublicationStatus.CANCELLED,
)


def _illegal_pairs(states, transitions_table):
    """从契约表反推所有非法 (from, to) 对（含自循环与跳级）。"""
    pairs = []
    for f in states:
        for t in states:
            if t not in transitions_table.get(f, set()):
                pairs.append((f, t))
    return pairs


_TOPIC_ILLEGAL_PAIRS = _illegal_pairs(_ALL_TOPIC_STATES, TOPIC_TRANSITIONS)
_CONTENT_ILLEGAL_PAIRS = _illegal_pairs(_ALL_CONTENT_STATES, CONTENT_TRANSITIONS)
_PUB_ILLEGAL_PAIRS = _illegal_pairs(_ALL_PUBLICATION_STATES, PUBLICATION_TRANSITIONS)


@pytest.mark.parametrize("from_state,to_state", _TOPIC_ILLEGAL_PAIRS)
def test_topic_every_illegal_pair_rejected(
    conn, topic_id, from_state, to_state
):
    """topics 全 25 对 - 5 合法 = 20 非法，全部 IllegalTransition。"""
    conn.execute(
        "UPDATE topics SET status=? WHERE id=?",
        (from_state, topic_id),
    )
    conn.commit()
    with pytest.raises(IllegalTransition):
        db.transition(conn, "topics", topic_id, from_state, to_state)


@pytest.mark.parametrize("from_state,to_state", _CONTENT_ILLEGAL_PAIRS)
def test_content_every_illegal_pair_rejected(
    conn, content_id, from_state, to_state
):
    """contents 全 49 对 - 14 合法 = 35 非法，全部 IllegalTransition。"""
    conn.execute(
        "UPDATE contents SET status=? WHERE id=?",
        (from_state, content_id),
    )
    conn.commit()
    with pytest.raises(IllegalTransition):
        db.transition(conn, "contents", content_id, from_state, to_state)


@pytest.mark.parametrize("from_state,to_state", _PUB_ILLEGAL_PAIRS)
def test_publication_every_illegal_pair_rejected(
    conn, pub_id, from_state, to_state
):
    """publications 全 25 对 - 6 合法 = 19 非法，全部 IllegalTransition。"""
    conn.execute(
        "UPDATE publications SET status=? WHERE id=?",
        (from_state, pub_id),
    )
    conn.commit()
    with pytest.raises(IllegalTransition):
        db.transition(conn, "publications", pub_id, from_state, to_state)


# ── Connect / init_db ─────────────────────────────────────

def test_connect_enables_wal(tmp_path):
    c = db.connect(tmp_path / "wal.db")
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    c.close()


def test_connect_enables_foreign_keys(tmp_path):
    c = db.connect(tmp_path / "fk.db")
    fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
    assert int(fk) == 1
    c.close()


def test_init_db_is_idempotent(tmp_path):
    """三次 init_db 不报错、不丢数据（TECH_SPEC §3 契约）。"""
    p = tmp_path / "idem.db"
    c = db.connect(p)
    db.init_db(c)
    db.insert_topic(c, _topic(content_hash="persisted"))
    db.init_db(c)
    db.init_db(c)
    rows = db.get_topics_by_status(c, TopicStatus.RAW)
    assert len(rows) == 1
    c.close()


# ── ids.py ────────────────────────────────────────────────

def test_new_id_format_matches_prefix_8hex():
    for prefix in ("t", "c", "p"):
        new = new_id(prefix)
        assert re.fullmatch(rf"{prefix}_[0-9a-f]{{8}}", new), new


def test_new_id_unique_for_1000_calls():
    ids = {new_id("t") for _ in range(1000)}
    assert len(ids) == 1000


# ── Errors hierarchy ──────────────────────────────────────

def test_illegal_transition_subclasses_pipeline_error():
    assert issubclass(IllegalTransition, PipelineError)


def test_stale_state_subclasses_pipeline_error():
    assert issubclass(StaleState, PipelineError)


def test_illegal_transition_attributes():
    e = IllegalTransition("topics", "raw", "consumed")
    assert e.table == "topics"
    assert e.from_status == "raw"
    assert e.to_status == "consumed"
    assert "topics" in str(e) and "raw" in str(e) and "consumed" in str(e)


def test_stale_state_attributes_with_actual():
    e = StaleState("topics", "t_x", "raw", "consumed")
    assert e.table == "topics"
    assert e.row_id == "t_x"
    assert e.expected_status == "raw"
    assert e.actual_status == "consumed"


# ── topics: insert / get ──────────────────────────────────

def test_insert_topic_roundtrip(conn):
    t = _topic(content_hash="h_round", title="hello")
    db.insert_topic(conn, t)
    got = db.get_topic(conn, t.id)
    assert got == t


def test_topic_unique_content_hash_enforced(conn):
    """content_hash UNIQUE → 重复入库抛 IntegrityError（HARD_PARTS §5）。"""
    db.insert_topic(conn, _topic(content_hash="dup", title="A"))
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_topic(conn, _topic(content_hash="dup", title="B"))


def test_topics_by_status_filters(conn):
    db.insert_topic(conn, _topic(content_hash="h1", status=TopicStatus.RAW))
    db.insert_topic(conn, _topic(content_hash="h2", status=TopicStatus.RAW))
    db.insert_topic(conn, _topic(content_hash="h3", status=TopicStatus.SCORED))
    rows = db.get_topics_by_status(conn, TopicStatus.RAW)
    assert {r.content_hash for r in rows} == {"h1", "h2"}


def test_get_topic_returns_none_for_missing(conn):
    assert db.get_topic(conn, new_id("t")) is None


# ── contents: insert / get / FK ───────────────────────────

def _seed_topic(conn) -> str:
    t = _topic(content_hash="h_seed")
    db.insert_topic(conn, t)
    return t.id


def test_insert_content_roundtrip(conn):
    tid = _seed_topic(conn)
    c = _content(topic_id=tid, formats=("a", "b"))
    db.insert_content(conn, c)
    got = db.get_content(conn, c.id)
    assert got == c


def test_content_requires_existing_topic_fk(conn):
    """FK：content.topic_id 必须引用已存在 topic，否则 IntegrityError。"""
    bogus = new_id("t")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_content(conn, _content(topic_id=bogus))


def test_content_unique_topic_id(conn):
    """UNIQUE(topic_id): 同一 topic 不能生成多个 content。"""
    tid = _seed_topic(conn)
    db.insert_content(conn, _content(topic_id=tid))
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_content(conn, _content(topic_id=tid))


def test_content_formats_json_tuple_roundtrip(conn):
    tid = _seed_topic(conn)
    c = _content(topic_id=tid, formats=("toutiao", "x"))
    db.insert_content(conn, c)
    got = db.get_content(conn, c.id)
    assert got.formats == ("toutiao", "x")
    assert isinstance(got.formats, tuple)


def test_content_gate_scores_json_dict_roundtrip(conn):
    tid = _seed_topic(conn)
    c = _content(topic_id=tid, gate_scores={"info": 8, "fun": 7, "view": 9})
    db.insert_content(conn, c)
    got = db.get_content(conn, c.id)
    assert got.gate_scores == {"info": 8, "fun": 7, "view": 9}


def test_content_gate_scores_none_roundtrip(conn):
    tid = _seed_topic(conn)
    c = _content(topic_id=tid, gate_scores=None)
    db.insert_content(conn, c)
    got = db.get_content(conn, c.id)
    assert got.gate_scores is None


# ── publications: insert / get / UNIQUE ───────────────────

def _seed_content(conn) -> str:
    tid = _seed_topic(conn)
    c = _content(topic_id=tid)
    db.insert_content(conn, c)
    return c.id


def test_insert_publication_roundtrip(conn):
    cid = _seed_content(conn)
    p = _pub(content_id=cid)
    db.insert_publication(conn, p)
    assert db.get_publication(conn, p.id) == p


def test_publication_unique_content_platform_account(conn):
    """UNIQUE(content_id, platform, account_id) 防重复发布最后防线。"""
    cid = _seed_content(conn)
    db.insert_publication(conn, _pub(content_id=cid))
    # 同内容同平台同账号再发一次 → IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_publication(conn, _pub(content_id=cid))


def test_publication_different_platform_allowed(conn):
    """跨平台同 content 各自一条（UNIQUE 只在三元组上）。"""
    cid = _seed_content(conn)
    db.insert_publication(conn, _pub(content_id=cid, platform="x"))
    db.insert_publication(conn, _pub(content_id=cid, platform="toutiao"))
    rows = db.get_publications_by_status(conn, PublicationStatus.QUEUED)
    assert len(rows) == 2


# ── metrics / llm_calls smoke ─────────────────────────────

def test_insert_metric_smoke(conn):
    cid = _seed_content(conn)
    db.insert_publication(conn, _pub(content_id=cid))
    pubs = db.get_publications_by_status(conn, PublicationStatus.QUEUED)
    m = Metric(
        publication_id=pubs[0].id,
        collected_at=db.now_utc(),
        views=100, likes=10, comments=2,
        shares=1, followers_delta=3,
        raw='{"src":"x"}',
    )
    rid = db.insert_metric(conn, m)
    assert isinstance(rid, int) and rid > 0


def test_insert_llm_call_smoke(conn):
    rid = db.insert_llm_call(
        conn,
        stage="score",
        ref_id="t_abc123",
        model="claude-haiku-4-5",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=0.0001,
        created_at=db.now_utc(),
    )
    assert isinstance(rid, int) and rid > 0


# ── State machine: topics ─────────────────────────────────

@pytest.fixture
def topic_id(conn):
    t = _topic(content_hash="h_top")
    db.insert_topic(conn, t)
    return t.id


def test_topic_legal_raw_to_scored(conn, topic_id):
    db.transition(conn, "topics", topic_id,
                  TopicStatus.RAW, TopicStatus.SCORED)
    assert db.get_topic(conn, topic_id).status == TopicStatus.SCORED


def test_topic_legal_raw_to_rejected(conn, topic_id):
    db.transition(conn, "topics", topic_id,
                  TopicStatus.RAW, TopicStatus.REJECTED)
    assert db.get_topic(conn, topic_id).status == TopicStatus.REJECTED


def test_topic_full_path_raw_to_consumed(conn, topic_id):
    db.transition(conn, "topics", topic_id, TopicStatus.RAW, TopicStatus.SCORED)
    db.transition(conn, "topics", topic_id, TopicStatus.SCORED, TopicStatus.SELECTED)
    db.transition(conn, "topics", topic_id, TopicStatus.SELECTED, TopicStatus.CONSUMED)
    assert db.get_topic(conn, topic_id).status == TopicStatus.CONSUMED


@pytest.mark.parametrize("illegal_to", [
    TopicStatus.SELECTED,    # 跳过 scored
    TopicStatus.CONSUMED,    # 跳过 scored+selected
])
def test_topic_raw_illegal_transitions(conn, topic_id, illegal_to):
    with pytest.raises(IllegalTransition):
        db.transition(conn, "topics", topic_id,
                      TopicStatus.RAW, illegal_to)


def test_topic_scored_to_consumed_illegal(conn, topic_id):
    db.transition(conn, "topics", topic_id, TopicStatus.RAW, TopicStatus.SCORED)
    with pytest.raises(IllegalTransition):
        db.transition(conn, "topics", topic_id,
                      TopicStatus.SCORED, TopicStatus.CONSUMED)


@pytest.mark.parametrize("terminal_status", [
    TopicStatus.CONSUMED,
    TopicStatus.REJECTED,
])
def test_topic_terminal_states_cannot_transition(
    conn, topic_id, terminal_status
):
    """consumed / rejected 是终态：从它们尝试转移全部 IllegalTransition。"""
    # 把行改成终态（直接 UPDATE 模拟）
    conn.execute(
        "UPDATE topics SET status=? WHERE id=?",
        (terminal_status, topic_id),
    )
    conn.commit()
    for to_status in [
        TopicStatus.RAW, TopicStatus.SCORED,
        TopicStatus.SELECTED, TopicStatus.CONSUMED,
        TopicStatus.REJECTED,
    ]:
        with pytest.raises(IllegalTransition):
            db.transition(conn, "topics", topic_id,
                          terminal_status, to_status)


def test_topic_optimistic_lock_stale_state(conn, topic_id):
    """乐观锁：期望 from_status 但当前已是别的 → StaleState。"""
    db.transition(conn, "topics", topic_id,
                  TopicStatus.RAW, TopicStatus.SCORED)
    # 模拟另一进程把它跳到 selected
    conn.execute(
        "UPDATE topics SET status=? WHERE id=?",
        (TopicStatus.SELECTED, topic_id),
    )
    conn.commit()
    with pytest.raises(StaleState) as exc_info:
        db.transition(conn, "topics", topic_id,
                      TopicStatus.SCORED, TopicStatus.SELECTED)
    assert exc_info.value.expected_status == TopicStatus.SCORED
    assert exc_info.value.actual_status == TopicStatus.SELECTED


def test_transition_updates_updated_at(conn, topic_id):
    """成功 transition 后 updated_at 必须更新（TECH_SPEC §3 字段约定）。"""
    sentinel = "2020-01-01T00:00:00+00:00"
    conn.execute(
        "UPDATE topics SET updated_at=? WHERE id=?",
        (sentinel, topic_id),
    )
    conn.commit()
    db.transition(conn, "topics", topic_id,
                  TopicStatus.RAW, TopicStatus.SCORED)
    got = db.get_topic(conn, topic_id)
    assert got.updated_at != sentinel
    assert got.updated_at.startswith("20")


def test_transition_unknown_table_rejected(conn):
    with pytest.raises(ValueError):
        db.transition(conn, "ghost_table", "x", "raw", "scored")


def test_transition_nonexistent_row_raises_illegal(conn):
    """行不存在 → IllegalTransition（保守处理）。"""
    with pytest.raises(IllegalTransition):
        db.transition(conn, "topics", new_id("t"),
                      TopicStatus.RAW, TopicStatus.SCORED)


# ── State machine: contents ───────────────────────────────

@pytest.fixture
def content_id(conn):
    cid = _seed_content(conn)
    return cid


def test_content_legal_draft_to_gated(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)


def test_content_legal_draft_to_discarded(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.DISCARDED)


def test_content_legal_draft_to_failed(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.FAILED)


@pytest.mark.parametrize("illegal_to", [
    ContentStatus.APPROVED,            # 跳过 gated
    ContentStatus.REJECTED_BY_HUMAN,   # 跳过 gated
    ContentStatus.DONE,                # 跳过 gated+approved
])
def test_content_draft_illegal_transitions(conn, content_id, illegal_to):
    with pytest.raises(IllegalTransition):
        db.transition(conn, "contents", content_id,
                      ContentStatus.DRAFT, illegal_to)


def test_content_gated_to_approved(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)
    db.transition(conn, "contents", content_id,
                  ContentStatus.GATED, ContentStatus.APPROVED)


def test_content_gated_to_rejected_by_human(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)
    db.transition(conn, "contents", content_id,
                  ContentStatus.GATED, ContentStatus.REJECTED_BY_HUMAN)


@pytest.mark.parametrize("illegal_to", [
    ContentStatus.GATED,         # 不允许回退
    ContentStatus.DISCARDED,
    ContentStatus.DONE,
    ContentStatus.FAILED,
])
def test_content_gated_illegal_transitions(conn, content_id, illegal_to):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)
    with pytest.raises(IllegalTransition):
        db.transition(conn, "contents", content_id,
                      ContentStatus.GATED, illegal_to)


def test_content_approved_to_done(conn, content_id):
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)
    db.transition(conn, "contents", content_id,
                  ContentStatus.GATED, ContentStatus.APPROVED)
    db.transition(conn, "contents", content_id,
                  ContentStatus.APPROVED, ContentStatus.DONE)


@pytest.mark.parametrize("terminal_status", [
    ContentStatus.DISCARDED,
    ContentStatus.REJECTED_BY_HUMAN,
    ContentStatus.DONE,
    ContentStatus.FAILED,
])
def test_content_terminal_states_cannot_transition(
    conn, content_id, terminal_status
):
    conn.execute(
        "UPDATE contents SET status=? WHERE id=?",
        (terminal_status, content_id),
    )
    conn.commit()
    with pytest.raises(IllegalTransition):
        db.transition(conn, "contents", content_id,
                      terminal_status, ContentStatus.GATED)


def test_content_optimistic_lock_stale_state(conn, content_id):
    """contents 表的乐观锁：手动 UPDATE 改 status 后 transition 抛 StaleState。"""
    db.transition(conn, "contents", content_id,
                  ContentStatus.DRAFT, ContentStatus.GATED)
    # 模拟另一进程把它跳到了 approved
    conn.execute(
        "UPDATE contents SET status=? WHERE id=?",
        (ContentStatus.APPROVED, content_id),
    )
    conn.commit()
    with pytest.raises(StaleState) as exc_info:
        db.transition(conn, "contents", content_id,
                      ContentStatus.GATED, ContentStatus.APPROVED)
    assert exc_info.value.expected_status == ContentStatus.GATED
    assert exc_info.value.actual_status == ContentStatus.APPROVED


# ── State machine: publications ────────────────────────────

@pytest.fixture
def pub_id(conn):
    cid = _seed_content(conn)
    p = _pub(content_id=cid)
    db.insert_publication(conn, p)
    return p.id


def test_pub_queued_to_publishing(conn, pub_id):
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)


def test_pub_queued_to_cancelled(conn, pub_id):
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.CANCELLED)


def test_pub_publishing_to_published(conn, pub_id):
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED)


def test_pub_publishing_to_failed(conn, pub_id):
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.PUBLISHING, PublicationStatus.FAILED)


def test_pub_failed_to_queued_only_legal_reset_path(conn, pub_id):
    """FAILED → QUEUED 合法（仅 reset / UI retry 走，合同约定的特殊路径）。"""
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.PUBLISHING, PublicationStatus.FAILED)
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.FAILED, PublicationStatus.QUEUED)


@pytest.mark.parametrize("illegal_to", [
    PublicationStatus.PUBLISHED,
    PublicationStatus.FAILED,
    PublicationStatus.QUEUED,
])
def test_pub_queued_illegal_transitions(conn, pub_id, illegal_to):
    with pytest.raises(IllegalTransition):
        db.transition(conn, "publications", pub_id,
                      PublicationStatus.QUEUED, illegal_to)


@pytest.mark.parametrize("illegal_from_to", [
    (PublicationStatus.QUEUED, PublicationStatus.PUBLISHED),
    (PublicationStatus.PUBLISHING, PublicationStatus.QUEUED),
    (PublicationStatus.PUBLISHING, PublicationStatus.CANCELLED),
    (PublicationStatus.PUBLISHED, PublicationStatus.FAILED),
    (PublicationStatus.PUBLISHED, PublicationStatus.QUEUED),
    (PublicationStatus.CANCELLED, PublicationStatus.QUEUED),
])
def test_pub_illegal_transitions_matrix(conn, pub_id, illegal_from_to):
    """参数化覆盖非法的 publication 转移对。"""
    from_s, to_s = illegal_from_to
    with pytest.raises(IllegalTransition):
        db.transition(conn, "publications", pub_id, from_s, to_s)


def test_pub_optimistic_lock_stale_state(conn, pub_id):
    db.transition(conn, "publications", pub_id,
                  PublicationStatus.QUEUED, PublicationStatus.PUBLISHING)
    conn.execute(
        "UPDATE publications SET status=? WHERE id=?",
        (PublicationStatus.FAILED, pub_id),
    )
    conn.commit()
    with pytest.raises(StaleState) as exc_info:
        db.transition(conn, "publications", pub_id,
                      PublicationStatus.PUBLISHING,
                      PublicationStatus.PUBLISHED)
    assert exc_info.value.actual_status == PublicationStatus.FAILED


# ── 重跑幂等性（HARD_PARTS §5） ──────────────────────────────

def test_double_init_db_idempotent_with_existing_data(tmp_path):
    """全表落数据后再次 init_db 必须保留所有数据。"""
    p = tmp_path / "dbl.db"
    c = db.connect(p)
    db.init_db(c)
    db.insert_topic(c, _topic(content_hash="kept1"))
    db.init_db(c)
    db.insert_topic(c, _topic(content_hash="kept2"))
    db.init_db(c)
    rows = db.get_topics_by_status(c, TopicStatus.RAW)
    assert {r.content_hash for r in rows} == {"kept1", "kept2"}
    c.close()


def test_repeated_legal_transitions_on_fresh_db(tmp_path):
    """全新 db 跑一系列 transfer——schema 第一次出现即通过。"""
    p = tmp_path / "fresh.db"
    c = db.connect(p)
    db.init_db(c)
    t = _topic(content_hash="h_fresh")
    db.insert_topic(c, t)
    db.transition(c, "topics", t.id, TopicStatus.RAW, TopicStatus.SCORED)
    db.transition(c, "topics", t.id, TopicStatus.SCORED, TopicStatus.SELECTED)
    assert db.get_topic(c, t.id).status == TopicStatus.SELECTED
    c.close()


# ── M10-2 增量：list_*/count_*/get_publications_by_content/recent_activity ────


class TestListTopics:
    """list_topics 按过滤+分页返回；空表/无匹配 → []。"""

    def test_empty_returns_empty_list(self, conn):
        assert db.list_topics(conn) == []

    def test_returns_all_without_filter(self, conn):
        for i in range(3):
            db.insert_topic(conn, _topic(content_hash=f"h_list_{i}"))
        rows = db.list_topics(conn)
        assert len(rows) == 3
        assert all(isinstance(r, Topic) for r in rows)

    def test_filter_by_status(self, conn):
        t1 = _topic(content_hash="h_a")
        db.insert_topic(conn, t1)
        db.transition(conn, "topics", t1.id, TopicStatus.RAW, TopicStatus.SCORED)
        t2 = _topic(content_hash="h_b")
        db.insert_topic(conn, t2)
        scored = db.list_topics(conn, status=TopicStatus.SCORED.value)
        assert len(scored) == 1
        assert scored[0].id == t1.id

    def test_filter_by_pillar(self, conn):
        t_a = _topic(content_hash="h_pa", pillar="ai_daily")
        t_b = _topic(content_hash="h_pb", pillar="finance")
        db.insert_topic(conn, t_a)
        db.insert_topic(conn, t_b)
        out = db.list_topics(conn, pillar="finance")
        assert [t.id for t in out] == [t_b.id]

    def test_limit_offset(self, conn):
        for i in range(5):
            db.insert_topic(conn, _topic(content_hash=f"h_lo_{i}"))
        page1 = db.list_topics(conn, limit=2, offset=0)
        page2 = db.list_topics(conn, limit=2, offset=2)
        assert len(page1) == 2 and len(page2) == 2
        assert {t.id for t in page1}.isdisjoint({t.id for t in page2})

    def test_invalid_filter_column_rejected(self, conn):
        # 合法列不抛（sanity：上方 filter 测试已经覆盖）
        db.list_topics(conn, source="rss:x")
        # 非法列名走底层 helper 直接抛 ValueError
        with pytest.raises(ValueError, match="column"):
            db._build_filter_where(
                db._TOPICS_FILTER_COLS, unknown_col="x"
            )


class TestCountTopics:
    def test_empty(self, conn):
        assert db.count_topics(conn) == 0

    def test_with_filter(self, conn):
        t = _topic(content_hash="h_c")
        db.insert_topic(conn, t)
        db.transition(conn, "topics", t.id, TopicStatus.RAW, TopicStatus.SCORED)
        assert db.count_topics(conn) == 1
        assert db.count_topics(conn, status=TopicStatus.SCORED.value) == 1
        assert db.count_topics(conn, status=TopicStatus.SELECTED.value) == 0


class TestListContents:
    def test_empty(self, conn):
        assert db.list_contents(conn) == []

    def test_filter_pillar(self, conn):
        # contents.topic_id UNIQUE → 不同 content 需不同 topic
        t1 = _topic(content_hash="h_lc1")
        t2 = _topic(content_hash="h_lc2")
        db.insert_topic(conn, t1)
        db.insert_topic(conn, t2)
        c1 = _content(t1.id, pillar="ai_daily")
        c2 = _content(t2.id, pillar="finance")
        db.insert_content(conn, c1)
        db.insert_content(conn, c2)
        out = db.list_contents(conn, pillar="finance")
        assert [c.id for c in out] == [c2.id]


class TestCountContents:
    def test_zero(self, conn):
        assert db.count_contents(conn) == 0

    def test_with_filter(self, conn):
        topic = _topic(content_hash="h_cc")
        db.insert_topic(conn, topic)
        db.insert_content(conn, _content(topic.id))
        assert db.count_contents(conn) == 1
        assert db.count_contents(conn, pillar="ai_daily") == 1
        assert db.count_contents(conn, pillar="finance") == 0


class TestListPublications:
    def test_filter_by_status(self, conn):
        topic = _topic(content_hash="h_lp")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        # 不同 account_id 避免 UNIQUE(content_id, platform, account_id) 冲突
        db.insert_publication(conn, _pub(content.id, account_id="a_q"))
        db.insert_publication(conn, _pub(content.id, account_id="a_p"))
        # a_q 走完整链路 queued→publishing→published；a_p 保持 queued
        rows = db.get_publications_by_content(conn, content.id)
        a_q = next(r for r in rows if r.account_id == "a_q")
        db.transition(
            conn, "publications", a_q.id,
            PublicationStatus.QUEUED, PublicationStatus.PUBLISHING,
        )
        db.transition(
            conn, "publications", a_q.id,
            PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED,
        )
        out = db.list_publications(conn, status=PublicationStatus.QUEUED.value)
        assert len(out) == 1
        assert out[0].account_id == "a_p"

    def test_filter_by_platform(self, conn):
        topic = _topic(content_hash="h_lpf")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        db.insert_publication(conn, _pub(content.id, platform="x", account_id="a1"))
        db.insert_publication(conn, _pub(content.id, platform="toutiao", account_id="a1"))
        out = db.list_publications(conn, platform="toutiao")
        assert len(out) == 1
        assert out[0].platform == "toutiao"


class TestListPublicationsM11B:
    """M11-B 新增只读过滤参数：account_id / pending_only。"""

    def test_filter_by_account_id(self, conn):
        topic = _topic(content_hash="h_lpa")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        db.insert_publication(conn, _pub(content.id, account_id="acct_alice"))
        db.insert_publication(conn, _pub(content.id, platform="x", account_id="acct_bob"))
        out = db.list_publications(conn, account_id="acct_alice")
        assert len(out) == 1
        assert out[0].account_id == "acct_alice"

    def test_pending_only_excludes_published(self, conn):
        topic = _topic(content_hash="h_lpo")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        # 一个 pending(queued) + 一个已 published（直接 INSERT，模拟完整链路结果）
        db.insert_publication(conn, _pub(content.id, account_id="pending_one"))
        db.insert_publication(conn, _pub(
            content.id, platform="x", account_id="done_one",
            status=PublicationStatus.PUBLISHED,
            published_at="2026-07-11T10:00:00+00:00",
        ))
        out = db.list_publications(conn, pending_only=True)
        ids = {r.account_id for r in out}
        assert "pending_one" in ids
        assert "done_one" not in ids

    def test_combined_filters(self, conn):
        """status + account_id + pending_only 三个过滤同时生效。"""
        topic = _topic(content_hash="h_lpc")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        db.insert_publication(conn, _pub(content.id, account_id="u1"))
        db.insert_publication(conn, _pub(content.id, platform="x", account_id="u2"))
        out = db.list_publications(
            conn, status="queued", account_id="u1", pending_only=True,
        )
        assert len(out) == 1
        assert out[0].account_id == "u1"


class TestGetPublicationsByContent:
    def test_returns_empty_for_unknown_content(self, conn):
        assert db.get_publications_by_content(conn, "c_nope") == []

    def test_returns_sorted_by_scheduled_at(self, conn):
        topic = _topic(content_hash="h_gpb")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        p_early = _pub(content.id, account_id="a1", scheduled_at="2026-07-10T10:00:00+00:00")
        p_late = _pub(content.id, account_id="a2", scheduled_at="2026-07-12T10:00:00+00:00")
        db.insert_publication(conn, p_late)
        db.insert_publication(conn, p_early)
        out = db.get_publications_by_content(conn, content.id)
        assert [p.scheduled_at for p in out] == [p_early.scheduled_at, p_late.scheduled_at]


class TestRecentActivity:
    def test_empty(self, conn):
        assert db.recent_activity(conn) == []

    def test_unions_three_tables(self, conn):
        topic = _topic(content_hash="h_ra")
        db.insert_topic(conn, topic)
        content = _content(topic.id)
        db.insert_content(conn, content)
        pub = _pub(content.id)
        db.insert_publication(conn, pub)
        rows = db.recent_activity(conn)
        kinds = {r["kind"] for r in rows}
        assert kinds == {"topic", "content", "publication"}
        for r in rows:
            assert {"id", "kind", "status", "updated_at"} <= set(r.keys())

    def test_ordering_by_updated_at_desc(self, conn):
        t = _topic(content_hash="h_rao")
        db.insert_topic(conn, t)
        # 直接 update_at 改一个
        conn.execute(
            "UPDATE topics SET updated_at=? WHERE id=?",
            ("2026-01-01T00:00:00+00:00", t.id),
        )
        conn.commit()
        t2 = _topic(content_hash="h_rao2")
        db.insert_topic(conn, t2)
        out = db.recent_activity(conn, limit=2)
        assert out[0]["id"] == t2.id  # 新的（默认 created_at 更新）排前
