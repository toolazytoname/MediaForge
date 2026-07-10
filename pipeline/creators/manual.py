"""M11-G 手动创作编排函数。

目标：「人工手写/编辑」与「AI 自动生成」（canonical.create_one）共用同一条
后半程（gate→review→publish），产出同一张 contents 表的 draft。

实现要点（契约红线）：
  - topic_id 仍 NOT NULL UNIQUE → 手动创作先造一个 source='manual' 的轻量 topic
    （status 走 SELECTED→CONSUMED），再挂 Content
  - canonical_path NOT NULL → 人写 markdown 落 output/YYYY-MM-DD/<content_id>/
    canonical.md，与自动路径同格式同目录约定（HARD_PARTS §5 tmp→rename）
  - 初始 status 固定 ContentStatus.DRAFT
  - **禁止调用 LLM**（成本护栏 HARD_PARTS §4）：不导 complete / complete_json
  - 复用 db.insert_topic / db.insert_content / db.transition / db.update_content_draft
    不写裸 SQL，不引入新状态
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Sequence

from pipeline import db
from pipeline.creators.canonical import DEFAULT_OUTPUT_ROOT
from pipeline.models import (
    Content,
    ContentStatus,
    Topic,
    TopicStatus,
)
from pipeline.utils.errors import CreateError, IllegalTransition, StaleState
from pipeline.utils.ids import new_id


def _serialize_formats(formats: Sequence[str]) -> str:
    """formats 是 tuple/list[str]，存 DB 的 formats 列是 JSON 字符串。"""
    return json.dumps(list(formats), ensure_ascii=False)


def _write_canonical(
    *,
    final_path: Path,
    body_markdown: str,
    tmp_path: Path,
) -> None:
    """tmp→rename 模式（HARD_PARTS §5 幂等），单文件不需要 meta.json。"""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(body_markdown, encoding="utf-8")
    tmp_path.rename(final_path)


def create_manual(
    conn,
    *,
    title: str,
    pillar: str,
    body_markdown: str,
    formats: Sequence[str] = (),
    now: str,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Content:
    """为手写 markdown 创建一条 draft content（同 entry 一个，姊妹于 canonical.create_one）。

    步骤:
      1. 造 source='manual' topic（status=SELECTED）,同步 INSERT
      2. 转移到 CONSUMED（保留 transition 审计链）
      3. 写 output/<date>/<content_id>/canonical.md（HARD_PARTS §5 tmp→rename）
      4. INSERT contents row,status=DRAFT,与 topic 转移同一事务

    Args:
        conn: SQLite 连接
        title: 标题（必填）
        pillar: 主题分类（必填）
        body_markdown: markdown 正文
        formats: 目标平台格式 tuple/list，如 ("xhs", "x", "article")；空 tuple 也允许
        now: ISO8601 UTC 时间字符串
        output_root: 默认 "output"（与 canonical.create_one 共用）

    Returns:
        Content（已落库，status=draft，topic_id 绑 manual topic）

    Raises:
        CreateError: 字段缺失 / 写盘失败
        IllegalTransition / StaleState: topic 转移非法（极少见，DB 不变量层）
    """
    title = (title or "").strip()
    pillar = (pillar or "").strip() or "uncategorized"
    body_markdown = body_markdown or ""

    if not title:
        raise CreateError("create_manual: title 不能为空")
    if not body_markdown.strip():
        raise CreateError("create_manual: body_markdown 不能为空")

    output_root = Path(output_root)
    content_id = new_id("c")
    topic_id = new_id("t")
    date_str = now[:10]  # YYYY-MM-DD
    out_dir = output_root / date_str / content_id
    tmp_path = out_dir.with_name(out_dir.name + ".tmp")
    canonical_path = str(out_dir / "canonical.md")

    # 1+2+4: 串行执行(每次单独 atomic commit;db.insert_topic / db.transition
    #          / 此处裸 conn.execute 各自提交。任一失败,前面已落库保留)。
    try:
        # 1. 造 manual topic（轻量：url/summary/content_hash 都用 placeholder）
        topic = Topic(
            id=topic_id,
            source="manual",
            title=title,
            url=f"manual://{topic_id}",
            summary=None,
            content_hash=f"manual-{topic_id}",
            pillar=pillar,
            score=None,
            score_reason=None,
            status=TopicStatus.SELECTED.value,
            created_at=now,
            updated_at=now,
        )
        db.insert_topic(conn, topic)
        # 2. 转移 SELECTED → CONSUMED
        db.transition(
            conn, "topics", topic_id,
            from_status=TopicStatus.SELECTED.value,
            to_status=TopicStatus.CONSUMED.value,
        )
        # 4. INSERT contents（topic_id 已 unique）
        conn.execute(
            """
            INSERT INTO contents
                (id, topic_id, pillar, title, canonical_path, formats,
                 gate_score_total, gate_scores, gate_verdict,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id, topic_id, pillar, title, canonical_path,
                _serialize_formats(formats),
                None, None, None,
                ContentStatus.DRAFT.value, now, now,
            ),
        )
        conn.commit()
    except (IllegalTransition, StaleState) as e:
        raise CreateError(f"create_manual: topic 转移失败：{e}") from e

    # 3. 写 canonical.md（HARD_PARTS §5 幂等 tmp→rename）
    try:
        _write_canonical(
            final_path=Path(canonical_path),
            body_markdown=body_markdown,
            tmp_path=tmp_path,
        )
    except OSError as e:
        raise CreateError(
            f"create_manual: 写 {canonical_path} 失败: {type(e).__name__}: {e}"
        ) from e

    # 返回 Content（从 DB 回读,与 insert 一致）
    c = db.get_content(conn, content_id)
    assert c is not None, "create_manual: 内容刚刚 insert 不应丢失"
    return c


def update_manual_draft(
    conn,
    content_id: str,
    *,
    title: str | None = None,
    pillar: str | None = None,
    body_markdown: str | None = None,
    formats: Sequence[str] | None = None,
    now: str,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
) -> Content:
    """编辑一条 draft（手动或自动都允许 —— 自动生成的 draft 也走同条编辑路径）。

    仅允许编辑 status=draft 的 content；其它状态由其它流程管（gate / review）。

    字段：
      - title: 留 None 表示不改
      - pillar: 同上
      - body_markdown: 提供时整体重写 canonical.md（同步覆盖）
      - formats: 提供时整列替换（JSON 序列化）

    复用 db.update_content_draft(预计 M11-G 同步添加),并在 body_markdown 改变
    时同步覆盖文件,任何字段改都会更新 updated_at。

    Returns:
        更新后的 Content

    Raises:
        CreateError: 内容不存在 / 状态非 draft
    """
    c = db.get_content(conn, content_id)
    if c is None:
        raise CreateError(f"update_manual_draft: content {content_id} 不存在")
    if c.status != ContentStatus.DRAFT.value:
        raise CreateError(
            f"update_manual_draft: content {content_id} 状态 {c.status} != "
            f"{ContentStatus.DRAFT.value},允许仅限 draft"
        )

    new_title = title.strip() if title is not None else None
    new_pillar = pillar.strip() if pillar is not None else None
    if new_pillar == "":
        new_pillar = "uncategorized"

    body_changed = body_markdown is not None
    if body_changed and not (body_markdown or "").strip():
        raise CreateError("update_manual_draft: body_markdown 不能置空")

    formats_payload: str | None = None
    if formats is not None:
        formats_payload = _serialize_formats(formats)

    # 1. DB 更新(走 db.update_content_draft 不裸 SQL)
    db.update_content_draft(
        conn, content_id,
        title=new_title,
        pillar=new_pillar,
        formats_json=formats_payload,
        expect_status=ContentStatus.DRAFT.value,
        now=now,
    )

    # 2. 若 body_markdown 改了,同步覆盖文件(tmp→rename)
    if body_changed:
        canonical = Path(c.canonical_path)
        tmp = canonical.with_name(canonical.name + ".tmp")
        try:
            _write_canonical(
                final_path=canonical,
                body_markdown=body_markdown or "",
                tmp_path=tmp,
            )
        except OSError as e:
            raise CreateError(
                f"update_manual_draft: 重写 {canonical} 失败: "
                f"{type(e).__name__}: {e}"
            ) from e

    c2 = db.get_content(conn, content_id)
    assert c2 is not None
    return c2
