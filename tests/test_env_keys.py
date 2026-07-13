"""pipeline.env_keys 测试：secrets/env.json 持久化 + os.environ 合并。

背景：Settings 页新增可写的全局服务 key 配置能力，需要一个共享的持久化层
（此前只读活进程 env，无任何文件持久化）。这里验证：
  - `load_env_secrets`：文件不存在 no-op；文件存在时合并进 os.environ；
    已存在的真实进程 env 优先，不被文件覆盖。
  - `write_env_secret`/`delete_env_secret`：读写往返，幂等。
  - `mask`：绝不回传明文（HARD_PARTS §9）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline.env_keys import (
    delete_env_secret,
    load_env_secrets,
    mask,
    write_env_secret,
)


class TestLoadEnvSecrets:
    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        assert not path.exists()
        load_env_secrets(path)  # 不抛异常即通过

    def test_loads_keys_into_environ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "env.json"
        path.write_text(json.dumps({"MINIMAX_API_KEY": "from-file"}), encoding="utf-8")
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

        load_env_secrets(path)

        assert os.environ["MINIMAX_API_KEY"] == "from-file"

    def test_existing_process_env_wins_over_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "env.json"
        path.write_text(json.dumps({"MINIMAX_API_KEY": "from-file"}), encoding="utf-8")
        monkeypatch.setenv("MINIMAX_API_KEY", "from-real-env")

        load_env_secrets(path)

        assert os.environ["MINIMAX_API_KEY"] == "from-real-env"


class TestWriteDeleteEnvSecret:
    def test_write_then_read_back(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        write_env_secret("MINIMAX_API_KEY", "sk-123", path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"MINIMAX_API_KEY": "sk-123"}

    def test_write_preserves_other_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        write_env_secret("MINIMAX_API_KEY", "sk-123", path)
        write_env_secret("OPENAI_API_KEY", "sk-456", path)

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"MINIMAX_API_KEY": "sk-123", "OPENAI_API_KEY": "sk-456"}

    def test_delete_existing_key_returns_true(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        write_env_secret("MINIMAX_API_KEY", "sk-123", path)

        assert delete_env_secret("MINIMAX_API_KEY", path) is True
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {}

    def test_delete_missing_file_returns_false(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        assert delete_env_secret("MINIMAX_API_KEY", path) is False

    def test_delete_missing_key_returns_false(self, tmp_path: Path) -> None:
        path = tmp_path / "env.json"
        write_env_secret("OPENAI_API_KEY", "sk-456", path)

        assert delete_env_secret("MINIMAX_API_KEY", path) is False


class TestMask:
    def test_masks_all_but_last_four(self) -> None:
        assert mask("sk-1234567890") == "*********7890"

    def test_short_value_fully_masked(self) -> None:
        assert mask("abc") == "***"

    def test_empty_value_masked_to_empty(self) -> None:
        assert mask("") == ""
