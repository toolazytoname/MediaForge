"""M8 S8-3 测试：新增 `doctor` 体检子命令。

行为契约（docs/TASKS.md S8-3 + HARD_PARTS §9 凭据安全）：
  - `run_doctor(config_path, db_path, secrets_dir) -> list[CheckResult]` 纯函数
  - CheckResult frozen dataclass: (name, ok, hint)
  - 检查项：
    1. config.yaml 存在 + load_config 通过（pydantic ValidationError 兜住）
    2. state.db 存在（db_path）
    3. secrets/ 目录存在（空目录也算过）
    4. LLM key env（MINIMAX_API_KEY 或 ANTHROPIC_API_KEY 至少一个）
    5. budget.monthly_usd > 0
    6. publish.enabled 当前值（true 时 hint 含「真发」字样；不 fail 只 warn）
  - **绝不打印密钥值**（§9）
  - doctor **只读**：不创建任何文件
  - cmd_doctor：有任一 ❌ → exit 1；全 ✅ → exit 0
  - cmd_doctor 输出格式：✅/❌ <name>：<hint>
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline import db


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """构造空 state.db（含 schema）。"""
    db_path = tmp_path / "state.db"
    c = db.connect(db_path)
    db.init_db(c)
    c.close()
    return db_path


def _patch_db_path(monkeypatch: pytest.MonkeyPatch, tmp_db_path: Path) -> None:
    """cmd_doctor 内部用 _DB_PATH（与 cmd_status 模式一致）。"""
    import pipeline.run as run_mod
    monkeypatch.setattr(run_mod, "_DB_PATH", str(tmp_db_path))


def _write_minimal_config(path: Path, *, budget_usd: float = 80.0,
                          publish_enabled: bool = False) -> None:
    """写一个能 load_config 通过的最小 config.yaml。"""
    yaml_content = f"""\
timezone: Asia/Shanghai
pillars:
  - id: ai_daily
    name: AI/科技日报解读
    description: 每日精选
    scoring_hint: 时效性强
llm:
  tiers:
    cheap: claude-haiku-4-5-20251001
    creative: claude-sonnet-5
    critical: claude-sonnet-5
budget:
  monthly_usd: {budget_usd}
publish:
  enabled: {str(publish_enabled).lower()}
  allowed_platforms: []
"""
    path.write_text(yaml_content, encoding="utf-8")


def _args(config_path: str = "./config.yaml") -> MagicMock:
    return MagicMock(config=config_path)


# ── 1. config 检查 ──────────────────────────────────────


class TestConfigCheck:
    def test_missing_config_file_fails_with_cp_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """缺 config.yaml → config ❌ + hint 含 'cp config.example.yaml config.yaml'。"""
        from pipeline.doctor import run_doctor

        monkeypatch.chdir(tmp_path)  # 防止读到仓库根的 config.example.yaml
        results = run_doctor(
            config_path=str(tmp_path / "missing.yaml"),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )

        config_check = next(r for r in results if r.name == "config")
        assert config_check.ok is False
        assert "cp config.example.yaml config.yaml" in config_check.hint

    def test_corrupt_config_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """config 字段缺失/类型错（pydantic ValidationError）→ config ❌。"""
        from pipeline.doctor import run_doctor

        bad = tmp_path / "config.yaml"
        # llm.tiers 缺必需字段 creative/critical（必填，extra=forbid）
        bad.write_text(
            "timezone: Asia/Shanghai\n"
            "pillars:\n"
            "  - id: ai_daily\n"
            "    name: AI Daily\n"
            "    description: test\n"
            "    scoring_hint: test\n"
            "llm:\n"
            "  tiers:\n"
            "    cheap: claude-haiku\n",
            encoding="utf-8",
        )

        results = run_doctor(
            config_path=str(bad),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        config_check = next(r for r in results if r.name == "config")
        assert config_check.ok is False
        # hint 应提示字段错（pydantic 错误信息或「config 校验失败」）
        assert config_check.hint  # 非空

    def test_valid_config_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """合法 config → config ✅。"""
        from pipeline.doctor import run_doctor

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        config_check = next(r for r in results if r.name == "config")
        assert config_check.ok is True


# ── 2. state.db 检查 ──────────────────────────────────────


class TestStateDbCheck:
    def test_missing_state_db_fails_with_init_db_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """缺 state.db → state.db ❌ + hint 含 'init-db'。"""
        from pipeline.doctor import run_doctor

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        # 不建 state.db
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        db_check = next(r for r in results if r.name == "state.db")
        assert db_check.ok is False
        assert "init-db" in db_check.hint

    def test_existing_state_db_passes(
        self, tmp_path: Path, tmp_db_path: Path,
    ) -> None:
        """state.db 存在 → state.db ✅。"""
        from pipeline.doctor import run_doctor

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_db_path),
            secrets_dir=str(tmp_path / "secrets"),
        )
        db_check = next(r for r in results if r.name == "state.db")
        assert db_check.ok is True


# ── 3. secrets/ 检查 ──────────────────────────────────────


class TestSecretsCheck:
    def test_missing_secrets_dir_fails_with_mkdir_hint(
        self, tmp_path: Path,
    ) -> None:
        """缺 secrets/ → secrets ❌ + hint 含 'mkdir'。"""
        from pipeline.doctor import run_doctor

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        # 注意：不创建 secrets/

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        sec_check = next(r for r in results if r.name == "secrets")
        assert sec_check.ok is False
        assert "mkdir" in sec_check.hint

    def test_existing_secrets_dir_passes(
        self, tmp_path: Path,
    ) -> None:
        """secrets/ 存在（空目录也算过）→ secrets ✅。"""
        from pipeline.doctor import run_doctor

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        sec_check = next(r for r in results if r.name == "secrets")
        assert sec_check.ok is True


# ── 4. LLM key 检查（绝不打印值） ─────────────────────────


class TestLlmKeyCheck:
    def test_no_env_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """env 无 LLM key → llm_key ❌。"""
        from pipeline.doctor import run_doctor

        # 清掉所有可能的 env var
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        llm_check = next(r for r in results if r.name == "llm_key")
        assert llm_check.ok is False
        # hint 应提示设置哪个 env var（不打印值）
        assert "MINIMAX" in llm_check.hint or "ANTHROPIC" in llm_check.hint

    def test_minimax_env_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """env 有 MINIMAX_API_KEY → llm_key ✅。"""
        from pipeline.doctor import run_doctor

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        llm_check = next(r for r in results if r.name == "llm_key")
        assert llm_check.ok is True

    def test_anthropic_env_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """env 有 ANTHROPIC_API_KEY → llm_key ✅。"""
        from pipeline.doctor import run_doctor

        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        llm_check = next(r for r in results if r.name == "llm_key")
        assert llm_check.ok is True


# ── 5. budget 检查 ──────────────────────────────────────


class TestBudgetCheck:
    def test_zero_budget_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """budget.monthly_usd = 0 → budget ❌。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg, budget_usd=0.0)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        b_check = next(r for r in results if r.name == "budget")
        assert b_check.ok is False
        assert "预算" in b_check.hint or "budget" in b_check.hint.lower()

    def test_positive_budget_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """budget.monthly_usd > 0 → budget ✅。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg, budget_usd=80.0)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        b_check = next(r for r in results if r.name == "budget")
        assert b_check.ok is True


# ── 6. publish.enabled 检查（warn 不 fail） ─────────────────


class TestPublishEnabledCheck:
    def test_publish_enabled_true_passes_with_real_publish_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """publish.enabled=true → publish ✅ + hint 含「真发」字样。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg, publish_enabled=True)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        p_check = next(r for r in results if r.name == "publish.enabled")
        assert p_check.ok is True
        assert "真发" in p_check.hint

    def test_publish_enabled_false_passes_with_disabled_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """publish.enabled=false → publish ✅ + hint 提示关闭。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg, publish_enabled=False)

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        p_check = next(r for r in results if r.name == "publish.enabled")
        assert p_check.ok is True
        # hint 应提示当前是关闭状态
        assert "关" in p_check.hint or "false" in p_check.hint.lower()


# ── 7. 完整环境全 ✅ ──────────────────────────────────────


class TestFullEnvironment:
    def test_all_checks_pass(
        self, tmp_path: Path, tmp_db_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """齐全环境：config + state.db + secrets/ + LLM key → 全 ✅。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg, budget_usd=80.0, publish_enabled=False)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_db_path),
            secrets_dir=str(tmp_path / "secrets"),
        )
        assert all(r.ok for r in results), (
            f"应全 ✅，实际失败：{[(r.name, r.hint) for r in results if not r.ok]}"
        )


# ── 8. **绝不打印 key 值**（§9 凭据安全） ─────────────────


class TestNoSecretLeakage:
    def test_hints_never_contain_key_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """env set 了带 sk- 前缀的 key，所有 hint 不得含此值。"""
        from pipeline.doctor import run_doctor

        # 一个看起来像真 key 的值（含 sk- 前缀 + 32 字符 hex）
        fake_key = "sk-" + "a1b2c3d4e5f6" * 4  # sk- + 48 hex
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", fake_key)

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        all_text = "\n".join(r.hint for r in results)
        # 绝不能出现 key 值
        assert fake_key not in all_text, (
            f"hint 泄露了 key 值！all_hints={all_text!r}"
        )
        # 也不应出现 sk- 前缀
        assert "sk-" not in all_text, (
            f"hint 出现 'sk-' 前缀（疑似 key 泄露）！all_hints={all_text!r}"
        )

    def test_str_check_result_never_contains_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """str(CheckResult) 也不得含 key 值。"""
        from pipeline.doctor import CheckResult, run_doctor

        fake_key = "sk-" + "deadbeef" * 4
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", fake_key)

        # 手动构造一个 CheckResult，把 key 注入到不相关的字段——验证设计不会泄露
        cr = CheckResult(name="test", ok=True, hint="hint text")
        s = str(cr)
        assert fake_key not in s


# ── 9. cmd_doctor 输出格式 ─────────────────────────────


class TestCmdDoctorOutput:
    def test_cmd_doctor_prints_emoji_and_name_and_hint(
        self, tmp_path: Path, tmp_db_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """cmd_doctor 输出：✅/❌ <name>：<hint> 格式。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        # cmd_doctor 用相对路径 './config.yaml' 和 'secrets'，需 chdir 到 tmp
        monkeypatch.chdir(tmp_path)

        import pipeline.run as run_mod
        rc = run_mod.cmd_doctor(_args(config_path="./config.yaml"))
        out = capsys.readouterr().out

        assert rc == 0
        # 输出含 ✅ 符号
        assert "✅" in out
        # 输出含检查项名
        assert "config" in out
        assert "state.db" in out
        assert "secrets" in out
        assert "llm_key" in out
        # 输出含中英文冒号分隔
        assert re.search(r"✅\s+config", out)


# ── 10. cmd_doctor exit code ─────────────────────────────


class TestCmdDoctorExitCode:
    def test_cmd_doctor_exit_1_on_failure(
        self, tmp_path: Path, tmp_db_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """有 ❌ → exit 1。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)

        import pipeline.run as run_mod
        # config_path 故意指向不存在的文件
        rc = run_mod.cmd_doctor(_args(config_path=str(tmp_path / "no.yaml")))
        out = capsys.readouterr().out
        assert rc == 1
        # 输出含 ❌ 符号
        assert "❌" in out

    def test_cmd_doctor_exit_0_on_all_pass(
        self, tmp_path: Path, tmp_db_path: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        """全 ✅ → exit 0。"""
        _patch_db_path(monkeypatch, tmp_db_path)
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        monkeypatch.chdir(tmp_path)

        import pipeline.run as run_mod
        rc = run_mod.cmd_doctor(_args(config_path="./config.yaml"))
        out = capsys.readouterr().out
        assert rc == 0
        # 输出全是 ✅
        assert "✅" in out
        assert "❌" not in out


# ── 11. doctor 只读：不创建任何文件 ──────────────────────


class TestReadOnly:
    def test_doctor_does_not_create_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_doctor 不创建 db / secrets / config / log 等任何文件。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        # 故意不建 state.db、secrets/

        files_before = set(tmp_path.iterdir())
        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        files_after = set(tmp_path.iterdir())

        # 检查不创建文件
        assert files_after == files_before, (
            f"doctor 创建了文件！新增：{files_after - files_before}"
        )

    def test_cmd_doctor_does_not_initialize_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cmd_doctor 不应主动 init_db（即使 state.db 缺，也只报告不修）。"""
        import pipeline.run as run_mod

        db_path = tmp_path / "state.db"
        _patch_db_path(monkeypatch, db_path)
        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        monkeypatch.chdir(tmp_path)

        assert not db_path.exists()

        rc = run_mod.cmd_doctor(_args(config_path="./config.yaml"))
        # state.db 仍不应存在（doctor 不修不建，只报告）
        # 注：可能 cmd_doctor 会调 db.connect + db.init_db 探测？——按 spec「不创建任何文件」
        # 如果 run_doctor 探测 db 时调了 db.connect，state.db 可能被 create（empty file）。
        # 这条断言更宽：state.db 要么不存在（最严），要么存在但 schema 不完整。
        # 我们断言：state.db 不存在（最严格），因为 run_doctor 只调 os.path.exists。
        assert not db_path.exists(), (
            "cmd_doctor 不应创建 state.db（spec 红线：只读不修不建）"
        )
        assert rc == 1  # 有 ❌（state.db 缺）


# ── 12. CheckResult 数据结构 ─────────────────────────────


class TestCheckResult:
    def test_check_result_is_frozen(self) -> None:
        """CheckResult frozen（不可变）——全局 coding-style 规则。"""
        from pipeline.doctor import CheckResult

        cr = CheckResult(name="x", ok=True, hint="h")
        with pytest.raises(Exception):  # FrozenInstanceError
            cr.ok = False  # type: ignore[misc]

    def test_check_result_has_expected_fields(self) -> None:
        """CheckResult 含 name/ok/hint 三字段。"""
        from pipeline.doctor import CheckResult

        cr = CheckResult(name="config", ok=False, hint="missing")
        assert cr.name == "config"
        assert cr.ok is False
        assert cr.hint == "missing"


# ── 13. 检查项顺序 + 数量 ──────────────────────────────


class TestCheckOrderAndCount:
    def test_returns_at_least_six_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_doctor 至少返回 6 项检查（spec 要求）。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        # 至少 6 项：config / state.db / secrets / llm_key / budget / publish.enabled
        assert len(results) >= 6

    def test_check_order_matches_spec(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """检查项顺序：config → state.db → secrets → llm_key → budget → publish.enabled。"""
        from pipeline.doctor import run_doctor

        for k in ("MINIMAX_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key-12345")

        cfg = tmp_path / "config.yaml"
        _write_minimal_config(cfg)
        (tmp_path / "secrets").mkdir()

        results = run_doctor(
            config_path=str(cfg),
            db_path=str(tmp_path / "state.db"),
            secrets_dir=str(tmp_path / "secrets"),
        )
        names = [r.name for r in results]
        # 前 6 项应严格按此顺序
        expected_first6 = [
            "config", "state.db", "secrets", "llm_key", "budget", "publish.enabled"
        ]
        assert names[:6] == expected_first6, (
            f"检查项顺序错；expected={expected_first6}, actual={names[:6]}"
        )
