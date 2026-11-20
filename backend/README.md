# 后端运行与验证

从仓库根目录启动，使用 Python 3.11 或更高版本。安装依赖、配置管理员和启动命令见根目录 README。服务入口为 `backend.app.main:app`，支持单进程运行。ASGI 生命周期启动时执行 Alembic 升级，并创建默认 RSI 阈值 80 / 70 和关闭微信通知的设置。数据库默认位于 `data/stock_radar.db`，启用 WAL、外键检查及 15 秒写入等待。

| 后端环境变量 | 用途 |
|---|---|
| `ADMIN_PASSWORD_HASH` | 管理员 Argon2id 哈希 |
| `ADMIN_PASSWORD_HASH_FILE` | 未设置哈希环境变量时读取的文件，默认 `data/admin.hash` |
| `DATABASE_URL` | 以 `sqlite:///` 开头的库路径 |
| `CORS_ORIGINS` | 以逗号分隔的前端 Origin，本地默认 localhost/127.0.0.1 的 5173 和 4173 端口，Pages 使用 `https://lipume.github.io` |
| `HITHINK_FINANCE_API_KEY` | 同花顺历史日 K 接口凭据 |
| `SERVERCHAN_SENDKEY` | Server酱凭据。配置后还需在设置页开启推送 |
| `SCHEDULER_ENABLED` | 默认 true；本地离线验收设为 false |

## API

接口前缀为 `/api/v1`，非敏感健康检查和登录可匿名访问。持仓、缓存、检测记录、设置和系统状态都要求 Bearer 令牌。访问令牌有效 1 小时，服务重启后重新登录。单个来源 5 分钟内最多允许 5 次失败登录。接口以 `data`、`error`、`meta.request_id` 和 `meta.as_of` 返回结果，私有响应设置 `Cache-Control: no-store`。

- `GET /health`、`POST /auth/login`（JSON 的 `password` 字段）、`POST /auth/logout`。
- `GET /holdings`、`POST /holdings`（`symbol, market, display_name?, notes?`），`PATCH /holdings/{id}` 和 `DELETE /holdings/{id}`。停用保留列表项，删除隐藏列表项；重新登记同代码可沿用事件历史。
- `GET /stocks/{symbol}/bars?days=120` 返回按日期升序排列的 OHLCV 和 RSI6/12/24。查询上限为 500 根。
- `GET /signals?symbol=600519.SH&limit=50` 返回信号及依据；`POST /checks` 返回 HTTP 202 和 `run_id`；`GET /checks/{id}` 查询执行进度和结果。
- `GET /settings` 和 `PUT /settings` 使用 `overbought, high_level, notifications_enabled`；`GET /system/status` 返回最近一轮运行、最近一批通知及凭据配置状态。

根目录运行 `.venv/bin/python -m unittest discover -s tests -p test_backend_api.py -v` 验证登录与限速、精确 CORS、持仓修改与恢复、跨实例持久化、日线排序、陈旧状态和默认配置。测试为每个用例建立独立临时 SQLite 库并运行真实迁移。测试数据与本地持仓隔离。

完成前端构建后启动后端，访问 `http://127.0.0.1:8765/stock-radar/`。在线访问前端、后端分域时应在后端精确添加前端 origin，并在前端配置后端 HTTPS 地址。

`.venv/bin/python -m unittest discover -s tests -p test_mvp_flow.py -v` 通过 HTTP 驱动真实后台线程、SQLite 和规则计算。它使用离线行情响应和本地通知回执，依次验证登录、保存设置、新增持仓、首次事件与合并通知、30 秒内限速、同日重复检测、行情返回 429 时保留图表及历史记录。此用例同时比对已迁移 SQLite 的结构和 ORM 元数据。
