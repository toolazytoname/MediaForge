## 本地质量门

一条命令覆盖后端测试、前端安装/类型检查/构建和秘密扫描。不需要真实平台密钥，也不会做真实发布。

### 支持的工具链

| 工具 | 版本 | 安装 |
| --- | --- | --- |
| Python | 3.11+（CI 锁 3.12，见 `.python-version`） | `python3 -m venv .venv` |
| Node | 20.19+（CI 锁 22，见 `frontend/.nvmrc`） | 官方安装包 / nvm |
| npm | 10+（随 Node） | `npm ci` 安装前端依赖 |

Python 依赖：`pip install -r requirements.txt`  
前端依赖：`frontend/package-lock.json` + `npm ci`

### 全新 clone 后

```bash
git clone https://github.com/toolazytoname/MediaForge.git
cd MediaForge
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
bash scripts/verify.sh
```

失败时看最后一级 `======== verify: <stage> ========`：

- `tooling` — Python/Node 版本不够
- `backend-deps` — `pip install -r requirements.txt` 失败
- `backend-tests` — pytest 失败，看 traceback
- `frontend-install` — `npm ci` 失败
- `frontend-typecheck-build` — `vue-tsc` 或 `vite build` 失败
- `secret-scan` — 仓库里出现高置信度密钥形态

图文安全交付契约（不需要真实平台密钥）：`tests/test_capability_registry.py`、`tests/test_deliverables.py`、`tests/test_delivery_kernel.py`、`tests/test_adapter_capabilities.py`、`tests/test_dryrun_invariance.py`。

组图交付契约（不需要真实平台密钥）：`tests/test_gallery_deliverable.py`。覆盖 3 个夹具审批→导出、未批准 409、非法 `vas_` 拒绝、无回执不记平台成功、小红书 unknown 失败、Project 无 direct。

持久媒体任务契约（不需要真实密钥/GPU）：`tests/test_durable_jobs.py`、`tests/test_video_bridge.py`。覆盖重启后同一 job 可 poll/fetch、同一 idempotency_key 不重复资产/计费、未知成本为 NULL、取消/超时后不再写成功。

自主程度契约（RFC §5.7，不需要真实平台密钥）：`tests/test_autonomy_policy.py`、`tests/webui/test_autonomy_api.py`。

自动化到审批与复盘回流（LAZY-88，不需要真实平台密钥）：`tests/test_automation.py`、`tests/test_budget_policy.py`、`tests/test_delivery_metrics.py`、`tests/test_insights.py`、`tests/webui/test_s6_api.py`。覆盖定时停在 `awaiting_approval`、超预算/未知成本可恢复暂停、夹具指标回流、建议未确认不改品牌规则。

GitHub Actions（`.github/workflows/verify.yml`）调用同一条 `scripts/verify.sh`，避免本地与远端两套逻辑。
