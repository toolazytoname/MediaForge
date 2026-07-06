"""头条发布选择器集中（HARD_PARTS §2 防腐层）。

所有头条网页 DOM 选择器集中在此单文件；页面改版只修一处。
每个选择器尽量配 fallback，查找时按优先级依次尝试。

**这是平台接口契约的一部分，不是 platform 内部实现细节**——后续若启用
plan B（AitoEarn electron 摸底 / social-auto-upload）的选择器替换整个
模块即可，caller 代码不动。

参考：
- AiToEarn electron 遗留代码（MIT）：project/aitoearn-electron/electron/plat/toutiao/
- social-auto-upload toutiao_uploader 源码（13k⭐）

注：头条创作者平台地址 = https://mp.toutiao.com/
"""
from __future__ import annotations

# ── 登录 / 健康检查 ─────────────────────────────────────

# 个人主页 / 创作中心入口（用于 cookie 失效检测：未登录会跳登录页）
PROFILE_URL_FALLBACK = (
    "https://mp.toutiao.com/profile_v3/public/",
    "https://mp.toutiao.com/",
    "https://mp.toutiao.com/auth/",
)

# 未登录时页面典型信号（任一命中视为 cookie 失效）
LOGIN_INDICATORS: tuple[str, ...] = (
    "登录",
    "扫码登录",
    "手机登录",
    "请先登录",
)


# ── 创作者中心发布页 ─────────────────────────────────────

# 发布页入口（通常从首页 → 发布文章按钮可达）
PUBLISH_URL_FALLBACK = (
    "https://mp.toutiao.com/profile_v3/graphic/publish/",
    "https://mp.toutiao.com/tt-publish/article/",
)

# 标题输入框（多种 fallback：input / contenteditable div / ProseMirror）
TITLE_SELECTORS: tuple[str, ...] = (
    "input[placeholder*='标题']",
    "input[placeholder*='请输入文章标题']",
    "textarea[placeholder*='标题']",
    "div[contenteditable='true'][data-placeholder*='标题']",
    ".article-title input",
    ".article-title textarea",
)

# 正文编辑器（头条常用 ProseMirror / TipTap 类富文本）
BODY_SELECTORS: tuple[str, ...] = (
    "div.ProseMirror[contenteditable='true']",
    "div[contenteditable='true'].public-DraftEditor-content",
    "div[contenteditable='true'][data-placeholder*='正文']",
    "div.article-content[contenteditable='true']",
    ".editor-content[contenteditable='true']",
)

# 封面选择（头条通常有"自动封面"/"单图大图"/"三图"模式）
COVER_MODE_RADIO: tuple[str, ...] = (
    "input[type='radio'][value='auto']",
    "input[type='radio'][data-type='auto']",
    "label:has-text('自动封面')",
)

# 发布按钮（提交）
SUBMIT_BUTTON: tuple[str, ...] = (
    "button:has-text('发布')",
    "button:has-text('发表')",
    "button.publish-btn",
    ".submit-publish button",
)

# 发布成功标志 URL 模式（成功后会跳到发布管理页）
SUCCESS_URL_PATTERN: tuple[str, ...] = (
    "/publish/success",
    "/content/manage",
    "/article/manage",
    "/publish/article",
)


# ── 图片上传（头条支持插入图片到正文） ─────────────────────

# 图片上传 input[type=file]
IMAGE_FILE_INPUT: tuple[str, ...] = (
    "input[type='file'][accept*='image']",
    "input[type='file'][multiple]",
    ".upload-image input[type='file']",
)


__all__ = [
    "PROFILE_URL_FALLBACK",
    "LOGIN_INDICATORS",
    "PUBLISH_URL_FALLBACK",
    "TITLE_SELECTORS",
    "BODY_SELECTORS",
    "COVER_MODE_RADIO",
    "SUBMIT_BUTTON",
    "SUCCESS_URL_PATTERN",
    "IMAGE_FILE_INPUT",
]