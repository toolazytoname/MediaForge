"""U7-9: 从 config.yaml 彻底移除一个账号（用户决策：删除 = 账号也消失，
不是只清凭据文件——见 TASKS.md U7-9）。

用 ruamel.yaml round-trip 编辑，断言：
  1. 目标账号从 platforms.<platform>.accounts[] 里被删掉
  2. 同 platform 下其它账号、其它 platform、config.yaml 里的注释都原样保留
  3. 幂等：platform/account 不存在时返回 False，不报错、不改文件
"""
from __future__ import annotations

from pathlib import Path

from pipeline.webui.config_edit import (
    add_account_to_config,
    remove_account_from_config,
)

_CONFIG_YAML = """\
timezone: Asia/Shanghai
pillars: []
llm:
  tiers:
    cheap: x
    creative: y
    critical: z

# ── Platforms ─────────────────────────────────────────────
platforms:
  toutiao:
    kind: playwright
    windows: ["12:00-14:00"]
    accounts:
      - id: main
        cookies: secrets/cookies/toutiao_main.json
      - id: second
        cookies: secrets/cookies/toutiao_second.json
  xiaohongshu:
    kind: playwright
    windows: ["12:00-14:00"]
    accounts:
      - id: main
        cookies: secrets/cookies/xiaohongshu_main.json
"""


def _write_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(_CONFIG_YAML, encoding="utf-8")
    return p


def test_removes_target_account(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    removed = remove_account_from_config("xiaohongshu", "main", config_path=path)
    assert removed is True

    text = path.read_text(encoding="utf-8")
    assert "xiaohongshu_main.json" not in text
    # 平台本身应该还在（只是空 accounts），不能把整个 xiaohongshu 块删掉
    assert "xiaohongshu:" in text


def test_preserves_sibling_accounts_and_platforms(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    remove_account_from_config("toutiao", "second", config_path=path)

    text = path.read_text(encoding="utf-8")
    assert "id: main" in text
    assert "toutiao_main.json" in text
    assert "toutiao_second.json" not in text
    assert "xiaohongshu_main.json" in text


def test_preserves_comments(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    remove_account_from_config("xiaohongshu", "main", config_path=path)

    text = path.read_text(encoding="utf-8")
    assert "# ── Platforms" in text


def test_missing_platform_is_idempotent_no_op(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    before = path.read_text(encoding="utf-8")
    removed = remove_account_from_config("douyin", "main", config_path=path)
    assert removed is False
    assert path.read_text(encoding="utf-8") == before


def test_missing_account_is_idempotent_no_op(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    before = path.read_text(encoding="utf-8")
    removed = remove_account_from_config("toutiao", "no-such", config_path=path)
    assert removed is False
    assert path.read_text(encoding="utf-8") == before


def test_missing_config_file_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "no-such-config.yaml"
    removed = remove_account_from_config("toutiao", "main", config_path=path)
    assert removed is False


# ── add_account_to_config（一键登录成功后自动登记账号） ─────


_CONFIG_YAML_EMPTY_ACCOUNTS = """\
timezone: Asia/Shanghai
pillars: []
llm:
  tiers:
    cheap: x
    creative: y
    critical: z

# ── Platforms ─────────────────────────────────────────────
platforms:
  toutiao:
    kind: playwright
    windows: ["07:00-09:00"]
    accounts: []
  xiaohongshu:
    kind: playwright
    windows: ["12:00-14:00"]
    accounts:
      - id: other
        cookies: secrets/cookies/xiaohongshu_other.json
"""


def _write_empty_accounts_config(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(_CONFIG_YAML_EMPTY_ACCOUNTS, encoding="utf-8")
    return p


def test_add_account_appends_to_empty_accounts_list(tmp_path: Path) -> None:
    path = _write_empty_accounts_config(tmp_path)
    added = add_account_to_config("toutiao", "main", config_path=path)
    assert added is True

    text = path.read_text(encoding="utf-8")
    assert "id: main" in text
    assert "secrets/cookies/toutiao_main.json" in text


def test_add_account_preserves_sibling_accounts_and_comments(tmp_path: Path) -> None:
    path = _write_empty_accounts_config(tmp_path)
    add_account_to_config("xiaohongshu", "main", config_path=path)

    text = path.read_text(encoding="utf-8")
    assert "id: other" in text  # 兄弟账号原样保留
    assert "xiaohongshu_other.json" in text
    assert "# ── Platforms" in text  # 注释原样保留


def test_add_account_already_present_is_idempotent_no_op(tmp_path: Path) -> None:
    path = _write_empty_accounts_config(tmp_path)
    before = path.read_text(encoding="utf-8")
    added = add_account_to_config("xiaohongshu", "other", config_path=path)
    assert added is False
    assert path.read_text(encoding="utf-8") == before


def test_add_account_missing_platform_returns_false_no_op(tmp_path: Path) -> None:
    """douyin 在 config.yaml 里还没配置块 -> 不知道 windows，不能瞎造，no-op。"""
    path = _write_empty_accounts_config(tmp_path)
    before = path.read_text(encoding="utf-8")
    added = add_account_to_config("douyin", "main", config_path=path)
    assert added is False
    assert path.read_text(encoding="utf-8") == before


def test_add_account_missing_config_file_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "no-such-config.yaml"
    added = add_account_to_config("toutiao", "main", config_path=path)
    assert added is False
