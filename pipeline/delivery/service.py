"""Project delivery orchestration: preview / export / wechat draft (RFC §5.4–5.5)."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pipeline import approvals, db, project_exports, projects as project_store
from pipeline.config import AppConfig, DeliveryConfig, PublishConfig
from pipeline.deliverables import KIND_ARTICLE, KIND_GALLERY, Deliverable, get_deliverable, load_deliverables
from pipeline.delivery.materialize import materialize_wechat_article, project_content_hash
from pipeline.delivery.store import (
    DeliveryAttempt,
    LegacyBinding,
    get_attempt,
    get_attempt_by_key,
    get_binding,
    insert_attempt,
    insert_audit,
    latest_attempts,
    make_idempotency_key,
    request_hash,
    upsert_binding,
)
from pipeline.models import Content, ContentStatus, Publication, PublicationStatus, Topic, TopicStatus
from pipeline.account_authorization import AuthorizationError, assert_account_may_deliver
from pipeline.account_profiles import DEFAULT_ACCOUNTS_ROOT, AccountProfileError, load_profile
from pipeline.autonomy import AutonomyError, load_policy, require_delivery_mode
from pipeline.utils.sidecar_ids import valid_sidecar_id
from pipeline.oauth.store import upsert_oauth_metadata
from pipeline.publishers.base import AccountConfig, PostBundle, PublishError, PublishResult, PublisherAdapter
from pipeline.publishers.capability_registry import (
    get_capability,
    mode_allowed,
    official_publish_platforms,
)
from pipeline.publishers.safe_publish import SafePublishResult, safe_publish
from pipeline.utils.flock import LockHeld, acquire, release
from pipeline.utils.ids import new_id
from pipeline.utils.redact import token_last4

OFFICIAL_PLATFORMS = official_publish_platforms()

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
    if adapter is not None and account is not None and deliverable.kind == KIND_ARTICLE:
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
    try:
        require_delivery_mode(project_id, "export", projects_root=projects_root)
    except AutonomyError as error:
        raise DeliveryError(str(error), http_status=error.http_status, code=error.code) from error
    snapshot = state.approval.snapshot
    assert snapshot is not None
    fingerprint = approvals.approval_fingerprint(snapshot)
    if deliverable_id:
        deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    else:
        items = list(load_deliverables(project_id, projects_root=projects_root).items)
        toutiao = next((item for item in items if item.kind == KIND_ARTICLE and "toutiao" in item.targets), None)
        galleries = [item for item in items if item.kind == KIND_GALLERY]
        if toutiao is not None:
            deliverable = toutiao
        elif len(galleries) == 1:
            deliverable = galleries[0]
        elif galleries:
            raise DeliveryError("deliverable_id is required when multiple galleries exist")
        else:
            raise DeliveryError("toutiao article deliverable is missing")
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
        if deliverable.kind == KIND_GALLERY:
            export = project_exports.create_gallery_export(
                project_id, deliverable.id, projects_root=projects_root,
            )
        else:
            export = project_exports.create_export(project_id, projects_root=projects_root)
    except project_exports.ProjectExportError as error:
        if "completed approval" in str(error) or "not the approved snapshot" in str(error):
            raise DeliveryError(str(error), http_status=409, code="not_approved") from error
        raise DeliveryError(str(error)) from error
    if existing is not None:
        return DeliveryResult(existing, replayed=True, export=export)
    receipt = {
        "file_name": export.file_name,
        "path": export.path,
        "kind": deliverable.kind,
        "platform_post_id": None,
        "platform_url": None,
        "notice": "local export only; no platform receipt",
    }
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id="local", mode="export", outcome="success",
        idempotency_key=key, request_hash_value=request_hash({"export": export.file_name}),
        actor=actor, raw_receipt=json_dumps(receipt),
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
    path: str = "human",
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
) -> DeliveryResult:
    _require_bridge(cfg)
    deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    platform = _single_platform(deliverable)
    if deliverable.kind != KIND_ARTICLE or platform not in {"wechat_mp", "toutiao"}:
        raise DeliveryError("draft is only implemented for wechat_mp/toutiao articles", code="mode_not_allowed")
    if not mode_allowed(platform, "draft", adapter):
        raise DeliveryError(f"{platform} draft is not available", code="mode_not_allowed")
    if path == "human":
        state = _approval_or_409(project_id, projects_root)
        try:
            require_delivery_mode(project_id, "draft", projects_root=projects_root)
        except AutonomyError as error:
            raise DeliveryError(str(error), http_status=error.http_status, code=error.code) from error
        snapshot = state.approval.snapshot
        assert snapshot is not None
        if snapshot.deliverable_versions.get(deliverable.id) != deliverable.version:
            raise DeliveryError("deliverable version is not the approved snapshot", http_status=409, code="not_approved")
        fingerprint = approvals.approval_fingerprint(snapshot)
    else:
        fingerprint = f"auto:{deliverable.id}:{deliverable.version}"
    try:
        assert_account_may_deliver(
            project_id, platform=platform, account_id=account.id, mode="draft",
            path=path, projects_root=projects_root, accounts_root=accounts_root,
            content_fingerprint=_content_fp(project_id, projects_root),
        )
    except AuthorizationError as error:
        raise DeliveryError(str(error), http_status=409, code=error.code) from error
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

    wrapped = _DraftMode(adapter) if platform == "toutiao" else _RequireMediaId(adapter)
    result = safe_publish(
        conn, publication, wrapped, config=publish_config, account=account,
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


def create_direct(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str,
    actor: str,
    adapter: PublisherAdapter,
    account: AccountConfig,
    publish_config: PublishConfig,
    confirm_token: str,
    cfg: AppConfig | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    accounts_root: str | Path = DEFAULT_ACCOUNTS_ROOT,
    retry_of_id: str | None = None,
    path: str = "human",
) -> DeliveryResult:
    """Gated WeChat (or other official) public publish. Missing permission never calls adapter."""
    _require_bridge(cfg)
    if not confirm_token or not str(confirm_token).strip():
        raise DeliveryError(
            "direct publish requires an explicit confirm token",
            http_status=403,
            code="confirm_required",
        )
    deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    platform = _single_platform(deliverable)
    if deliverable.kind != KIND_ARTICLE:
        raise DeliveryError("direct is only implemented for articles", code="mode_not_allowed")
    if path == "human":
        state = _approval_or_409(project_id, projects_root)
        try:
            require_delivery_mode(project_id, "direct", projects_root=projects_root)
        except AutonomyError as error:
            raise DeliveryError(str(error), http_status=error.http_status, code=error.code) from error
        snapshot = state.approval.snapshot
        assert snapshot is not None
        fingerprint = approvals.approval_fingerprint(snapshot)
        if snapshot.deliverable_versions.get(deliverable.id) != deliverable.version:
            raise DeliveryError("deliverable version is not the approved snapshot", http_status=409, code="not_approved")
    else:
        fingerprint = f"auto:{deliverable.id}:{deliverable.version}"
    if retry_of_id:
        prior = get_attempt(conn, retry_of_id)
        if prior is None:
            raise DeliveryError("retry target not found", http_status=404, code="attempt_not_found")
        if prior.outcome == "success":
            raise DeliveryError("successful attempts cannot be retried", code="success_not_retryable")
        if prior.outcome == "unknown":
            return DeliveryResult(
                prior, replayed=True,
                publication_id=prior.publication_id, media_id=prior.platform_post_id,
            )
    unknown_open = _open_unknown_direct(
        conn, project_id=project_id, deliverable_id=deliverable.id, account_id=account.id,
    )
    if unknown_open is not None:
        return DeliveryResult(
            unknown_open, replayed=True,
            publication_id=unknown_open.publication_id, media_id=unknown_open.platform_post_id,
        )
    key = make_idempotency_key(
        project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, platform=platform,
        account_id=account.id, mode="direct", approval_fingerprint=fingerprint,
        retry_of_id=retry_of_id,
    )
    existing = get_attempt_by_key(conn, key)
    if existing is not None:
        return DeliveryResult(
            existing, replayed=True,
            publication_id=existing.publication_id, media_id=existing.platform_post_id,
        )

    blocked = _direct_permission_error(
        project_id, platform=platform, account=account, adapter=adapter,
        publish_config=publish_config, path=path, projects_root=projects_root,
        accounts_root=accounts_root,
    )
    if blocked is not None:
        attempt = insert_attempt(
            conn, project_id=project_id, deliverable_id=deliverable.id,
            deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
            platform=platform, account_id=account.id, mode="direct", outcome="failure",
            idempotency_key=key, request_hash_value=request_hash({
                "platform": platform, "deliverable_id": deliverable.id, "blocked": blocked,
            }),
            actor=actor, retry_of_id=retry_of_id, error=blocked,
            confirm_token_hash=_hash_confirm(confirm_token),
            raw_receipt=json_dumps({"blocked": True, "error": blocked, "platform": platform}),
        )
        insert_audit(
            conn, actor=actor, action="delivery.direct",
            payload={"outcome": "failure", "blocked": True, "error": blocked},
            project_id=project_id, deliverable_id=deliverable.id,
        )
        return DeliveryResult(attempt)

    lock_path = Path(projects_root) / project_id / "locks" / (
        f"direct-{deliverable.id}-{account.id}.lock"
    )
    try:
        acquire(lock_path)
    except LockHeld as error:
        raise DeliveryError(
            "another direct publish is already in flight",
            code="direct_locked", http_status=409,
        ) from error
    try:
        existing = get_attempt_by_key(conn, key)
        if existing is not None:
            return DeliveryResult(
                existing, replayed=True,
                publication_id=existing.publication_id, media_id=existing.platform_post_id,
            )
        unknown_open = _open_unknown_direct(
            conn, project_id=project_id, deliverable_id=deliverable.id, account_id=account.id,
        )
        if unknown_open is not None:
            return DeliveryResult(
                unknown_open, replayed=True,
                publication_id=unknown_open.publication_id, media_id=unknown_open.platform_post_id,
            )
        return _create_direct_locked(
            conn, project_id=project_id, deliverable=deliverable, platform=platform,
            actor=actor, adapter=adapter, account=account, publish_config=publish_config,
            confirm_token=confirm_token, projects_root=projects_root, fingerprint=fingerprint,
            key=key, retry_of_id=retry_of_id,
        )
    finally:
        release(lock_path)


def _create_direct_locked(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable: Deliverable,
    platform: str,
    actor: str,
    adapter: PublisherAdapter,
    account: AccountConfig,
    publish_config: PublishConfig,
    confirm_token: str,
    projects_root: str | Path,
    fingerprint: str,
    key: str,
    retry_of_id: str | None,
) -> DeliveryResult:
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

    publication = _reuse_or_insert_publication(
        conn, content_id=content_id, platform=platform, account_id=account.id, now=now,
        requeue_failed=False,
    )
    upsert_binding(conn, LegacyBinding(
        project_id, deliverable.id, content_id, publication.id, platform,
        account.id, str(materialized.materialize_dir), now,
    ))
    if _latest_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        account_id=account.id, mode="direct", outcome="success",
    ) is not None:
        raise DeliveryError("already publicly published", code="already_published")
    draft_ok = _latest_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        account_id=account.id, mode="draft", outcome="success",
    )
    if publication.status == PublicationStatus.PUBLISHED.value:
        if draft_ok is None or not draft_ok.platform_post_id:
            raise DeliveryError(
                "publication is not queued for direct; send a draft first or use a queued item",
                code="not_queued",
            )
        if draft_ok.deliverable_version != deliverable.version:
            raise DeliveryError(
                "remote draft media_id is not the approved deliverable version",
                code="draft_stale",
            )
        if draft_ok.approval_fingerprint != fingerprint:
            raise DeliveryError(
                "remote draft was not created from the current approval",
                code="draft_stale",
            )
        directed = PostBundle(
            content_id=content_id,
            title=materialized.title,
            body_path=materialized.canonical_path,
            media_paths=(),
            tags=(),
            extra={"delivery_mode": "direct", "draft_media_id": draft_ok.platform_post_id},
        )
        try:
            published = adapter.publish(directed, account, dry_run=False)
            result = SafePublishResult(
                published=bool(published.platform_post_id),
                platform_post_id=published.platform_post_id,
                url=published.url,
                reason="" if published.platform_post_id else "unknown receipt",
            )
            raw = published.raw_response
        except PublishError as error:
            result = SafePublishResult(published=False, reason=f"publish error: {error}")
            raw = json_dumps({"error": str(error), "draft_media_id": draft_ok.platform_post_id})
        outcome, error, post_id, url = _direct_outcome(result, publication)
        attempt = insert_attempt(
            conn, project_id=project_id, deliverable_id=deliverable.id,
            deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
            platform=platform, account_id=account.id, mode="direct", outcome=outcome,
            idempotency_key=key, request_hash_value=request_hash({
                "publication_id": publication.id, "content_id": content_id,
                "via": "draft_media_id",
            }),
            actor=actor, publication_id=publication.id, content_id=content_id,
            retry_of_id=retry_of_id, platform_post_id=post_id, platform_url=url,
            raw_receipt=raw if isinstance(raw, str) else json_receipt(result), error=error,
            confirm_token_hash=_hash_confirm(confirm_token),
        )
        insert_audit(
            conn, actor=actor, action="delivery.direct",
            payload={"outcome": outcome, "platform_post_id": post_id, "publication_id": publication.id},
            project_id=project_id, deliverable_id=deliverable.id, publication_id=publication.id,
        )
        return DeliveryResult(attempt, publication_id=publication.id, media_id=post_id)
    result = safe_publish(
        conn, publication, _DirectMode(adapter), config=publish_config, account=account,
        dry_run=False, now_iso=now,
    )
    refreshed = db.get_publication(conn, publication.id)
    outcome, error, post_id, url = _direct_outcome(result, refreshed)
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id=account.id, mode="direct", outcome=outcome,
        idempotency_key=key, request_hash_value=request_hash({
            "publication_id": publication.id, "content_id": content_id,
        }),
        actor=actor, publication_id=publication.id, content_id=content_id,
        retry_of_id=retry_of_id, platform_post_id=post_id, platform_url=url,
        raw_receipt=json_receipt(result), error=error,
        confirm_token_hash=_hash_confirm(confirm_token),
    )
    insert_audit(
        conn, actor=actor, action="delivery.direct",
        payload={"outcome": outcome, "platform_post_id": post_id, "publication_id": publication.id},
        project_id=project_id, deliverable_id=deliverable.id, publication_id=publication.id,
    )
    return DeliveryResult(attempt, publication_id=publication.id, media_id=post_id)


def _open_unknown_direct(
    conn: sqlite3.Connection, *, project_id: str, deliverable_id: str, account_id: str,
) -> DeliveryAttempt | None:
    return _latest_attempt(
        conn, project_id=project_id, deliverable_id=deliverable_id,
        account_id=account_id, mode="direct", outcome="unknown",
    )


def _latest_attempt(
    conn: sqlite3.Connection, *, project_id: str, deliverable_id: str, account_id: str,
    mode: str, outcome: str | None = None,
) -> DeliveryAttempt | None:
    for item in latest_attempts(conn, project_id):
        if item.deliverable_id != deliverable_id or item.account_id != account_id:
            continue
        if item.mode != mode:
            continue
        if outcome is not None and item.outcome != outcome:
            continue
        return item
    return None


def _content_fp(project_id: str, projects_root: str | Path) -> str:
    from pipeline.account_authorization import quality_fingerprint
    return quality_fingerprint(project_id, projects_root=projects_root)


def _publish_id_from_text(text: str) -> str | None:
    import re
    match = re.search(r"publish_id=([A-Za-z0-9_-]+)", text)
    return match.group(1) if match else None


def verify_direct_receipt(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    adapter: PublisherAdapter,
    actor: str,
    now: str | None = None,
) -> DeliveryResult:
    """Query platform for an unknown receipt. Never calls publish()."""
    prior = get_attempt(conn, attempt_id)
    if prior is None:
        raise DeliveryError("verify target not found", http_status=404, code="attempt_not_found")
    if prior.outcome != "unknown":
        return DeliveryResult(prior, replayed=True, publication_id=prior.publication_id, media_id=prior.platform_post_id)
    publish_id = _publish_id_from_text(prior.error or "") or _publish_id_from_text(prior.raw_receipt or "")
    if not publish_id:
        raise DeliveryError("unknown attempt has no publish_id to query", code="publish_id_missing")
    query = getattr(adapter, "query_freepublish", None)
    if not callable(query):
        raise DeliveryError(f"{prior.platform} cannot query publish receipts", code="verify_unsupported")
    stamp = now or db.now_utc()
    terminal_key = make_idempotency_key(
        project_id=prior.project_id, deliverable_id=prior.deliverable_id,
        deliverable_version=prior.deliverable_version, platform=prior.platform,
        account_id=prior.account_id, mode="direct",
        approval_fingerprint=f"verify:{prior.id}:terminal",
    )
    existing = get_attempt_by_key(conn, terminal_key)
    if existing is not None and existing.outcome in {"success", "failure"}:
        return DeliveryResult(existing, replayed=True, publication_id=existing.publication_id, media_id=existing.platform_post_id)
    status = query(publish_id)
    publish_status = status.get("publish_status") if isinstance(status, dict) else None
    article_id = status.get("article_id") if isinstance(status, dict) else None
    url = None
    if isinstance(status, dict):
        raw_url = status.get("article_url") or status.get("url")
        url = raw_url if isinstance(raw_url, str) else None
    if publish_status == 1:
        outcome, error, post_id = "unknown", f"unknown receipt: still publishing publish_id={publish_id}", publish_id
        key = make_idempotency_key(
            project_id=prior.project_id, deliverable_id=prior.deliverable_id,
            deliverable_version=prior.deliverable_version, platform=prior.platform,
            account_id=prior.account_id, mode="direct",
            approval_fingerprint=f"verify:{prior.id}:{stamp}:{uuid4().hex}",
        )
    elif publish_status in (0, 4) and isinstance(article_id, str) and article_id:
        outcome, error, post_id = "success", None, article_id
        key = terminal_key
    else:
        outcome, error, post_id = "failure", f"freepublish status={publish_status!r}", None
        key = terminal_key
    attempt = insert_attempt(
        conn, project_id=prior.project_id, deliverable_id=prior.deliverable_id,
        deliverable_version=prior.deliverable_version,
        approval_fingerprint=f"verify:{prior.id}", platform=prior.platform,
        account_id=prior.account_id, mode="direct", outcome=outcome,
        idempotency_key=key, request_hash_value=request_hash({
            "verify": prior.id, "publish_id": publish_id, "at": stamp,
        }),
        actor=actor, publication_id=prior.publication_id, content_id=prior.content_id,
        retry_of_id=prior.id, platform_post_id=post_id, platform_url=url,
        raw_receipt=json_dumps(status if isinstance(status, dict) else {"status": status}),
        error=error, created_at=stamp,
    )
    insert_audit(
        conn, actor=actor, action="delivery.verify",
        payload={"outcome": outcome, "publish_id": publish_id, "verify_of": prior.id},
        project_id=prior.project_id, deliverable_id=prior.deliverable_id,
    )
    return DeliveryResult(attempt, publication_id=attempt.publication_id, media_id=post_id)


def _direct_permission_error(
    project_id: str,
    *,
    platform: str,
    account: AccountConfig,
    adapter: PublisherAdapter,
    publish_config: PublishConfig,
    path: str,
    projects_root: str | Path,
    accounts_root: str | Path,
) -> str | None:
    if not publish_config.enabled:
        return "publish is disabled"
    if publish_config.allowed_platforms and platform not in publish_config.allowed_platforms:
        return f"platform {platform!r} not in allowed_platforms"
    try:
        assert_account_may_deliver(
            project_id, platform=platform, account_id=account.id, mode="direct",
            path=path, projects_root=projects_root, accounts_root=accounts_root,
            content_fingerprint=_content_fp(project_id, projects_root),
        )
    except AuthorizationError as error:
        return str(error)
    if not valid_sidecar_id(account.id, "acc_"):
        return "delivery_target must be direct on a bound account profile"
    try:
        profile = load_profile(account.id, accounts_root=accounts_root)
    except AccountProfileError as error:
        return str(error)
    if profile.delivery_target != "direct":
        return "delivery_target must be direct"
    if not mode_allowed(platform, "direct", adapter):
        return f"{platform} direct is not available"
    return None


def _direct_outcome(
    result: SafePublishResult, publication: Publication | None,
) -> tuple[str, str | None, str | None, str | None]:
    post_id = result.platform_post_id or (publication.platform_post_id if publication else None)
    url = result.url or (publication.platform_url if publication else None)
    reason = result.reason or ""
    if "unknown" in reason.lower():
        return "unknown", reason, _publish_id_from_text(reason), None
    if result.published and post_id:
        return "success", None, post_id, url
    if result.published and not post_id:
        return "failure", "direct succeeded without platform_post_id", None, None
    return "failure", reason or "direct failed", None, None


class _DirectMode:
    """Force adapter.publish extra.delivery_mode=direct without loosening receipts."""

    def __init__(self, inner: PublisherAdapter):
        self._inner = inner
        self.platform = inner.platform

    def capabilities(self):
        return self._inner.capabilities()

    def validate(self, bundle):
        return self._inner.validate(bundle)

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        extra = dict(bundle.extra or {})
        extra["delivery_mode"] = "direct"
        directed = PostBundle(
            content_id=bundle.content_id,
            title=bundle.title,
            body_path=bundle.body_path,
            media_paths=bundle.media_paths,
            tags=bundle.tags,
            extra=extra,
        )
        return self._inner.publish(directed, account, dry_run)


def create_official_delivery(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    deliverable_id: str,
    actor: str,
    adapter: PublisherAdapter,
    account: AccountConfig,
    confirm_token: str,
    cfg: AppConfig | None = None,
    projects_root: str | Path = project_store.DEFAULT_PROJECTS_ROOT,
    bundle: Any | None = None,
    visibility: str | None = None,
    retry_of_id: str | None = None,
) -> DeliveryResult:
    """Official platform publish. Fail-closed without user-context or receipt."""
    _require_bridge(cfg)
    if not confirm_token or not str(confirm_token).strip():
        raise DeliveryError(
            "official publish requires an explicit confirm token",
            http_status=403,
            code="confirm_required",
        )
    state = _approval_or_409(project_id, projects_root)
    _project, policy = load_policy(project_id, projects_root=projects_root)
    if policy.key == "pack":
        raise DeliveryError(
            "自动内容包不得官方直发",
            http_status=403,
            code="autonomy_forbids_delivery",
        )
    snapshot = state.approval.snapshot
    assert snapshot is not None
    deliverable = get_deliverable(project_id, deliverable_id, projects_root=projects_root)
    platform = _single_platform(deliverable)
    if platform not in OFFICIAL_PLATFORMS:
        raise DeliveryError(f"{platform} is not an official adapter", code="mode_not_allowed")
    if not mode_allowed(platform, "direct", adapter):
        raise DeliveryError(
            f"{platform} direct is unavailable without user-context OAuth",
            code="mode_not_allowed",
        )
    if snapshot.deliverable_versions.get(deliverable.id) != deliverable.version:
        raise DeliveryError("deliverable version is not the approved snapshot", http_status=409, code="not_approved")
    if bundle is None:
        raise DeliveryError("official publish requires a media bundle", code="bundle_missing")
    fingerprint = approvals.approval_fingerprint(snapshot)
    key = make_idempotency_key(
        project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, platform=platform,
        account_id=account.id, mode="direct", approval_fingerprint=fingerprint,
        retry_of_id=retry_of_id,
    )
    existing = get_attempt_by_key(conn, key)
    if existing is not None:
        return DeliveryResult(
            existing, replayed=True,
            publication_id=existing.publication_id, media_id=existing.platform_post_id,
        )
    _record_oauth_metadata(conn, adapter, account, platform)
    extra = dict(getattr(bundle, "extra", None) or {})
    if visibility:
        extra["visibility"] = visibility
        bundle = type(bundle)(
            **{**bundle.__dict__, "extra": extra},
        )
    issues = adapter.validate(bundle)
    if issues:
        err = "; ".join(issues)
        attempt = insert_attempt(
            conn, project_id=project_id, deliverable_id=deliverable.id,
            deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
            platform=platform, account_id=account.id, mode="direct", outcome="failure",
            idempotency_key=key, request_hash_value=request_hash({
                "platform": platform, "deliverable_id": deliverable.id,
            }),
            actor=actor, retry_of_id=retry_of_id, error=err,
            confirm_token_hash=_hash_confirm(confirm_token),
            raw_receipt=json_dumps({"error": err, "platform": platform}),
        )
        insert_audit(
            conn, actor=actor, action="delivery.official",
            payload={"outcome": "failure", "platform": platform, "error": err},
            project_id=project_id, deliverable_id=deliverable.id,
        )
        return DeliveryResult(attempt)
    try:
        result = adapter.publish(bundle, account, dry_run=False)
    except PublishError as error:
        outcome, post_id, url, err = _official_failure(str(error))
        attempt = insert_attempt(
            conn, project_id=project_id, deliverable_id=deliverable.id,
            deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
            platform=platform, account_id=account.id, mode="direct", outcome=outcome,
            idempotency_key=key, request_hash_value=request_hash({
                "platform": platform, "deliverable_id": deliverable.id,
            }),
            actor=actor, retry_of_id=retry_of_id, error=err,
            confirm_token_hash=_hash_confirm(confirm_token),
            raw_receipt=json_dumps({"error": err, "platform": platform}),
        )
        insert_audit(
            conn, actor=actor, action="delivery.official",
            payload={"outcome": outcome, "platform": platform, "error": err},
            project_id=project_id, deliverable_id=deliverable.id,
        )
        return DeliveryResult(attempt)
    outcome, post_id, url, err = _official_success(platform, result)
    attempt = insert_attempt(
        conn, project_id=project_id, deliverable_id=deliverable.id,
        deliverable_version=deliverable.version, approval_fingerprint=fingerprint,
        platform=platform, account_id=account.id, mode="direct", outcome=outcome,
        idempotency_key=key, request_hash_value=request_hash({
            "platform": platform, "deliverable_id": deliverable.id,
        }),
        actor=actor, retry_of_id=retry_of_id, platform_post_id=post_id,
        platform_url=url, raw_receipt=json_receipt(result), error=err,
        confirm_token_hash=_hash_confirm(confirm_token),
    )
    insert_audit(
        conn, actor=actor, action="delivery.official",
        payload={"outcome": outcome, "platform": platform, "platform_post_id": post_id},
        project_id=project_id, deliverable_id=deliverable.id,
    )
    return DeliveryResult(attempt, media_id=post_id)


def compensate_delivery(
    conn: sqlite3.Connection,
    *,
    attempt_id: str,
    actor: str,
    adapter: PublisherAdapter,
    cfg: AppConfig | None = None,
) -> DeliveryResult:
    """Minimal delete/compensation for a prior official success. Fixture-friendly."""
    _require_bridge(cfg)
    from pipeline.delivery.store import get_attempt
    prior = get_attempt(conn, attempt_id)
    if prior is None:
        raise DeliveryError("compensation target not found", http_status=404, code="attempt_not_found")
    if prior.outcome != "success" or not prior.platform_post_id:
        raise DeliveryError("only successful attempts with a platform id can be compensated", code="not_compensatable")
    compensate = getattr(adapter, "compensate", None)
    if not callable(compensate):
        raise DeliveryError(f"{prior.platform} has no compensate contract", code="compensate_unsupported")
    key = make_idempotency_key(
        project_id=prior.project_id, deliverable_id=prior.deliverable_id,
        deliverable_version=prior.deliverable_version, platform=prior.platform,
        account_id=prior.account_id, mode=prior.mode,
        approval_fingerprint=f"compensate:{prior.id}",
    )
    existing = get_attempt_by_key(conn, key)
    if existing is not None:
        return DeliveryResult(existing, replayed=True)
    try:
        compensate(prior.platform_post_id)
        outcome, error = "success", None
    except PublishError as exc:
        outcome, error = "failure", str(exc)
    attempt = insert_attempt(
        conn, project_id=prior.project_id, deliverable_id=prior.deliverable_id,
        deliverable_version=prior.deliverable_version,
        approval_fingerprint=prior.approval_fingerprint, platform=prior.platform,
        account_id=prior.account_id, mode=prior.mode, outcome=outcome,
        idempotency_key=key, request_hash_value=request_hash({
            "compensate": prior.id, "platform_post_id": prior.platform_post_id,
        }),
        actor=actor, compensation_of_id=prior.id, error=error,
        platform_post_id=prior.platform_post_id,
        raw_receipt=json_dumps({"compensated": prior.platform_post_id, "error": error}),
    )
    insert_audit(
        conn, actor=actor, action="delivery.compensate",
        payload={"outcome": outcome, "compensation_of_id": prior.id},
        project_id=prior.project_id, deliverable_id=prior.deliverable_id,
    )
    return DeliveryResult(attempt)


def _official_success(platform: str, result: PublishResult) -> tuple[str, str | None, str | None, str | None]:
    required = get_capability(platform).receipts.success_requires
    post_id = result.platform_post_id
    url = result.url
    if "platform_post_id" in required and not post_id:
        return "failure", None, url, f"{platform} returned no platform_post_id; unknown is failure"
    if "url" in required and not url:
        return "failure", post_id, None, f"{platform} returned no url; unknown is failure"
    return "success", post_id, url, None


def _official_failure(message: str) -> tuple[str, None, None, str]:
    # Unknown receipts are product failures; do not persist outcome=unknown as success.
    return "failure", None, None, message


def _hash_confirm(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _record_oauth_metadata(
    conn: sqlite3.Connection, adapter: PublisherAdapter, account: AccountConfig, platform: str,
) -> None:
    creds = getattr(adapter, "_creds", None)
    if creds is None:
        return
    token = getattr(creds, "access_token", None)
    user_id = getattr(creds, "open_id", None) or getattr(creds, "user_id", None)
    scopes = getattr(creds, "scopes", ())
    has_ctx = bool(getattr(creds, "has_user_context", False))
    upsert_oauth_metadata(
        conn,
        platform=platform,
        account_id=account.id,
        auth_kind="oauth_user",
        key_ref=str(account.credentials_path),
        last4=token_last4(token if isinstance(token, str) else None),
        scopes=scopes,
        user_id=str(user_id) if user_id else None,
        has_user_context=has_ctx,
    )


def _reuse_or_insert_publication(
    conn: sqlite3.Connection, *, content_id: str, platform: str, account_id: str, now: str,
    requeue_failed: bool = True,
) -> Publication:
    existing = conn.execute(
        "SELECT * FROM publications WHERE content_id = ? AND platform = ? AND account_id = ?",
        (content_id, platform, account_id),
    ).fetchone()
    if existing is not None:
        pub = db._row_to_publication(existing)
        if pub.status == PublicationStatus.FAILED.value and requeue_failed:
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


class _RequireMediaId:
    """Adapter wrapper: a draft without platform_post_id must fail, not publish."""

    def __init__(self, inner: PublisherAdapter):
        self._inner = inner
        self.platform = inner.platform

    def capabilities(self):
        return self._inner.capabilities()

    def validate(self, bundle):
        return self._inner.validate(bundle)

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        result = self._inner.publish(bundle, account, dry_run)
        if not dry_run and not result.platform_post_id:
            raise PublishError(f"{self.platform} draft succeeded without media_id")
        return result


class _DraftMode(_RequireMediaId):
    """Force extra.delivery_mode=draft for Playwright adapters that share publish()."""

    def publish(self, bundle, account, dry_run=False) -> PublishResult:
        extra = dict(bundle.extra or {})
        extra["delivery_mode"] = "draft"
        drafted = PostBundle(
            content_id=bundle.content_id,
            title=bundle.title,
            body_path=bundle.body_path,
            media_paths=bundle.media_paths,
            tags=bundle.tags,
            extra=extra,
        )
        return super().publish(drafted, account, dry_run)


def _single_platform(deliverable: Deliverable) -> str:
    if len(deliverable.targets) != 1:
        raise DeliveryError("deliverable must target exactly one platform")
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
    if attempt.mode == "direct" and attempt.outcome == "success":
        return "已公开发布"
    if attempt.mode == "direct" and attempt.outcome == "unknown":
        return "发布结果未知，请核对，不要自动重试"
    if attempt.mode == "direct":
        return "公开发布失败"
    if attempt.platform == "douyin" and attempt.outcome == "success":
        return "已提交抖音官方发布"
    if attempt.platform == "youtube" and attempt.outcome == "success":
        return "已上传 YouTube"
    if attempt.platform == "tiktok" and attempt.outcome == "success":
        return "已提交 TikTok 官方发布"
    if attempt.platform == "instagram" and attempt.outcome == "success":
        return "已提交 Instagram Professional 发布"
    if attempt.platform == "x" and attempt.outcome == "success":
        return "已发到 X"
    if attempt.compensation_of_id and attempt.outcome == "success":
        return "已补偿删除平台内容"
    return attempt.outcome
