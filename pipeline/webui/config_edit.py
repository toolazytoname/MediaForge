"""U7-9: 从 config.yaml 彻底移除一个账号。

背景（用户决策，见 TASKS.md U7-9）：U7-8 最初只删凭据文件，账号仍留在
config.yaml、UI 上账号行不消失，只是健康状态变红。用户看到实际行为后
明确要求"彻底移除账号"——删除即账号从列表消失，而不是留一个失效的行。

用 `ruamel.yaml` round-trip 模式编辑（而不是 `yaml.safe_load` +
`yaml.dump`），是因为后者会丢掉 config.yaml 里大量的行内注释/分段标题
（如 `# ── Platforms ─────`），对手工维护的配置文件是破坏性的。

只做定点删除：platforms.<platform>.accounts[] 里 id == account 的那一条。
平台本身（即便 accounts 变空）保留，不做进一步清理——账号数量归零后
`collect_cookie_health` 自然展示为"未授权"，符合 M11-C 网格设计。
"""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from pipeline.webui import deps


def remove_account_from_config(
    platform: str,
    account: str,
    *,
    config_path: str | Path | None = None,
) -> bool:
    """从 config.yaml 的 `platforms.<platform>.accounts[]` 删掉 id == account 的条目。

    Args:
        platform: 平台 key（如 "xiaohongshu"）。
        account: 账号 id（如 "main"）。
        config_path: 覆盖 config.yaml 路径；默认 `deps._CONFIG_PATH`
            （与 `deps.get_config()` 读取的是同一份配置，避免读写路径漂移）。

    Returns:
        True 表示确实删掉了一条；False 表示 config 文件不存在、平台未配置，
        或该账号本就不在列表里（三种情况都是幂等 no-op，不算错误）。
    """
    path = Path(config_path if config_path is not None else deps._CONFIG_PATH)
    if not path.exists():
        return False

    yaml = YAML()
    yaml.preserve_quotes = True
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    platforms = (data or {}).get("platforms") or {}
    platform_cfg = platforms.get(platform)
    if not platform_cfg:
        return False

    accounts = platform_cfg.get("accounts")
    if not accounts:
        return False

    idx = next(
        (i for i, acc in enumerate(accounts) if acc.get("id") == account),
        None,
    )
    if idx is None:
        return False

    del accounts[idx]
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return True


# credential 字段名按 platform kind 决定：playwright 存 cookies 文件路径，
# api 存 credentials 文件路径（与 pipeline/config.py 的 AccountPlaywright /
# AccountAPI 判别式一致，TECH_SPEC §6 契约）。
_CREDENTIAL_FIELD_BY_KIND = {"playwright": "cookies", "api": "credentials"}


def add_account_to_config(
    platform: str,
    account: str,
    *,
    config_path: str | Path | None = None,
) -> bool:
    """一键登录成功后，把账号登记进 `platforms.<platform>.accounts[]`。

    背景：一键登录只把 cookie/凭据存进 `secrets/cookies/`，从不touch
    `config.yaml`——但账号中心的账号数/健康度全部读 config.yaml 声明的
    账号列表（`collect_cookie_health`），导致登录明明成功、UI 却一直显示
    0 个账号（用户实测反馈：「明明已经登录成功了，但是还是0」）。

    Args:
        platform: 平台 key（如 "toutiao"）。
        account: 账号 id（如 "main"）。
        config_path: 覆盖 config.yaml 路径；默认 `deps._CONFIG_PATH`。

    Returns:
        True 表示确实新增了一条；False 表示幂等 no-op —— 配置文件不存在、
        platform 未配置（无法凭空猜 windows，不能瞎创建 platform 块），
        或该账号已经在列表里。
    """
    path = Path(config_path if config_path is not None else deps._CONFIG_PATH)
    if not path.exists():
        return False

    yaml = YAML()
    yaml.preserve_quotes = True
    with path.open("r", encoding="utf-8") as f:
        data = yaml.load(f)

    platforms = (data or {}).get("platforms") or {}
    platform_cfg = platforms.get(platform)
    if not platform_cfg:
        return False

    field = _CREDENTIAL_FIELD_BY_KIND.get(platform_cfg.get("kind"))
    if field is None:
        return False

    accounts = platform_cfg.get("accounts")
    if accounts is None:
        return False

    if any(acc.get("id") == account for acc in accounts):
        return False

    accounts.append({
        "id": account,
        field: f"secrets/cookies/{platform}_{account}.json",
    })
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return True


__all__ = ["remove_account_from_config", "add_account_to_config"]
