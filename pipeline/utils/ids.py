"""ID 生成（TECH_SPEC §4 / §10）。

全系统唯一入口，禁止散落 uuid / secrets 调用。所有 ID 形如 '<prefix>_<8hex>'，
8hex = 32 位熵，单项目体量下碰撞概率可忽略。
"""
from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """生成 '<prefix>_<8hex>' 形式的 ID。

    prefix 约定：
      - 't_' topic（M1-2 起）
      - 'c_' content（M2-1 起）
      - 'p_' publication（M3-1 起）
    """
    return f"{prefix}_{secrets.token_hex(4)}"
