"""Project delivery orchestration: preview / export / wechat draft (RFC §5.4–5.5)."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pipeline import approvals, db, project_exports, projects as project_store
from pipeline.config import AppConfig, DeliveryConfig, PublishConfig
from pipeline.deliverables import Deliverable, get_deliverable
from pipeline.delivery.materialize import materialize_wechat_article, project_content_hash
from pipeline.delivery.store import (
    DeliveryAttempt,
    LegacyBinding,
    get_attempt_by_key,
    get_binding,
    insert_attempt,
    insert_audit,
    make_idempotency_key,
    request_hash,
    upsert_binding,
)
from pipeline.models import Content, ContentStatus, Publication, PublicationStatus, Topic, TopicStatus
from pipeline.publishers.base import AccountConfig, PublishResult, PublisherAdapter
from pipeline.publishers.capability_registry import mode_allowed
from pipeline.publishers.safe_publish import SafePublishResult, safe_publish
from pipeline.utils.ids import new_id

_PROJECT_PILLAR = "project"


class DeliveryError(ValueError):
    def __init__(self, message: str, *, http_status: int = 400, code: str = "delivery_error"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


@dataclass(frozen=True)
class DeliveryResult:
    attempt: DeliveryAttempt
    replayed: bool = False
    export: project_exports.ProjectExport | None = None
    publication_id: str | None = None
    media_id: str | None = None


def bridge_enabled(cfg: AppConfig | DeliveryConfig | None = None) -> bool:
    if cfg is None:
        return True
    delivery = cfg if isinstance(cfg, DeliveryConfig) else getattr(cfg, "delivery", None)
    if delivery is None:
        return True
    return getattr(delivery, "bridge", "on") == "on"


def _require_bridge(cfg: AppConfig | None) -> None:
    if not bridge_enabled(cfg):
        raise DeliveryError("delivery bridge is off", http_status=403, code="delivery_bridge_off")


def _approval_or_409(project_id: str, projects_root: str | Path) -> approvals.ApprovalStatus:
    state = approvals.status(project_id, projects_root=projects_root)
    if not state.complete or state.stale or state.approval.snapshot is None:
        raise DeliveryError("content package is not approved", http_status=409, code="not_approved")
    return state


def preview_deliverable(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str,
    actor: str,
    adapter: PublisherAdapter | None = None,
    account: AccountConfig | None = None,
    cfg: AppConfig | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> DeliveryResult:
    """Local preview via safe_publish(dry_run=True). Official tables stay unchanged."""
    _require_bridge(cfg)
    deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    platform = _single_platform(deliverable)
    if not mode_allowed(platform, "preview", adapter):
        raise DeliveryError(f"{platform} preview is not available", code="mode_not_allowed")
    fingerprint = "preview"
    key = make_idempotency_key(
        project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, platform=platform,
        account_id=account.id if account else "preview", mode="preview",
        approval_fingerprint=fingerprint, preview_nonce=uuid4().hex,
    )
    req = request_hash({"project_id": project_id, "deliverable_id": deliverable.id, "mode": "preview"})
    outcome = "success"
    error = None
    receipt = None
    post_id = None
    if adapter is not None and account is not None:
        materialized = materialize_wechat_article(
            project_id, deliverable, content_id=f"c_preview_{uuid4().hex[:8]}",
            projects_root=projects_root,
        )
        ephemeral_content = Content(
            id=materialized.content_id, topic_id="t_preview", pillar=_PROJECT_PILLAR,
            title=materialized.title, canonical_path=str(materialized.canonical_path),
            formats=(platform,), gate_score_total=None, gate_scores=None, gate_verdict=None,
            status=ContentStatus.APPROVED.value, created_at=db.now_utc(), updated_at=db.now_utc(),
        )
        ephemeral_pub = Publication(
            id="p_preview", content_id=ephemeral_content.id, platform=platform,
            account_id=account.id, scheduled_at=db.now_utc(), published_at=None,
            platform_post_id=None, platform_url=None, error=None, retry_count=0,
            status=PublicationStatus.QUEUED.value, created_at=db.now_utc(), updated_at=db.now_utc(),
        )
        preview_cfg = PublishConfig(enabled=True, allowed_platforms=[platform])
        result = safe_publish(
            conn, ephemeral_pub, adapter, config=preview_cfg, account=account,
            dry_run=True, now_iso=db.now_utc(), content=ephemeral_content,
        )
        if result.reason and result.reason != "dry-run preview":
            outcome = "failure"
            error = result.reason
        receipt = json_receipt(result)
        post_id = result.platform_post_id
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id=account.id if account else "preview",
        mode="preview", outcome=outcome, idempotency_key=key,
        request_hash_value=req, actor=actor, raw_receipt=receipt, error=error,
        platform_post_id=post_id,
    )
    insert_audit(
        conn, actor=actor, action="delivery.preview",
        payload={"deliverable_id": deliverable.id, "outcome": outcome},
        project_id=project_id, deliverable_id=deliverable.id,
    )
    return DeliveryResult(attempt)


def create_export_delivery(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str | None,
    actor: str,
    cfg: AppConfig | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
) -> DeliveryResult:
    _require_bridge(cfg)
    state = _approval_or_409(project_id, projects_root)
    snapshot = state.approval.snapshot
    assert snapshot is not None
    fingerprint = approvals.approval_fingerprint(snapshot)
    if deliverable_id:
        deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    else:
        from pipeline.deliverables import load_deliverables
        items = list(load_deliverables(project_id, projects_root=projects_root).items)
        toutiao = next((item for item in items if "toutiao" in item.targets), None)
        if toutiao is None:
            raise DeliveryError("toutiao article deliverable is missing")
        deliverable = toutiao
    platform = _single_platform(deliverable)
    if not mode_allowed(platform, "export"):
        raise DeliveryError(f"{platform} export is not available", code="mode_not_allowed")
    if snapshot.deliverable_versions.get(deliverable.id) != deliverable.version:
        raise DeliveryError("deliverable version is not the approved snapshot", http_status=409, code="not_approved")
    key = make_idempotency_key(
        project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, platform=platform,
        account_id="local", mode="export", approval_fingerprint=fingerprint,
    )
    existing = get_attempt_by_key(conn, key)
    try:
        export = project_exports.create_export(project_id, projects_root=projects_root)
    except project_exports.ProjectExportError as error:
        if "completed approval" in str(error):
            raise DeliveryError(str(error), http_status=409, code="not_approved") from error
        raise DeliveryError(str(error)) from error
    if existing is not None:
        return DeliveryResult(existing, replayed=True, export=export)
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id="local", mode="export", outcome="success",
        idempotency_key=key, request_hash_value=request_hash({"export": export.file_name}),
        actor=actor, raw_receipt=json_dumps({"file_name": export.file_name, "path": export.path}),
    )
    insert_audit(
        conn, actor=actor, action="delivery.export",
        payload={"file_name": export.file_name, "platform": platform},
        project_id=project_id, deliverable_id=deliverable.id,
    )
    return DeliveryResult(attempt, export=export)


def create_draft(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str,
    actor: str,
    adapter: PublisherAdapter,
    account: AccountConfig,
    publish_config: PublishConfig,
    cfg: AppConfig | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    retry_of_id: str | None = None,
) -> DeliveryResult:
    _require_bridge(cfg)
    state = _approval_or_409(project_id, projects_root)
    snapshot = state.approval.snapshot
    assert snapshot is not None
    deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    platform = _single_platform(deliverable)
    if platform != "wechat_mp":
        raise DeliveryError("draft is only implemented for wechat_mp", code="mode_not_allowed")
    if not mode_allowed(platform, "draft", adapter):
        raise DeliveryError("wechat draft is not available", code="mode_not_allowed")
    if snapshot.deliverable_versions.get(deliverable.id) != deliverable.version:
        raise DeliveryError("deliverable version is not the approved snapshot", http_status=409, code="not_approved")
    fingerprint = approvals.approval_fingerprint(snapshot)
    key = make_idempotency_key(
        project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, platform=platform,
        account_id=account.id, mode="draft", approval_fingerprint=fingerprint,
        retry_of_id=retry_of_id,
    )
    existing = get_attempt_by_key(conn, key)
    if existing is not None:
        if existing.outcome == "success":
            return DeliveryResult(existing, replayed=True, publication_id=existing.publication_id, media_id=existing.platform_post_id)
        if retry_of_id is None:
            return DeliveryResult(existing, replayed=True, publication_id=existing.publication_id)
    if retry_of_id:
        prior = get_attempt_by_key  # noqa: F841
        from pipeline.delivery.store import get_attempt
        old = get_attempt(conn, retry_of_id)
        if old is None:
            raise DeliveryError("retry target not found", http_status=404, code="attempt_not_found")
        if old.outcome == "success":
            raise DeliveryError("successful attempts cannot be retried", code="success_not_retryable")

    project = project_store.load_project(project_id, projects_root=projects_root)
    binding = get_binding(conn, deliverable_id=deliverable.id, platform=platform, account_id=account.id)
    digest = project_content_hash(project_id, deliverable.id)
    existing_topic = conn.execute(
        "SELECT * FROM topics WHERE content_hash = ?", (digest,),
    ).fetchone()
    content_id = binding.content_id if binding else None
    if content_id is None and existing_topic is not None:
        existing_content = conn.execute(
            "SELECT * FROM contents WHERE topic_id = ?", (existing_topic["id"],),
        ).fetchone()
        if existing_content is not None:
            content_id = existing_content["id"]
    materialized = materialize_wechat_article(
        project_id, deliverable, content_id=content_id, projects_root=projects_root,
    )
    now = db.now_utc()
    if content_id is None:
        topic_id = new_id("t")
        db.insert_topic(conn, Topic(
            id=topic_id, source=f"project:{project_id}", title=project.title, url=None,
            summary=project.idea, content_hash=digest,
            pillar=_PROJECT_PILLAR, score=None, score_reason=None,
            status=TopicStatus.CONSUMED.value, created_at=now, updated_at=now,
        ))
        db.insert_content(conn, Content(
            id=materialized.content_id, topic_id=topic_id, pillar=_PROJECT_PILLAR,
            title=materialized.title, canonical_path=str(materialized.canonical_path),
            formats=(platform,), gate_score_total=None, gate_scores=None, gate_verdict=None,
            status=ContentStatus.APPROVED.value, created_at=now, updated_at=now,
        ))
        project_store.update_project(
            project, now=now, content_ids=(*project.content_ids, materialized.content_id),
            projects_root=projects_root,
        )
        content_id = materialized.content_id
    else:
        content_id = content_id

    publication = _reuse_or_insert_publication(
        conn, content_id=content_id, platform=platform, account_id=account.id, now=now,
    )
    upsert_binding(conn, LegacyBinding(
        project_id, deliverable.id, content_id, publication.id, platform,
        account.id, str(materialized.materialize_dir), now,
    ))

    result = safe_publish(
        conn, publication, adapter, config=publish_config, account=account,
        dry_run=False, now_iso=now,
    )
    refreshed = db.get_publication(conn, publication.id)
    outcome, error, media_id, url = _draft_outcome(result, refreshed)
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id=account.id, mode="draft", outcome=outcome,
        idempotency_key=key, request_hash_value=request_hash({
            "publication_id": publication.id, "content_id": content_id,
        }),
        actor=actor, publication_id=publication.id, content_id=content_id,
        retry_of_id=retry_of_id, platform_post_id=media_id, platform_url=url,
        raw_receipt=json_receipt(result), error=error,
    )
    insert_audit(
        conn, actor=actor, action="delivery.draft",
        payload={"outcome": outcome, "media_id": media_id, "publication_id": publication.id},
        project_id=project_id, deliverable_id=deliverable.id, publication_id=publication.id,
    )
    return DeliveryResult(attempt, publication_id=publication.id, media_id=media_id)


def _reuse_or_insert_publication(
    conn: sqlite3.Connection, *, content_id: str, platform: str, account_id: str, now: str,
) -> Publication:
    existing = conn.execute(
        "SELECT * FROM publications WHERE content_id = ? AND platform = ? AND account_id = ?",
        (content_id, platform, account_id),
    ).fetchone()
    if existing is not None:
        pub = db._row_to_publication(existing)
        if pub.status == PublicationStatus.FAILED.value:
            db.transition(
                conn, "publications", pub.id,
                PublicationStatus.FAILED.value, PublicationStatus.QUEUED.value,
            )
            updated = db.get_publication(conn, pub.id)
            assert updated is not None
            return updated
        if pub.status == PublicationStatus.QUEUED.value:
            return pub
        return pub
    pub = Publication(
        id=new_id("p"), content_id=content_id, platform=platform, account_id=account_id,
        scheduled_at=now, published_at=None, platform_post_id=None, platform_url=None,
        error=None, retry_count=0, status=PublicationStatus.QUEUED.value,
        created_at=now, updated_at=now,
    )
    db.insert_publication(conn, pub)
    return pub


def _draft_outcome(
    result: SafePublishResult, publication: Publication | None,
) -> tuple[str, str | None, str | None, None]:
    media_id = result.platform_post_id or (publication.platform_post_id if publication else None)
    if result.published and media_id:
        return "success", None, media_id, None
    if result.published and not media_id:
        return "failure", "wechat draft succeeded without media_id", None, None
    reason = result.reason or "draft failed"
    if "unknown" in reason.lower():
        return "unknown", reason, None, None
    return "failure", reason, None, None


def _single_platform(deliverable: Deliverable) -> str:
    if len(deliverable.targets) != 1:
        raise DeliveryError("article deliverable must target exactly one platform")
    return deliverable.targets[0]


def json_receipt(result: SafePublishResult | PublishResult) -> str:
    if isinstance(result, SafePublishResult):
        payload = {
            "published": result.published, "reason": result.reason,
            "platform_post_id": result.platform_post_id, "url": result.url,
            "dry_run": result.dry_run,
        }
    else:
        payload = {"platform_post_id": result.platform_post_id, "url": result.url, "raw": result.raw_response}
    return json_dumps(payload)


def json_dumps(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False)


def attempt_to_dict(result: DeliveryResult) -> dict[str, Any]:
    payload = asdict(result.attempt)
    payload["replayed"] = result.replayed
    if result.export is not None:
        payload["export"] = asdict(result.export)
        payload["export"]["url"] = f"/output/projects/{result.export.project_id}/{result.export.path}"
    payload["media_id"] = result.media_id
    payload["label"] = _user_label(result.attempt)
    return payload


def _user_label(attempt: DeliveryAttempt) -> str:
    if attempt.mode == "preview":
        return "预览通过" if attempt.outcome == "success" else "预览失败"
    if attempt.mode == "export":
        return "已导出本地包" if attempt.outcome == "success" else "导出失败"
    if attempt.mode == "draft" and attempt.outcome == "success":
        return "已创建公众号草稿"
    if attempt.mode == "draft":
        return "草稿失败"
    return attempt.outcome
