"""SQLite 封装 + 状态机迁移。

TECH_SPEC §3 全部 DDL 在此。状态机集中在 transition() 强制。
任何对状态的写都必须走 transition()，禁止绕过（URL、CLI、UI 都一样）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.models import (
    Content,
    ContentStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
    CONTENT_TRANSITIONS,
    PUBLICATION_TRANSITIONS,
    TOPIC_TRANSITIONS,
)
from pipeline.utils.errors import IllegalTransition, StaleState


# 状态机表 — keys 受白名单控制，transition() 拒绝其他输入
_TRANSITIONS = {
    "topics": TOPIC_TRANSITIONS,
    "contents": CONTENT_TRANSITIONS,
    "publications": PUBLICATION_TRANSITIONS,
}
_STATE_TABLES = frozenset(_TRANSITIONS)


# ── Schema DDL（TECH_SPEC §3）────────────────────────────────
_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT,
    summary       TEXT,
    content_hash  TEXT NOT NULL UNIQUE,
    pillar        TEXT,
    score         REAL,
    score_reason  TEXT,
    status        TEXT NOT NULL DEFAULT 'raw',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contents (
    id               TEXT PRIMARY KEY,
    topic_id         TEXT NOT NULL UNIQUE REFERENCES topics(id),
    pillar           TEXT NOT NULL,
    title            TEXT NOT NULL,
    canonical_path   TEXT NOT NULL,
    formats          TEXT NOT NULL DEFAULT '[]',
    gate_score_total REAL,
    gate_scores      TEXT,
    gate_verdict     TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    -- M-x：封面图 + 文中插图路径
    cover_path       TEXT,
    inline_images    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS publications (
    id               TEXT PRIMARY KEY,
    content_id       TEXT NOT NULL REFERENCES contents(id),
    platform         TEXT NOT NULL,
    account_id       TEXT NOT NULL,
    scheduled_at     TEXT NOT NULL,
    published_at     TEXT,
    platform_post_id TEXT,
    platform_url     TEXT,
    error            TEXT,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'queued',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(content_id, platform, account_id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id  TEXT NOT NULL REFERENCES publications(id),
    collected_at    TEXT NOT NULL,
    views           INTEGER,
    likes           INTEGER,
    comments        INTEGER,
    shares          INTEGER,
    followers_delta INTEGER,
    raw             TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stage         TEXT NOT NULL,
    ref_id        TEXT,
    model         TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);
"""


# ── Connection / init ──────────────────────────────────────

def connect(path: str | Path = "state.db") -> sqlite3.Connection:
    """打开 SQLite 连接，启用 WAL + 外键 + Row factory。

    数据库文件不存在会自动创建。要让表存在，调用 init_db()。
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """幂等建表，多次调用无副作用。"""
    conn.executescript(_SCHEMA)
    _migrate_add_cover_path(conn)  # M-x：老库平滑升级
    conn.commit()


def _migrate_add_cover_path(conn: sqlite3.Connection) -> None:
    """M-x 迁移：老 state.db 加 cover_path / inline_images 列（若缺）。

    SQLite 不支持 IF NOT EXISTS on ADD COLUMN——靠 PRAGMA table_info 检测。
    """
    cur = conn.execute("PRAGMA table_info(contents)")
    cols = {row[1] for row in cur.fetchall()}
    if "cover_path" not in cols:
        conn.execute("ALTER TABLE contents ADD COLUMN cover_path TEXT")
    if "inline_images" not in cols:
        conn.execute(
            "ALTER TABLE contents ADD COLUMN inline_images TEXT NOT NULL DEFAULT '[]'"
        )


def now_utc() -> str:
    """ISO8601 UTC 字符串。TECH_SPEC §10 约定存储一律 UTC。"""
    return datetime.now(timezone.utc).isoformat()


# ── Row → dataclass ────────────────────────────────────────

def _row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        url=row["url"],
        summary=row["summary"],
        content_hash=row["content_hash"],
        pillar=row["pillar"],
        score=row["score"],
        score_reason=row["score_reason"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_content(row: sqlite3.Row) -> Content:
    return Content(
        id=row["id"],
        topic_id=row["topic_id"],
        pillar=row["pillar"],
        title=row["title"],
        canonical_path=row["canonical_path"],
        formats=tuple(json.loads(row["formats"])),
        gate_score_total=row["gate_score_total"],
        gate_scores=json.loads(row["gate_scores"]) if row["gate_scores"] else None,
        gate_verdict=row["gate_verdict"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        # M-x：图路径字段，老库 SELECT 没这些列时取空
        cover_path=row["cover_path"] if "cover_path" in row.keys() else None,
        inline_images=tuple(
            json.loads(row["inline_images"])
            if "inline_images" in row.keys() and row["inline_images"]
            else []
        ),
    )


def _row_to_publication(row: sqlite3.Row) -> Publication:
    return Publication(
        id=row["id"],
        content_id=row["content_id"],
        platform=row["platform"],
        account_id=row["account_id"],
        scheduled_at=row["scheduled_at"],
        published_at=row["published_at"],
        platform_post_id=row["platform_post_id"],
        platform_url=row["platform_url"],
        error=row["error"],
        retry_count=row["retry_count"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── topics ────────────────────────────────────────────────

def insert_topic(conn: sqlite3.Connection, t: Topic) -> None:
    """插入一条 topic。content_hash UNIQUE 重复时抛 sqlite3.IntegrityError。"""
    conn.execute(
        """
        INSERT INTO topics
            (id, source, title, url, summary, content_hash, pillar,
             score, score_reason, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            t.id, t.source, t.title, t.url, t.summary, t.content_hash,
            t.pillar, t.score, t.score_reason, t.status,
            t.created_at, t.updated_at,
        ),
    )
    conn.commit()


# summary 入库前最大字符数（TECH_SPEC §3 topics.summary 字段口径）
_SUMMARY_MAX_CHARS = 2000


def try_insert_topic(
    conn: sqlite3.Connection,
    raw: "RawItem",
    source: str,
    now: str,
) -> tuple[Topic, bool]:
    """INSERT OR IGNORE 一条 topic；按 content_hash 去重。

    Args:
        conn: SQLite 连接
        raw: SourceAdapter.fetch() 产出的标准化条目
        source: 数据源标识（如 'rss:hn'）
        now: ISO8601 UTC 时间字符串

    Returns:
        (Topic, is_new)
          - is_new=True：新插入，返回带新 id 的 Topic
          - is_new=False：content_hash 已存在，返回库中已有 Topic（保留首次
            入库的 source/title/url 等，重复调用不会覆盖）

    与 insert_topic 区别：本函数用 INSERT OR IGNORE 不抛异常，专为 ingest
    编排"批量入库 + 重复计数"设计。
    """
    from pipeline.sources.base import RawItem  # noqa: F401 — type hint
    from pipeline.sources.dedup import content_hash
    from pipeline.utils.ids import new_id

    h = content_hash(raw.title, raw.url)
    summary = (raw.summary or "")[:_SUMMARY_MAX_CHARS] or None

    new_topic_id = new_id("t")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO topics
            (id, source, title, url, summary, content_hash, pillar,
             score, score_reason, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_topic_id, source, raw.title, raw.url, summary, h,
            None, None, None, TopicStatus.RAW.value, now, now,
        ),
    )
    conn.commit()

    if cur.rowcount == 1:
        row = conn.execute(
            "SELECT * FROM topics WHERE id=?", (new_topic_id,)
        ).fetchone()
        return _row_to_topic(row), True

    # content_hash 已存在 → 返回已有 Topic（不覆盖）
    row = conn.execute(
        "SELECT * FROM topics WHERE content_hash=?", (h,)
    ).fetchone()
    return _row_to_topic(row), False


def get_topic(conn: sqlite3.Connection, topic_id: str) -> Topic | None:
    row = conn.execute(
        "SELECT * FROM topics WHERE id=?", (topic_id,)
    ).fetchone()
    return _row_to_topic(row) if row else None


def get_topics_by_status(
    conn: sqlite3.Connection, status: str
) -> list[Topic]:
    rows = conn.execute(
        "SELECT * FROM topics WHERE status=? ORDER BY created_at",
        (status,),
    ).fetchall()
    return [_row_to_topic(r) for r in rows]


# ── contents ──────────────────────────────────────────────

def insert_content(conn: sqlite3.Connection, c: Content) -> None:
    conn.execute(
        """
        INSERT INTO contents
            (id, topic_id, pillar, title, canonical_path, formats,
             gate_score_total, gate_scores, gate_verdict,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            c.id, c.topic_id, c.pillar, c.title, c.canonical_path,
            json.dumps(list(c.formats)),
            c.gate_score_total,
            json.dumps(c.gate_scores) if c.gate_scores is not None else None,
            c.gate_verdict,
            c.status, c.created_at, c.updated_at,
        ),
    )
    conn.commit()


def get_content(conn: sqlite3.Connection, content_id: str) -> Content | None:
    row = conn.execute(
        "SELECT * FROM contents WHERE id=?", (content_id,)
    ).fetchone()
    return _row_to_content(row) if row else None


def get_contents_by_status(
    conn: sqlite3.Connection, status: str
) -> list[Content]:
    rows = conn.execute(
        "SELECT * FROM contents WHERE status=? ORDER BY created_at",
        (status,),
    ).fetchall()
    return [_row_to_content(r) for r in rows]


# ── publications ──────────────────────────────────────────

def insert_publication(
    conn: sqlite3.Connection, p: Publication
) -> None:
    conn.execute(
        """
        INSERT INTO publications
            (id, content_id, platform, account_id, scheduled_at, published_at,
             platform_post_id, platform_url, error, retry_count,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.id, p.content_id, p.platform, p.account_id,
            p.scheduled_at, p.published_at,
            p.platform_post_id, p.platform_url,
            p.error, p.retry_count,
            p.status, p.created_at, p.updated_at,
        ),
    )
    conn.commit()


def get_publication(
    conn: sqlite3.Connection, publication_id: str
) -> Publication | None:
    row = conn.execute(
        "SELECT * FROM publications WHERE id=?", (publication_id,)
    ).fetchone()
    return _row_to_publication(row) if row else None


def get_publications_by_status(
    conn: sqlite3.Connection, status: str
) -> list[Publication]:
    rows = conn.execute(
        "SELECT * FROM publications WHERE status=? "
        "ORDER BY scheduled_at",
        (status,),
    ).fetchall()
    return [_row_to_publication(r) for r in rows]


# ── metrics / llm_calls (无状态机，按需后续补查询) ──────────

def insert_metric(conn: sqlite3.Connection, m: Metric) -> int:
    """插入一条 metrics 快照，返回 rowid（同 metrics.id）。"""
    cur = conn.execute(
        """
        INSERT INTO metrics
            (publication_id, collected_at, views, likes, comments,
             shares, followers_delta, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            m.publication_id, m.collected_at,
            m.views, m.likes, m.comments,
            m.shares, m.followers_delta, m.raw,
        ),
    )
    conn.commit()
    return cur.lastrowid or 0


def insert_llm_call(conn: sqlite3.Connection, **fields) -> int:
    """插入一条 llm_calls 记录。fields: stage/ref_id/model/input_tokens/
    output_tokens/cost_usd/created_at。返回 rowid。"""
    cur = conn.execute(
        """
        INSERT INTO llm_calls
            (stage, ref_id, model, input_tokens, output_tokens,
             cost_usd, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fields["stage"],
            fields.get("ref_id"),
            fields["model"],
            fields.get("input_tokens"),
            fields.get("output_tokens"),
            fields.get("cost_usd"),
            fields["created_at"],
        ),
    )
    conn.commit()
    return cur.lastrowid or 0


# ── 只读查询助手（CLI status / webui dashboard 共用） ────────
#
# S8-1 引入：仅 SELECT，不写库；为 status / future dashboard 提供
# 复用的查询入口（不留 SQLite 散落的副本）。


def count_by_status(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    """统计指定表按 status 分组的行数。

    只读 SELECT。空表 → 空 dict（由调用方按需补 0）。
    table 仅接受 "topics" / "contents" / "publications"。
    """
    if table not in _STATE_TABLES:
        raise ValueError(
            f"count_by_status: table must be one of "
            f"{sorted(_STATE_TABLES)}, got {table!r}"
        )
    rows = conn.execute(
        f'SELECT status, COUNT(*) AS n FROM "{table}" GROUP BY status'
    ).fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def sum_llm_cost_this_month(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> float:
    """本月 LLM 花费（USD）。空表 / 本月无调用 → 0.0。

    「本月」按 ISO 月份前缀匹配（YYYY-MM），等价于
    `created_at >= <当月1号ISO>`，因为 llm_calls.created_at 是 ISO8601 UTC。

    Args:
        now: 用于判断「本月」的参考时间；缺省 = `datetime.now(timezone.utc)`。
             测试可注入固定 `now` 以断言。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) AS used "
        "FROM llm_calls WHERE substr(created_at, 1, 7) = ?",
        (month_prefix,),
    ).fetchone()
    return float(row["used"])


# ── 只读列表/筛选查询助手（M10-2 引入） ──────────────────────
#
# 全部仅 SELECT，不写库；为 webui API（dashboard/topics/contents/
# publications 列表页）提供过滤+分页的查询入口。空表 / 无匹配 → 空 list。
# 复用上方私有 _row_to_* mapper，保持 dataclass 构造一致性。
#
# 设计要点：
#   - 所有函数接受 keyword-only 过滤参数（status/pillar/source/platform），
#     简化调用方代码（不必记得位置参数顺序）
#   - limit/offset 默认值保守（50/0）防止「没传 limit 把全表拉回」的内存风险
#   - 不做 status 白名单校验（filters 透传给 SQL，调用方负责；与既有
#     count_by_status 的白名单策略不同，因为列表查询允许任意 status 子集，
#     而 status 是 enum 调用方约定）

# 各表的「可过滤列」白名单——防止 SQL 注入。filter 列名字符串硬编码到 SQL。
_TOPICS_FILTER_COLS = frozenset({"status", "pillar", "source"})
_CONTENTS_FILTER_COLS = frozenset({"status", "pillar"})
_PUBS_FILTER_COLS = frozenset({"status", "platform"})


def _build_filter_where(
    conn_columns: frozenset[str], **filters,
) -> tuple[str, list]:
    """从 kwargs 过滤参数构造 WHERE 子句 + 绑定值列表。

    只接受 conn_columns 白名单内的列名；其它列抛 ValueError。
    任意参数为 None → 不加条件。
    返回 ("WHERE ..." | "", [vals])。注意「无任何过滤」返回空串而非
    "WHERE 1=1"——简化调用方拼接。
    """
    clauses = []
    vals = []
    for col, val in filters.items():
        if col not in conn_columns:
            raise ValueError(
                f"_build_filter_where: column {col!r} not in "
                f"{sorted(conn_columns)}"
            )
        if val is None:
            continue
        clauses.append(f'"{col}" = ?')
        vals.append(val)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, vals


def list_topics(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    pillar: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Topic]:
    """按过滤条件列出 topics（按 updated_at DESC 排序，LIMIT/OFFSET）。

    只读 SELECT。limit/offset 由调用方负责「不爆内存」——limit 上限
    由 webui API 层再做一次硬封顶（默认 200）。
    """
    where, vals = _build_filter_where(
        _TOPICS_FILTER_COLS, status=status, pillar=pillar, source=source,
    )
    rows = conn.execute(
        f'SELECT * FROM topics {where} '
        f'ORDER BY updated_at DESC, id LIMIT ? OFFSET ?',
        (*vals, limit, offset),
    ).fetchall()
    return [_row_to_topic(r) for r in rows]


def count_topics(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    pillar: str | None = None,
    source: str | None = None,
) -> int:
    """与 list_topics 对应的计数（同样过滤条件）。"""
    where, vals = _build_filter_where(
        _TOPICS_FILTER_COLS, status=status, pillar=pillar, source=source,
    )
    row = conn.execute(
        f'SELECT COUNT(*) AS n FROM topics {where}', vals,
    ).fetchone()
    return int(row["n"])


def list_contents(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    pillar: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Content]:
    """按过滤条件列出 contents（按 updated_at DESC 排序）。"""
    where, vals = _build_filter_where(
        _CONTENTS_FILTER_COLS, status=status, pillar=pillar,
    )
    rows = conn.execute(
        f'SELECT * FROM contents {where} '
        f'ORDER BY updated_at DESC, id LIMIT ? OFFSET ?',
        (*vals, limit, offset),
    ).fetchall()
    return [_row_to_content(r) for r in rows]


def count_contents(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    pillar: str | None = None,
) -> int:
    """与 list_contents 对应的计数。"""
    where, vals = _build_filter_where(
        _CONTENTS_FILTER_COLS, status=status, pillar=pillar,
    )
    row = conn.execute(
        f'SELECT COUNT(*) AS n FROM contents {where}', vals,
    ).fetchone()
    return int(row["n"])


def list_publications(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    platform: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Publication]:
    """按过滤条件列出 publications（按 scheduled_at ASC 排序——日历语义）。

    注意：contents/topics 用 updated_at DESC（最近活动优先），publications
    用 scheduled_at ASC（即将发布的在前），这是 M3-1 / M4-4 的日历语义。
    """
    where, vals = _build_filter_where(
        _PUBS_FILTER_COLS, status=status, platform=platform,
    )
    rows = conn.execute(
        f'SELECT * FROM publications {where} '
        f'ORDER BY scheduled_at ASC, id LIMIT ? OFFSET ?',
        (*vals, limit, offset),
    ).fetchall()
    return [_row_to_publication(r) for r in rows]


def get_publications_by_content(
    conn: sqlite3.Connection, content_id: str,
) -> list[Publication]:
    """列出一条 content 的所有 publications（按 scheduled_at ASC）。

    内容详情页（M10-4 内容详情）需要展示「这条内容排到了哪些平台什么时间」。
    不存在该 content → 空 list（FK 保证不会出错，只是空）。
    """
    rows = conn.execute(
        "SELECT * FROM publications WHERE content_id=? "
        "ORDER BY scheduled_at ASC",
        (content_id,),
    ).fetchall()
    return [_row_to_publication(r) for r in rows]


def recent_activity(
    conn: sqlite3.Connection, *, limit: int = 20,
) -> list[dict]:
    """三表（topics/contents/publications）最近 updated_at 活动合并。

    返回 list[dict]，每项：
        {id, kind, status, updated_at}
    ORDER BY updated_at DESC, LIMIT ?。

    只读 UNION——三表结构不强制同构（kind 标来源表区分）。
    用于 webui dashboard 的「近期活动」面板。
    """
    rows = conn.execute(
        """
        SELECT id, 'topic' AS kind, status, updated_at FROM topics
        UNION ALL
        SELECT id, 'content' AS kind, status, updated_at FROM contents
        UNION ALL
        SELECT id, 'publication' AS kind, status, updated_at FROM publications
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "status": r["status"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


# ── 状态机 ─────────────────────────────────────────────────

def transition(
    conn: sqlite3.Connection,
    table: str,
    row_id: str,
    from_status: str,
    to_status: str,
) -> None:
    """转移一行记录的 status。

    行为：
      - (table, from_status, to_status) 不在合法转移表中 → IllegalTransition
      - 当前 status 不是 from_status（被另一流程改了） → StaleState
      - 行不存在                                   → IllegalTransition

    成功时 updated_at 自动更新为 now_utc()。
    """
    if table not in _STATE_TABLES:
        raise ValueError(f"unknown table: {table}")

    allowed = _TRANSITIONS[table].get(from_status, set())
    if to_status not in allowed:
        raise IllegalTransition(table, from_status, to_status)

    new_updated_at = now_utc()
    cur = conn.execute(
        f'UPDATE "{table}" SET status=?, updated_at=? '
        "WHERE id=? AND status=?",
        (to_status, new_updated_at, row_id, from_status),
    )
    conn.commit()

    if cur.rowcount == 1:
        return

    # rowcount == 0：行不存在 或 状态不匹配。区分以便上层决策。
    existing = conn.execute(
        f'SELECT status FROM "{table}" WHERE id=?', (row_id,)
    ).fetchone()
    if existing is None:
        # 合同未规定此类语义，按 IllegalTransition 处理（保守）
        raise IllegalTransition(table, from_status, to_status)
    raise StaleState(table, row_id, from_status, existing["status"])


# ── 状态条件 UPDATE（webui 等非 transition 调用方） ──────────
#
# 与 transition() 的区别：这些函数不改 status 字段，而是改业务字段
# （contents.gate_verdict / publications.scheduled_at），但仍要求
# 当前 status 必须等于 expect_status——保留乐观锁语义。
# TECH_SPEC §7：「UI 不得直接写 SQL」，所以即使是 status 条件 UPDATE
# 也封装在此，webui 路由不应再出现 conn.execute("UPDATE ...")。


def set_gate_verdict(
    conn: sqlite3.Connection,
    content_id: str,
    verdict: str,
    *,
    expect_status: str,
) -> int:
    """UPDATE contents SET gate_verdict=?, updated_at=? WHERE id=? AND status=?

    Args:
        conn: SQLite 连接
        content_id: 内容 id
        verdict: 新的 gate_verdict（如 "REJECTED_BY_HUMAN: 理由"）
        expect_status: 期望当前 status（通常为 ContentStatus.GATED.value）

    Returns:
        cursor.rowcount（1=成功，0=行不存在或状态不匹配）。
        内部 conn.commit()。
    """
    cur = conn.execute(
        "UPDATE contents SET gate_verdict=?, updated_at=? "
        "WHERE id=? AND status=?",
        (verdict, now_utc(), content_id, expect_status),
    )
    conn.commit()
    return cur.rowcount


def reschedule_publication(
    conn: sqlite3.Connection,
    pub_id: str,
    scheduled_at: str,
    *,
    expect_status: str,
) -> int:
    """UPDATE publications SET scheduled_at=?, updated_at=? WHERE id=? AND status=?

    Args:
        conn: SQLite 连接
        pub_id: publication id
        scheduled_at: 新的 ISO8601 UTC 时间字符串
        expect_status: 期望当前 status（通常为 PublicationStatus.QUEUED.value）

    Returns:
        cursor.rowcount（1=成功，0=行不存在或状态不匹配）。
        内部 conn.commit()。
    """
    cur = conn.execute(
        "UPDATE publications SET scheduled_at=?, updated_at=? "
        "WHERE id=? AND status=?",
        (scheduled_at, now_utc(), pub_id, expect_status),
    )
    conn.commit()
    return cur.rowcount
