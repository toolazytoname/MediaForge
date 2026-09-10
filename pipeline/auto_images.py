"""Recover per-slot image requests without repeating possibly charged calls."""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from pathlib import Path

from pipeline import visuals
from pipeline.account_budget import release_reservation, reserve_spend, settle_spend
from pipeline.utils.errors import BudgetExceeded
from pipeline.creators import image_gen
from pipeline.utils.flock import acquire, release
from pipeline.utils.ids import new_id


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def generate_slot(
    project_id: str, slot: visuals.VisualSlot, *, now: str, projects_root: str | Path,
    conn: sqlite3.Connection, account_id: str | None, accounts_root: str | Path | None,
) -> None:
    from pipeline.auto_create import AutoCreateError, _image_cost_usd
    journal = Path(projects_root) / project_id / 'auto_images' / f'{slot.id}.json'
    lock = journal.with_suffix('.lock')
    acquire(lock)
    try:
        plan = visuals.load_visuals(project_id, projects_root=projects_root)
        if any(a.slot_id == slot.id and a.status == 'selected' for a in plan.assets):
            return
        entry = json.loads(journal.read_text()) if journal.exists() else None
        account_root = str(Path(accounts_root).resolve()) if accounts_root is not None else None
        if entry and (entry['account_id'] != account_id or entry['accounts_root'] != account_root):
            raise AutoCreateError('图片请求的账号归属已变化，请先核对原请求', code='image_result_unknown')
        if entry is None:
            provider = image_gen._require_provider()
            model = getattr(provider, '_model', 'image-01')
            entry = dict(asset_id=new_id('vas'), model=model, cost=_image_cost_usd(model),
                         prompt=slot.direction or slot.purpose, state='prepared',
                         account_id=account_id,
                         accounts_root=str(Path(accounts_root).resolve()) if accounts_root is not None else None)
            _write(journal, entry)
        path = visuals.asset_path(project_id, entry['asset_id'], projects_root=projects_root)
        if entry['state'] == 'prepared':
            model = getattr(image_gen._require_provider(), '_model', 'image-01')
            if model != entry['model']:
                raise AutoCreateError('图片模型已变化，请核对待处理请求', code='image_result_unknown')
            if account_id and accounts_root is not None:
                try:
                    reserve_spend(account_id, token=entry['asset_id'], amount=entry['cost'],
                                  now=now, accounts_root=accounts_root)
                except ValueError as error:
                    raise AutoCreateError(str(error), code='budget_exceeded') from error
            entry = {**entry, 'state': 'pending'}
            _write(journal, entry)  # durable before the paid call
            try:
                image_gen.generate_image(entry['prompt'], out_path=path, aspect_ratio=slot.aspect_ratio,
                                         stage='create_image', ref_id=project_id, conn=conn, retry=False)
            except BudgetExceeded:
                _write(journal, {**entry, 'state': 'prepared'})
                if account_id and accounts_root is not None:
                    release_reservation(account_id, token=entry['asset_id'], now=now,
                                        accounts_root=accounts_root)
                raise
        if not path.is_file():
            raise AutoCreateError('图片生成结果未知，文章已保留，请先核对原请求，不能自动重试',
                                  code='image_result_unknown')
        # generate_image writes atomically; a persisted file can be registered after a crash.
        if account_id and accounts_root is not None:
            settle_spend(account_id, token=entry['asset_id'], now=now, accounts_root=accounts_root)
        existing = next((a for a in plan.assets if a.id == entry['asset_id']), None)
        asset = existing or visuals.record_asset(
            project_id, slot_id=slot.id, prompt=entry['prompt'], model=entry['model'],
            size=slot.aspect_ratio, cost_usd=entry['cost'], now=now,
            file_path=f"assets/{entry['asset_id']}.png", status='candidate',
            asset_id=entry['asset_id'], projects_root=projects_root,
        )
        visuals.select_asset(project_id, asset.id, reason='自动选中', rating=3, projects_root=projects_root)
        _write(journal, {**entry, 'state': 'complete'})
    finally:
        release(lock)
