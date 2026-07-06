"""tests/test_sanitize.py — 覆盖 webui/sanitize.py（config 脱敏，88%→100%）。"""
from __future__ import annotations

from pipeline.webui.sanitize import sanitize_config


class TestSanitizeEmpty:
    def test_empty_dict(self):
        # 空 dict fast path（line 22）
        assert sanitize_config({}) == {}

    def test_none_input(self):
        # None 输入也走 fast path
        assert sanitize_config(None) is None


class TestSanitizeBasic:
    def test_top_level_webhook(self):
        cfg = {"notify": {"webhook_url": "https://hooks.example.com/secret"}}
        out = sanitize_config(cfg)
        assert out["notify"]["webhook_url"] == "***"

    def test_preserves_non_sensitive(self):
        cfg = {"notify": {"webhook_url": "https://secret"}, "publish": {"enabled": False}}
        out = sanitize_config(cfg)
        assert out["publish"]["enabled"] is False
        assert out["notify"]["webhook_url"] == "***"


class TestSanitizePlatforms:
    def test_x_account_credentials(self):
        cfg = {
            "platforms": {
                "x": {
                    "accounts": [
                        {"id": "main", "credentials": "BEARER_TOKEN_xyz"},
                        {"id": "alt", "credentials": "OTHER"},
                    ]
                }
            }
        }
        out = sanitize_config(cfg)
        assert out["platforms"]["x"]["accounts"][0]["credentials"] == "***"
        assert out["platforms"]["x"]["accounts"][1]["credentials"] == "***"
        # id 不在白名单路径里，保持原值
        assert out["platforms"]["x"]["accounts"][0]["id"] == "main"

    def test_toutiao_cookies(self):
        cfg = {
            "platforms": {
                "toutiao": {
                    "accounts": [{"id": "main", "cookies": "/path/to/cookies.json"}]
                }
            }
        }
        out = sanitize_config(cfg)
        assert out["platforms"]["toutiao"]["accounts"][0]["cookies"] == "***"

    def test_xiaohongshu_cookies(self):
        cfg = {
            "platforms": {
                "xiaohongshu": {
                    "accounts": [{"id": "main", "cookies": "/path/cookies.json"}]
                }
            }
        }
        out = sanitize_config(cfg)
        assert out["platforms"]["xiaohongshu"]["accounts"][0]["cookies"] == "***"

    def test_empty_accounts_list(self):
        # accounts 列表为空——通配符分支遍历 0 个元素不报错
        cfg = {"platforms": {"x": {"accounts": []}}}
        out = sanitize_config(cfg)
        assert out["platforms"]["x"]["accounts"] == []


class TestSanitizeMissingPaths:
    def test_missing_platform_key(self):
        # platforms 存在但没 x 键——中途字段缺失分支（line 49）
        cfg = {"platforms": {"toutiao": {"accounts": []}}}
        out = sanitize_config(cfg)
        assert out["platforms"]["toutiao"] == {"accounts": []}

    def test_missing_top_level(self):
        # 没有任何敏感路径——直接返回
        cfg = {"publish": {"enabled": False}}
        assert sanitize_config(cfg) == {"publish": {"enabled": False}}

    def test_platforms_key_absent(self):
        cfg = {"notify": {"webhook_url": "x"}}
        out = sanitize_config(cfg)
        # platforms 路径完全走不到，webhook 仍被脱敏
        assert out["notify"]["webhook_url"] == "***"

    def test_intermediate_value_is_none(self):
        # 路径中途遇到 None 值（line 33 early return）
        cfg = {"platforms": None}
        # sanitize_config 在 None 输入 fast return；这里测路径中途 None
        # 真实场景：外层 dict 有 platforms 键但值是 None
        cfg2 = {"platforms": {"x": None}}
        out = sanitize_config(cfg2)
        assert out["platforms"]["x"] is None


class TestSanitizePathEdgeCases:
    def test_wildcard_only_path_ignored(self):
        # 末级是通配符（i == len(path)-1 且 key=='*'）——line 40 防御性 return
        # 实际 _SENSITIVE_PATHS 里没有这种路径，但函数本身要正确处理
        from pipeline.webui.sanitize import _mask_path

        # 直接构造"只有通配符"的路径调用 _mask_path
        obj = {"a": [{"credentials": "x"}]}
        _mask_path(obj, ("*",))  # 末级就是通配符
        # 不应抛错，obj 应保持原样
        assert obj == {"a": [{"credentials": "x"}]}

    def test_wildcard_terminal_on_list(self):
        # 路径末级是 '*' 且当前是 list——line 40 防御性 return
        from pipeline.webui.sanitize import _mask_path

        obj = {"items": ["a", "b", "c"]}
        _mask_path(obj, ("items", "*"))  # 末级是 *
        # list 元素不应被当作字段名（不会变 "***"）
        assert obj == {"items": ["a", "b", "c"]}
