# 持仓雷达 · Stock Radar

> 个人持仓 RSI 卖出风险监控系统。**手工选股和买入，自动监测已持有股票，出现规则信号时提醒，不自动交易。**

文档版本：2026-11-20 / V0.1 项目初始化包。**本包是详细设计与项目脚手架，不是已经上线的股票监控产品。**

## 先做什么

1. **先读** [`docs/00_项目导览.md`](docs/00_项目导览.md)，然后是 [`docs/01_需求分析_PRD.md`](docs/01_需求分析_PRD.md) 与 [`docs/02_概要设计_HLD.md`](docs/02_概要设计_HLD.md)。
2. **先探测数据**：在 Codex 中运行 [`prompts/01_Codex_数据接口测试.md`](prompts/01_Codex_数据接口测试.md)，不要先写完整前端。项目里已经提供可以独立运行的 `scripts/market_probe.py`。
3. 获取同花顺金融数据服务 API Key：<https://fuyao.aicubes.cn/admin/>；复制 `.env.example` 为 `.env`，只在本机/云端填入实际 Key。**不要把密钥放进 GitHub。**
4. 待 A/B 股接口真实返回通过验证后，再向 Codex 提交 [`prompts/02_Codex_完整系统实现.md`](prompts/02_Codex_完整系统实现.md)。
5. 上线前使用 [`docs/05_测试与验收.md`](docs/05_测试与验收.md) 和 [`prompts/03_Codex_验收检查.md`](prompts/03_Codex_验收检查.md)。

## 当前具备的内容

- 详细 PRD、系统概要架构、RSI 规则、数据接口调研、测试计划、部署运维方案、需求决策记录；
- 3 份可直接发给 Codex 的 Markdown Prompt；
- **可执行的同花顺 REST / AKShare B 股接口测试脚本**，支持离线演示，无需依赖同花顺凭据即可先验证测试流程；
- 独立 RSI 指标及高位死叉规则的参考 Python 实现和单元测试；
- 后端/前端目录、GitHub CI 和环境变量模板；
- `.gitignore` 排除了密钥、运行数据、数据库、日志与检测报告。

**尚未具备**：可供用户打开的正式网页、已授权的真实行情、长期运行的后台和微信自动推送。Codex 按分阶段 Prompt 完成后才具备这些功能。

## 一分钟本地验证

运行环境：Python 3.10+。

```bash
python scripts/market_probe.py --provider demo
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

## 总体架构

```text
手机 PWA（Vue 3 / TS / Vite / Vant / ECharts）
       │ HTTPS · 登录鉴权
       ▼
FastAPI 服务端 ── SQLite（持仓、K线、信号、通知记录）
       │
       ├── A 股 → 同花顺金融数据 REST API（**需要实测账号权限**）
       ├── B 股 → AKShare（**需要实测稳定性**）
       ├── RSI6 / RSI12 / RSI24 + 风险规则
       └── APScheduler 定时检查 → Server酱 → 微信通知

前端：GitHub Pages（或可替代静态站点）
后端：常驻 Python 服务（不能托管到 GitHub Pages）
```

## 关键边界

- 「RSI 超买」不是「RSI 超卖」。本系统监控 **RSI6 上破 80、高位死叉**；双重顶与顶背离作为后续进阶规则。
- RSI 信号是**风险预警**，不等于确定卖出或自动交易。
- 微信消息不保证每条实时到达；供应商 API、行情更新和网络故障必须有可见状态。
- 项目为私人辅助工具。未设计任何公开荐股、代客理财或交易下单功能。

## 参考文档

- 同花顺快速开始：<https://fuyao.aicubes.cn/docs/quickstart/>
- 同花顺 A 股行情 API：<https://fuyao.aicubes.cn/docs/api-reference/prices/>
- AKShare B 股接口：<https://akshare.akfamily.xyz/data/stock/stock.html>（镜像亦可参考 `<https://akshare-hh.readthedocs.io/en/latest/data/stock/stock.html>`）
- Server酱：<https://sct.ftqq.com/docs/getting-started/faq/>
