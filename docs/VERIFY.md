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

自主程度契约（RFC §5.7，不需要真实平台密钥）：`tests/test_autonomy_policy.py`、`tests/webui/test_autonomy_api.py`。

GitHub Actions（`.github/workflows/verify.yml`）调用同一条 `scripts/verify.sh`，避免本地与远端两套逻辑。
