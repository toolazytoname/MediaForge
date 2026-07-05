---
name: m4-0-decision-audit
description: MediaForge M4-0 发布通道决策复核——5 项目 GitHub API 复核结论
metadata:
  type: project
---

MediaForge M4-0（发布通道决策复核）结论（commit 8537b76，2026-07-06）：

## 5 项目复核快照（GitHub API）

| 项目 | stars | 最后 push | 状态 |
|------|-------|-----------|------|
| TrendPublish (liyown/ai-trend-publish) | 3037 | 2026-06-14 | 活跃，bug fix + 文档 |
| XiaohongshuSkills (white0dew) | 3132 | **2026-05-21** | 0 commit since vendor pin 锚点 |
| AiToEarn (yikart) | 23133 | 2026-07-03 | 极活跃 |
| Pixelle-Video (ATH-MaaS) | 24157 | 2026-06-14 | 稳定 |
| baoyu-skills (JimLiu) | 23131 | 2026-07-04 | 极活跃（image-gen v2.1.0 稳定） |

## 复核结论

**全部 5 项 M0-0 DECISION 维持 CONFIRMED**：
- TrendPublish → 参考（不变）
- XiaohongshuSkills → 采用（不变）
- AiToEarn → 放弃整体（不变）
- Pixelle-Video → 采用第二引擎（不变）
- baoyu-skills → §5.5 桥 + image-gen 子进程（不变）

## 风险监控要点

1. **XiaohongshuSkills 作者活跃度下降**：M0-0 记录"最快次日关 issue"，现"拖 1-3 个月批量关"。M4-3 mac 冒烟先行——若选择器失效，走 HARD_PARTS §7 Plan B（自写 Playwright + social-auto-upload 新版 uploader）。

2. **Pixelle-Video 任务状态存内存**：服务重启丢任务（已知限制，M5-3 接入时轮询 404 → failed + 重新 submit）。

3. **baoyu-image-gen v2.1.0 字节级稳定**：M2-4.5 集成时仍按惯例复核当时 HEAD CLI 签名（v2.1.0 → 可能升 v2.x，需重测 `--json` 出口）。

## 复核方法（复用模式）

```python
import urllib.request, json
url = f'https://api.github.com/repos/{full}'
req = urllib.request.Request(url, headers={'User-Agent': 'M4-0-audit'})
data = json.loads(urllib.request.urlopen(req, timeout=10).read())
# 看: stargazers_count, pushed_at, archived
```

也可用 GitHub API `commits?since=...` + `issues?state=all&per_page=N` 看近期动态。

**Why**: M4-0 是"决策不过期"的防火墙——开源项目变化快，复核间隔 > 1 个月就应重检。
**How to apply**: M5/M6 前再做一次同模式复核（5 项目 + baoyu 任何升级）。每个项目至少看 stars / last push / archived 三字段。