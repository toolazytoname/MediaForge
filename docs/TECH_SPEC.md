# TECH_SPEC — 技术规格与接口契约

> 版本 v1.0 | 2026-07-04
> **本文档是契约。实现代码必须严格遵守，发现契约缺陷 → 在 TASKS.md 记录，不得擅自修改。**

## 1. 运行环境

- Python 3.11+，venv 于项目根 `.venv/`
- 依赖清单 `requirements.txt`（核心：`anthropic`, `pydantic`, `pyyaml`, `httpx`, `feedparser`, `pytest`, `playwright`）
- 所有命令从项目根运行：`python -m pipeline.run <subcommand>`

## 2. CLI 契约

```
python -m pipeline.run init-db              # 建表（幂等）
python -m pipeline.run ingest               # 拉取所有启用的数据源
python -m pipeline.run score                # 给 raw topics 评分并选出当日 selected
python -m pipeline.run create               # 为 selected topics 生成内容
python -m pipeline.run gate                 # 质量门禁
python -m pipeline.run review [--notify]    # 生成/读取审核清单
python -m pipeline.run schedule             # 为 approved 内容排期
python -m pipeline.run publish [--dry-run]  # 发布到期的 publication
python -m pipeline.run collect              # 回流表现数据
python -m pipeline.run status               # 打印各状态计数表
python -m pipeline.run reset <id> <status>  # 人工重置状态（唯一允许的逆向操作）
python -m pipeline.run webui                # 启动本地 Web 控制台（默认 127.0.0.1:8787）
```

- 所有子命令：成功 exit 0；有失败项但流程完成 exit 0 + 日志 warning；致命错误 exit 1
- 全局参数：`--config path`（默认 `./config.yaml`）、`--verbose`
- 每个子命令处理完打印一行摘要：`ingest: 42 fetched, 31 new, 11 dup`

## 3. 数据模型（SQLite schema）

> `pipeline/db.py` 中以 `CREATE TABLE IF NOT EXISTS` 建表。字段增加走 ALTER TABLE 迁移函数，不删不改已有字段。

```sql
CREATE TABLE topics (
    id            TEXT PRIMARY KEY,          -- 't_' + 8位随机hex
    source        TEXT NOT NULL,             -- 数据源名: 'rss:hn' / 'github_trending' ...
    title         TEXT NOT NULL,
    url           TEXT,
    summary       TEXT,                      -- 原始摘要（截断至2000字符）
    content_hash  TEXT NOT NULL UNIQUE,      -- sha256(normalized_title + domain)
    pillar        TEXT,                      -- 匹配的内容支柱 id，score 阶段填
    score         REAL,                      -- 0-10，score 阶段填
    score_reason  TEXT,
    status        TEXT NOT NULL DEFAULT 'raw',
                  -- raw|scored|selected|consumed|rejected
    created_at    TEXT NOT NULL,             -- ISO8601 UTC
    updated_at    TEXT NOT NULL
);

CREATE TABLE contents (
    id            TEXT PRIMARY KEY,          -- 'c_' + 8位随机hex
    topic_id      TEXT NOT NULL UNIQUE REFERENCES topics(id),  -- 1:1
    pillar        TEXT NOT NULL,
    title         TEXT NOT NULL,
    canonical_path TEXT NOT NULL,            -- output/.../canonical.md 相对路径
    formats       TEXT NOT NULL DEFAULT '[]',-- JSON数组: ["toutiao","xiaohongshu","x"]
    gate_score_total REAL,
    gate_scores   TEXT,                      -- JSON: {"info":8,"fun":7,"view":8}
    gate_verdict  TEXT,                      -- 门禁评语
    status        TEXT NOT NULL DEFAULT 'draft',
                  -- draft|gated|approved|rejected_by_human|discarded|failed|done
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE publications (
    id            TEXT PRIMARY KEY,          -- 'p_' + 8位随机hex
    content_id    TEXT NOT NULL REFERENCES contents(id),
    platform      TEXT NOT NULL,             -- 'toutiao'|'xiaohongshu'|'x'|'wechat_mp'|...
    account_id    TEXT NOT NULL,             -- config 中的账号别名
    scheduled_at  TEXT NOT NULL,             -- ISO8601 UTC
    published_at  TEXT,
    platform_post_id TEXT,                   -- 平台返回的帖子ID
    platform_url  TEXT,
    error         TEXT,
    retry_count   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'queued',
                  -- queued|publishing|published|failed|cancelled
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(content_id, platform, account_id)  -- 防重复发布的最后防线
);

CREATE TABLE metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id TEXT NOT NULL REFERENCES publications(id),
    collected_at  TEXT NOT NULL,
    views         INTEGER, likes INTEGER, comments INTEGER,
    shares        INTEGER, followers_delta INTEGER,
    raw           TEXT                       -- 平台原始返回 JSON
);

CREATE TABLE llm_calls (                     -- 成本审计表
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stage         TEXT NOT NULL,             -- 'score'|'create'|'gate'...
    ref_id        TEXT,                      -- 关联的 topic/content id
    model         TEXT NOT NULL,
    input_tokens  INTEGER, output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);
```

## 4. Python 数据模型

`pipeline/models.py` 使用 **frozen dataclass**（不可变）。状态变更通过 `db.py` 的显式函数完成，返回新对象：

```python
@dataclass(frozen=True)
class Topic:
    id: str
    source: str
    title: str
    url: str | None
    summary: str | None
    content_hash: str
    pillar: str | None
    score: float | None
    score_reason: str | None
    status: str          # TopicStatus 枚举值
    created_at: str
    updated_at: str
```

`Content`、`Publication`、`Metric` 同理，字段与 SQL 一一对应。

**状态枚举**（`models.py`，str Enum）：

```python
class TopicStatus(str, Enum):
    RAW = "raw"; SCORED = "scored"; SELECTED = "selected"
    CONSUMED = "consumed"; REJECTED = "rejected"

class ContentStatus(str, Enum):
    DRAFT = "draft"; GATED = "gated"; APPROVED = "approved"
    REJECTED_BY_HUMAN = "rejected_by_human"; DISCARDED = "discarded"
    FAILED = "failed"; DONE = "done"

class PublicationStatus(str, Enum):
    QUEUED = "queued"; PUBLISHING = "publishing"; PUBLISHED = "published"
    FAILED = "failed"; CANCELLED = "cancelled"
```

**合法状态转移表**（`db.py` 的 `transition()` 函数强制校验，非法转移抛 `IllegalTransition`）：

```python
TOPIC_TRANSITIONS = {
    "raw": {"scored", "rejected"},
    "scored": {"selected", "rejected"},
    "selected": {"consumed"},
}
CONTENT_TRANSITIONS = {
    "draft": {"gated", "discarded", "failed"},
    "gated": {"approved", "rejected_by_human"},
    "approved": {"done"},
}
PUBLICATION_TRANSITIONS = {
    "queued": {"publishing", "cancelled"},
    "publishing": {"published", "failed"},
    "failed": {"queued"},          # 仅 reset 命令可走
}
```

## 5. 适配器接口契约

### 5.1 SourceAdapter（`pipeline/sources/base.py`）

```python
class SourceAdapter(ABC):
    name: str                      # 唯一标识，如 'rss:hn'

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """抓取最新条目。网络错误抛 SourceError（编排层捕获、跳过该源、继续其他源）。
        返回值按发布时间倒序，最多 max_items（config）条。"""

@dataclass(frozen=True)
class RawItem:
    title: str
    url: str | None
    summary: str | None
    published_at: str | None       # ISO8601，尽力解析，None 表示未知
```

- 实现类不接触数据库。入库、去重由编排层统一完成
- 新增数据源 = 新增一个文件 + config 注册，不改任何已有代码

### 5.2 PublisherAdapter（`pipeline/publishers/base.py`）

```python
class PublisherAdapter(ABC):
    platform: str                  # 'toutiao'|'xiaohongshu'|'x'|...

    @abstractmethod
    def validate(self, bundle: PostBundle) -> list[str]:
        """检查内容是否满足平台格式要求（字数/图片数/尺寸），返回问题列表，空=通过。
        不做网络请求。"""

    @abstractmethod
    def publish(self, bundle: PostBundle, account: AccountConfig,
                dry_run: bool) -> PublishResult:
        """执行发布。dry_run=True 时完成所有前置步骤但不点发布，返回模拟结果。
        任何异常包装成 PublishError 抛出（编排层负责状态与重试）。
        本方法必须可安全中断：中断后重跑不产生重复帖（靠编排层 publishing 状态锁）。"""

@dataclass(frozen=True)
class PostBundle:
    content_id: str
    title: str
    body_path: Path                # 平台格式化后的正文文件
    media_paths: tuple[Path, ...]  # 图/视频
    tags: tuple[str, ...]
    extra: dict                    # 平台特有字段（如头条的封面选择）

@dataclass(frozen=True)
class PublishResult:
    platform_post_id: str | None
    url: str | None
    raw_response: str
```

### 5.3 LLM 封装（`pipeline/creators/llm.py`）

```python
def complete(prompt: str, *, stage: str, ref_id: str | None,
             model_tier: str = "creative",     # 'cheap'|'creative'|'critical'
             max_tokens: int = 4096) -> str:
    """统一 LLM 入口。职责：
    1. model_tier → 具体模型映射来自 config.llm.tiers
    2. 每次调用记录 llm_calls 表（tokens+成本）
    3. 月度成本超 config.budget.monthly_usd → 抛 BudgetExceeded（除非 stage='gate'，门禁永不跳过）
    4. 429/5xx 指数退避重试 3 次
    5. prompt 与响应存 logs/llm/ 供调试（文件名=ref_id+stage+时间戳）"""
```

**所有模块禁止直接 import anthropic**，只能走这个入口——成本控制和审计依赖此约束。

### 5.4 视觉渲染（`pipeline/creators/render.py`）— 完全独立运行，不依赖 Claude Code

**设计原则：整条流水线是独立 Python 程序，只需 API token（Anthropic API key、平台凭据）即可在任何机器上 cron 运行，不依赖 Claude Code CLI。**

图卡/封面/配图通过「HTML 模板 + Playwright 截图」本地渲染：

```python
def render_cards(template: str, slides: list[dict], out_dir: Path,
                 viewport: tuple[int, int] = (1080, 1440)) -> list[Path]:
    """LLM 产出结构化 slides JSON（标题/要点/正文），注入 templates/<template>.html
    （Jinja2），用 Playwright headless Chromium 截图为 PNG。
    模板库 templates/ 是本仓库资产：xhs_card.html / cover.html / quote.html。
    无外部服务依赖，离线可跑。"""
```

- 配图（插画类）走图像生成 API（config `image_gen.provider`: `none|gemini|openai|baoyu`），provider=`none` 时降级为纯模板文字卡——保证无图像 API 也能出片；`baoyu` 走 subprocess 调 `JimLiu/baoyu-skills` 的 `baoyu-image-gen`（12 provider：OpenAI/Azure/Google/OpenRouter/DashScope/Z.AI/Jimeng/Seedream/MiniMax/Replicate/codex-cli/Agnes；`--json` 出口返回 `savedImage` 文件路径，API key 走环境变量注入，无需 EXTEND.md），见 evaluation-notes §5
- 视频封面同理走模板渲染

### 5.5 （可选增强，非核心路径）Claude Code skills 桥

`pipeline/creators/skills_bridge.py`：本机装有 Claude Code 时，可用 `claude -p "/baoyu-xhs-images ..."` 获得更精致的图卡。config `render.engine: template`（默认）或 `claude_skills`。**所有 M 里程碑的验收只以 template 引擎为准**，skills 桥坏了不影响流水线。Skills 桥主要承载 Claude 编排型 skill（xhs-images/cover-image/infographic 等）；`baoyu-image-gen` 因是纯 CLI，由 §5.4 `render.py` 直接 subprocess 调用，不走桥。

### 5.6 VideoEngine 接口（`pipeline/creators/video/base.py`）

视频生成必须走引擎抽象，保证可扩展（MoneyPrinterTurbo 之外未来接 OpenMontage 等）：

```python
class VideoEngine(ABC):
    name: str                      # 'mpt' | 'openmontage' | ...

    @abstractmethod
    def submit(self, req: VideoRequest) -> str:
        """提交生成任务，返回 engine 内部 job_id。失败抛 CreateError。"""

    @abstractmethod
    def poll(self, job_id: str) -> VideoJobStatus:
        """查询状态：pending|running|done|failed（含进度与错误信息）。"""

    @abstractmethod
    def fetch(self, job_id: str, dest: Path) -> Path:
        """下载成品 mp4 到 dest，返回最终路径。"""

@dataclass(frozen=True)
class VideoRequest:
    content_id: str
    script: str                    # 我方 LLM 产出的口播稿/脚本（不让引擎自己写文案）
    duration_s: int                # 目标时长
    aspect: str                    # '9:16' | '16:9'
    style: dict                    # 引擎特有参数（音色/模板/素材偏好）
```

- config `video.engine: mpt`（默认）。引擎选择在派生阶段按内容标记可覆盖（头部精品内容可指定 `openmontage`）
- `mpt` 引擎：HTTP 客户端对接 MoneyPrinterTurbo API（HARD_PARTS §6）
- `openmontage` 引擎（可选，M5-3 评估后实现）：以 headless agent 一次性任务驱动（subprocess 调 `claude -p` 或 Agent SDK），产物回收到 output 目录。**该引擎不可用不得影响 mpt 链路**（工厂函数捕获初始化失败降级）

## 6. config.yaml 契约

见根目录 `config.example.yaml`（含全部字段与注释）。加载即用 pydantic 校验，缺字段/类型错在启动时报错退出。要点：

- `pillars`: 内容支柱列表（id/name/描述/评分提示）
- `sources`: 数据源开关与参数
- `llm.tiers`: cheap/creative/critical → 模型 ID 映射
- `budget.monthly_usd`: LLM 月预算硬顶
- `gate.threshold_total: 24` / `gate.threshold_each: 6` / `gate.max_rewrites: 1`
- `review.policy`: `manual` 或 `auto_above:27`
- `publish.enabled`（默认 **false**）/ `publish.allowed_platforms` / `publish.min_gap_hours`
- `platforms.<name>.accounts[]`: 账号别名 + 凭据文件路径（指向 secrets/）
- `notify.webhook_url`: 飞书/TG webhook（可空）

## 7. Web 控制台契约（webui）

- 技术栈：
  - 后端 FastAPI（`pipeline/webui/app.py` + `pipeline/webui/api/*`），暴露 `/api/v1/*` JSON API
  - 前端 SPA（`frontend/` 源码，Vue 3 + Vite + TypeScript + Ant Design Vue + Pinia + Vue Router + ECharts），构建产物 `frontend/dist/`，由 FastAPI `StaticFiles` 挂载 `/assets` + 客户端路由 catch-all 返回 `index.html`
  - **引入 npm 构建链**（与 §7 原 htmx/jinja2 单文件方案不同；构建步骤见 `docs/GETTING_STARTED.md`「前端构建」节）
  - 旧 htmx + jinja2 路由（`/`、`/topics`、`/review` 等）标注 legacy 保留——SPA 达 parity 后移除
  - 启动：`python -m pipeline.run webui`，绑定 `config.webui.host:port`（默认 `127.0.0.1:8787`）
- **不变量（契约红线，spa/htmx 通用）**：
  - **UI 不得直接写 SQL**：读走 `db.py` / `db_reads.py` 查询函数，写走 `transition()` / `set_gate_verdict` / `reschedule_publication` 与既有编排函数——状态机与发布三重锁对 UI 同样生效
  - **发布需 dry-run 先行 + 显式确认**（二次确认弹条 + 复用 `safe_publish` 三重锁）；`publish` **排除于通用运行台白名单**（仅 `ingest/score/create/gate/derivative/review/schedule/collect/generate-images` 可由 UI 一键触发）
- 路由契约：

```
# 新：JSON API（FastAPI router，前端 SPA 主用）
GET  /api/v1/dashboard           Dashboard 计数/成本/待办/近期活动
GET  /api/v1/topics              选题列表（status/pillar/source/limit/offset 过滤）
GET  /api/v1/sources             选题数据源列表
GET  /api/v1/contents            内容列表
GET  /api/v1/contents/{id}       内容详情（canonical HTML + 派生文件 + 图片 + 时间线）
GET  /api/v1/review              审核台（gated 内容列表）
GET  /api/v1/publish/calendar    发布日历（?week=YYYY-MM-DD）
GET  /api/v1/publish/records     发布记录列表
GET  /api/v1/analytics/weekly    周报
GET  /api/v1/analytics/cost      LLM 成本（group=stage|day）
GET  /api/v1/analytics/publications/{id}/metrics  表现数据序列
GET  /api/v1/analytics/platforms 平台汇总
GET  /api/v1/accounts            账号 + cookie 健康
GET  /api/v1/accounts/login-guidance  各平台登录引导
GET  /api/v1/runs                运行历史
GET  /api/v1/settings            config 脱敏展示 + doctor 报告

# 旧：htmx legacy（标注 deprecated，SPA parity 后移除）
GET  /                           Dashboard（htmx 三表计数）
GET  /topics?status=             选题池列表
POST /topics/{id}/promote        加急（scored→selected）
POST /topics/{id}/reject         废弃
GET  /review                     审核台（gated 内容卡片流）
POST /review/{content_id}        body: {decision: approve|reject, reason?}
GET  /calendar?week=             发布日历
POST /publications/{id}/reschedule   body: {scheduled_at}
POST /publications/{id}/cancel
POST /publications/{id}/retry        (failed→queued，走 reset 逻辑)
GET  /contents/{id}              内容详情（全链路时间线）
GET  /settings                   config 脱敏展示 + 登录态健康
GET  /api/status                 JSON 状态计数（给页面轮询用）
```

- JSON API 错误统一格式 `{error:{code,message}}` + 对应 HTTP 码；旧 htmx 路由错误统一返回带 `role=alert` 的片段
- 文件预览：canonical.md 渲染为 HTML；图卡 PNG 直接 `<img>`（`/output` 挂静态目录，只读）

## 8. 错误处理与日志规范

- 分层错误类型：`SourceError` / `CreateError` / `GateError` / `PublishError` / `BudgetExceeded`（都在 `pipeline/utils/errors.py`）
- 编排层原则：**单条失败不阻断批次**——记录、标记该条 `failed`、继续下一条；**系统性失败**（数据库损坏、预算超限、config 无效）立即退出
- 日志：`pipeline/utils/log.py` 提供结构化 logger，每条日志必带 `stage` 与 `ref_id` 字段；文件 `logs/pipeline.log`（每天轮转，保留 30 天）
- 任何 except 分支必须要么 re-raise 要么 log.warning 以上级别记录——禁止裸 `except: pass`

## 9. 测试规范

- pytest，`tests/` 镜像 `pipeline/` 结构
- 覆盖率目标 80%（`pytest --cov=pipeline`）
- **必测清单**：
  - 状态机：每个非法转移抛 `IllegalTransition`
  - 去重：同 title+domain 二次入库被跳过
  - 排期：min_gap、错峰、避开整点（用固定随机种子）
  - 发布安全：`publish.enabled=false` 时任何路径都不能触达 `PublisherAdapter.publish`
  - 幂等：每个阶段对同一输入跑两次，结果与跑一次相同
- LLM 与平台调用一律 mock；`tests/fixtures/` 放录制的样例响应
- 集成测试 `tests/test_e2e_dryrun.py`：造一个假 topic，全流程跑到 publish --dry-run

## 10. 代码风格

遵守用户全局规则（`~/.claude/rules/common/coding-style.md`），本项目补充：

- 单文件 ≤ 400 行；函数 ≤ 50 行
- 所有公开函数有 type hints；`mypy --strict pipeline/` 为**目标**，尚未接入 CI 强制（仓库无 mypy 配置文件，CI 不跑；接入属另一任务）
- 时间一律 UTC ISO8601 字符串存储，展示层才转本地时区
- ID 生成：`utils/ids.py` 的 `new_id(prefix)`，禁止散落 uuid 调用
