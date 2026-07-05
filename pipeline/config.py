"""config.yaml 加载与校验（TECH_SPEC §6 契约）。

pydantic v2 模型；load_config(path) 失败时报清晰错误（字段路径 + 期望类型）。
新增字段走 schema 迁移函数（不在本任务范围；M6 视情况补）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


# ── Pillars ───────────────────────────────────────────────

class Pillar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    scoring_hint: str


# ── Sources（discriminated union by 'type'） ──────────────

class SourceRSS(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["rss"]
    name: str
    url: str
    enabled: bool = True
    max_items: int = 30


class SourceDailyHot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["dailyhot"]
    name: str
    base_url: str
    boards: list[str] = Field(default_factory=list)
    enabled: bool = False
    max_items: int = 20


Source = Annotated[
    Union[SourceRSS, SourceDailyHot],
    Field(discriminator="type"),
]


# ── Topics ────────────────────────────────────────────────

class TopicsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_quota: int = 5
    min_score: float = 6.0
    expire_days: int = 3


# ── LLM ───────────────────────────────────────────────────

class LLMTiers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cheap: str
    creative: str
    critical: str


class LLMBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_usd: float = 80.0


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: LLMTiers


# ── Gate ──────────────────────────────────────────────────

class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_total: int = 24
    threshold_each: int = 6
    max_rewrites: int = 1


# ── Review ────────────────────────────────────────────────

class ReviewConfig(BaseModel):
    """policy: 'manual' 或 'auto_above:N'（N 为分数阈值）。"""
    model_config = ConfigDict(extra="forbid")
    policy: str = "manual"


# ── Render ────────────────────────────────────────────────

class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["template", "claude_skills"] = "template"


class ImageGenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["none", "gemini", "openai", "baoyu"] = "none"


# ── Video ─────────────────────────────────────────────────

class MPTConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = "http://127.0.0.1:8080"
    voice: str = "zh-CN-YunxiNeural"
    poll_interval_s: int = 30
    timeout_s: int = 1200


class VideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["mpt", "openmontage", "aigcpanel", "pixelle"] = "mpt"
    mpt: MPTConfig = Field(default_factory=MPTConfig)


# ── Publish ───────────────────────────────────────────────

class PublishConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    allowed_platforms: list[str] = Field(default_factory=list)
    min_gap_hours: int = 4
    max_daily_per_account: int = 3
    cross_platform_gap_minutes: int = 30


# ── Platforms ─────────────────────────────────────────────

class AccountAPI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    credentials: str


class AccountPlaywright(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    cookies: str


class PlatformAPI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["api"]
    windows: list[str]
    accounts: list[AccountAPI]


class PlatformPlaywright(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["playwright"]
    windows: list[str]
    accounts: list[AccountPlaywright]


Platform = Annotated[
    Union[PlatformAPI, PlatformPlaywright],
    Field(discriminator="kind"),
]


class PlatformsConfig(BaseModel):
    """每个 platform key 可选；缺则视为未启用。"""
    model_config = ConfigDict(extra="forbid")
    x: Platform | None = None
    toutiao: Platform | None = None
    xiaohongshu: Platform | None = None


# ── WebUI ─────────────────────────────────────────────────

class WebUIAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str
    password_env: str


class WebUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8787
    auth: WebUIAuth | None = None


# ── Notify ────────────────────────────────────────────────

class NotifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_url: str | None = None


# ── 顶层 ──────────────────────────────────────────────────

class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "Asia/Shanghai"
    pillars: list[Pillar]
    sources: list[Source] = Field(default_factory=list)
    topics: TopicsConfig = Field(default_factory=TopicsConfig)
    llm: LLMConfig
    budget: LLMBudget = Field(default_factory=LLMBudget)
    gate: GateConfig = Field(default_factory=GateConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    image_gen: ImageGenConfig = Field(default_factory=ImageGenConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


# ── 加载入口 ───────────────────────────────────────────────

def load_config(path: str | Path) -> AppConfig:
    """读取并校验 config.yaml。失败抛 ValueError（含字段路径 + 期望类型）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")

    raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"config is empty: {p}")

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"]) or "<root>"
            inp = err.get("input")
            inp_repr = repr(inp) if inp is not None else "missing"
            lines.append(f"  {loc}: {err['msg']} (got {inp_repr})")
        raise ValueError(
            f"invalid config at {p}:\n" + "\n".join(lines)
        ) from e