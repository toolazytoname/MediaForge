"""main() 启动入口必须初始化真实 LLM/图片 provider。

回归背景：webui 之前从未调用 setup_provider_from_env()，导致 SPA 全程用
MockProvider（固定返回 "OK"），衍生/出图必然 500。main() 是唯一的启动入口，
必须在 uvicorn.run() 之前完成 provider 初始化。
"""
from __future__ import annotations

from unittest.mock import patch

from pipeline.webui import app as app_mod
from pipeline.webui import deps


def test_main_initializes_llm_and_image_providers_before_serving(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(deps, "_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.chdir(tmp_path)

    calls: list[str] = []

    with patch("uvicorn.run") as mock_run, patch(
        "pipeline.creators.llm.setup_provider_from_env",
        side_effect=lambda: calls.append("llm"),
    ) as mock_llm_setup, patch(
        "pipeline.creators.image_gen.setup_provider_from_env",
        side_effect=lambda: calls.append("image_gen"),
    ) as mock_image_setup, patch(
        "pipeline.webui.deps.load_config", side_effect=RuntimeError("no config")
    ):
        result = app_mod.main()

    assert result == 0
    mock_llm_setup.assert_called_once()
    mock_image_setup.assert_called_once()
    # provider 必须在真正开始 serve 请求之前初始化好
    assert calls == ["llm", "image_gen"]
    mock_run.assert_called_once()
