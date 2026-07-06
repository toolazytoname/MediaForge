"""抖音发布选择器集中（HARD_PARTS §2 防腐层）。

抖音创作者中心地址 = https://creator.douyin.com/
发布页（上传视频）：/creator-micro/home/upload/video

**AI 生成内容标识**（PRD §3.4 必填）：
中国互联网监管要求所有 AI 生成 / AI 显著修改的内容须明确声明。
抖音创作者中心提供「内容含 AI 生成」勾选框 + 「AI 生成成分占比」下拉；
**不勾选 = 违规，可能下架 + 账号扣分**。

设计参考：
- social-auto-upload/douyin_uploader（13k⭐）：patchright + storage_state + success-URL 判定
- AiToEarn electron 遗留代码（MIT，project/aitoearn-electron/electron/plat/douyin/）
  中的 creator 后台私有接口 + cookie 判活字段
"""
from __future__ import annotations

# ── 登录 / 健康检查 ─────────────────────────────────────

# 创作者中心主页（健康检查目标；非登录会跳 passport/）
PROFILE_URL_FALLBACK = (
    "https://creator.douyin.com/creator-micro/home",
    "https://creator.douyin.com/",
    "https://www.douyin.com/",
)

# 未登录页面典型关键词
LOGIN_INDICATORS: tuple[str, ...] = (
    "扫码登录",
    "请先登录",
    "未登录",
    "passport",
)


# ── 发布页 ────────────────────────────────────────────────

# 视频上传页入口
PUBLISH_URL_FALLBACK = (
    "https://creator.douyin.com/creator-micro/home/upload/video",
    "https://creator.douyin.com/creator-micro/upload",
)

# 视频上传 input[type=file]（抖音支持拖拽 + 文件选择）
VIDEO_FILE_INPUT: tuple[str, ...] = (
    "input[type='file'][accept*='video']",
    "input[type='file'][accept*='mp4']",
    "input[type='file']",
)

# 标题输入
TITLE_SELECTORS: tuple[str, ...] = (
    "input[placeholder*='标题']",
    "input[placeholder*='作品标题']",
    "input.title-input",
    "div[contenteditable='true'][data-placeholder*='标题']",
)

# 简介 / 描述
DESC_SELECTORS: tuple[str, ...] = (
    "textarea[placeholder*='简介']",
    "textarea[placeholder*='描述']",
    "textarea[placeholder*='说点什么']",
    ".editor-content[contenteditable='true']",
)


# ── AI 生成内容标识（PRD §3.4 必勾） ──────────────────

# 「含 AI 生成内容」勾选框 / 开关
AI_DECLARE_CHECKBOX: tuple[str, ...] = (
    "input[type='checkbox'][data-type='ai-generated']",
    "input[type='checkbox'][aria-label*='AI']",
    "label:has-text('AI 生成') input[type='checkbox']",
    "label:has-text('内容含 AI') input[type='checkbox']",
    ".ai-declare input[type='checkbox']",
)

# AI 生成成分占比（必选：低 / 中 / 高）
AI_DECLARE_RATIO: tuple[str, ...] = (
    "select[data-type='ai-ratio']",
    ".ai-declare select",
    "select[name='ai_ratio']",
)

# 提交按钮
SUBMIT_BUTTON: tuple[str, ...] = (
    "button.publish-btn",
    "button:has-text('发布')",
    "button:has-text('发表')",
    "button[data-type='publish']",
)

# 成功标志（通常跳到作品管理页）
SUCCESS_URL_PATTERN: tuple[str, ...] = (
    "/creator-micro/content/manage",
    "/creator-micro/manage/video",
    "/content/manage",
)


# ── 标签 / 话题（视频可选） ───────────────────────────────

HASHTAG_INPUT: tuple[str, ...] = (
    "input[placeholder*='话题']",
    "input[placeholder*='添加话题']",
    ".hashtag-input input",
)


__all__ = [
    "PROFILE_URL_FALLBACK",
    "LOGIN_INDICATORS",
    "PUBLISH_URL_FALLBACK",
    "VIDEO_FILE_INPUT",
    "TITLE_SELECTORS",
    "DESC_SELECTORS",
    "AI_DECLARE_CHECKBOX",
    "AI_DECLARE_RATIO",
    "SUBMIT_BUTTON",
    "SUCCESS_URL_PATTERN",
    "HASHTAG_INPUT",
]