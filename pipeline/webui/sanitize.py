"""M3-3 config 脱敏（拆分自 app.py）。

settings 页只读展示 config 时，敏感字段值替换为 '***'。
白名单字段保持可读（月度预算、模型映射等不算敏感）。
"""
from __future__ import annotations


# 敏感字段：值替换为 '***'
_SENSITIVE_PATHS = [
    ("notify", "webhook_url"),
    # 平台凭据路径（账号级）
    ("platforms", "x", "accounts", "*", "credentials"),
    ("platforms", "toutiao", "accounts", "*", "cookies"),
    ("platforms", "xiaohongshu", "accounts", "*", "cookies"),
]


def sanitize_config(cfg_dict: dict) -> dict:
    """config dict 脱敏。in-place 修改后返回（避免深拷贝性能损失）。"""
    if not cfg_dict:
        return cfg_dict
    for path in _SENSITIVE_PATHS:
        _mask_path(cfg_dict, path)
    return cfg_dict


def _mask_path(obj, path):
    """沿路径找到目标字段并替换值。通配符 '*' 处理列表中所有元素。"""
    cur = obj
    for i, key in enumerate(path):
        if cur is None:
            return
        if key == "*":
            # 处理列表：每个元素递归
            if isinstance(cur, list):
                for item in cur:
                    if i == len(path) - 1:
                        # 末级是 '*'，不当做字段名——跳过
                        return
                    _mask_path(item, path[i + 1:])
            return
        if isinstance(cur, dict) and key in cur:
            if i == len(path) - 1:
                cur[key] = "***"
                return
            cur = cur[key]
        else:
            return