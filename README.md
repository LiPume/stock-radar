# 持仓雷达 · Stock Radar

登记自己持有的 A/B 股，按前复权收盘日线计算 RSI6、RSI12、RSI24。RSI6 首次突破 80 产生关注信号，RSI6 在高位下穿 RSI12 产生重点预警。新事件才进入微信合并投递，行情失败会单独标记。

[GitHub Pages 手机前端](https://lipume.github.io/stock-radar/) · [源码](https://github.com/LiPume/stock-radar) · [本机应用](http://127.0.0.1:8765/stock-radar/)

V1.1 在每个交易日 14:30 生成逐股中文简报，15:45 按正式日线检测。行情时间或复权无法核实时，简报标记数据异常，并列出最近有效收盘的日期与指标。手机端可查看详情、简报历史、微信回执和行情用量，卖出后可在个股详情页删除持仓。盘中行情通过交易时段验证后可以开启每 30 分钟检测。[V1.1 运行与维护](docs/V1.1运行与维护.md)

手机端的个股日 K 叠加 5 日、10 日、20 日均线，可结合下方 RSI 查看。行情数值统一显示两位小数。

## 在本机使用

需要 Python 3.11+、uv 和 Node 22.12+。在仓库根目录安装锁定依赖，构建 PWA。

```sh
uv sync --locked --python 3.12 --extra server --extra probe
npm --prefix frontend ci
npm --prefix frontend run build
```

首次使用时，将 `.env.example` 复制到 `.env` 并填写所需后端变量。已有配置可直接沿用。运行密码设置程序，在本机终端输入自己的密码；Mac 也可双击 `scripts/设置本机登录密码.command`。只有 Argon2id 哈希写入 `data/admin.hash`。

```sh
.venv/bin/python scripts/admin_password.py
.venv/bin/python -m uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8765 --workers 1
```

打开本机应用，登录后添加持仓、确认市场并点击“立即检测”。首次启动为空列表。收盘后 15:45 自动检测需要后端持续运行；陈旧行情于 16:15、16:45 有限重试。微信通道需要后端配置 `SERVERCHAN_SENDKEY` 并在设置中启用。数据日期、上次检测、单股错误和消息回执可在页面查看。双重顶、顶背离保留扩展接口。

## 部署与验证

正式发布由推送到 `main` 触发。仓库 Settings → Pages → Source 选择 GitHub Actions。唯一工作流先运行后端测试，再将通过前端检查的 Vue 构建包发布到原网址，并用浏览器验收版本、香港 API、图表和旧版 PWA 的升级。用户可在设置底部对照本机版本、线上版本及后端版本。[手机端与发布说明](frontend/README.md)

正式主页默认连接香港 HTTPS API。变更后端时，在仓库 Actions 变量 `VITE_API_BASE_URL` 设置完整 API 根地址（结尾为 `/api/v1`）并重新构建。部署、CORS、备份和恢复步骤见[运维指南](docs/06_部署安全运维.md)，微信投递行为见[调度与通知](docs/调度与通知.md)。

```sh
.venv/bin/python -m unittest discover -s tests -v
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

两次真实数据验证均对三只公开样本各取得 162 根前复权日线，最新收盘日为 2026-11-20。后台联调使用独立临时库，最近三个交易日的全部 27 个 RSI 与第一阶段一致。终端截图逐数对齐、真实微信收件和目标手机安装仍待验收。[阶段验收记录](docs/08_交付检查记录.md)

## 一分钟本地验证

运行于项目虚拟环境。

```bash
python scripts/market_probe.py --provider demo --output reports/demo.json
python scripts/demo_rsi.py
python -m unittest discover -s tests -v
```

实测 A 股：

```bash
export HITHINK_FINANCE_API_KEY='你的Key'  # 仅当前终端；请勿提交到Git
python scripts/market_probe.py --provider hithink --a 600519.SH 688525.SH --days 240
```

实测 B 股（安装可选依赖后运行）：

```bash
python -m pip install -e '.[probe]'
python scripts/market_probe.py --provider akshare-b --b 900901.SH --days 240
```

也可用 `--provider all` 同时检测两类。默认报告写入 `reports/probe_result.json`（已被 Git 忽略）。


[香港 ECS 部署与回退](docs/香港部署与回退.md) · [需求、设计与项目导览](docs/00_项目导览.md)
