# Codex Prompt 01｜先验证行情 API，制作可复现的数据接口测试

> **使用方法**：把整份文件内容发给 Codex，工作目录选仓库根目录。**这是第一阶段，不要开始完整网页开发。**

你是 Python 数据工程师。当前项目是 `stock-radar`，用户最终要做的只是：把自己已买入的 A 股/B 股登记后，每日检测 RSI 卖出风险并微信提醒。请先**严格验证行情数据接口是否可用**，再考虑整个产品。

## 必读上下文

先读取并遵循：

- `README.md`
- `docs/00_项目导览.md`
- `docs/01_需求分析_PRD.md`
- `docs/03_RSI策略与计算口径.md`
- `docs/04_数据接口调研与验证.md`
- `docs/05_测试与验收.md`
- `AGENTS.md`

重点：同花顺金融数据 REST API (`https://fuyao.aicubes.cn`) 与 SuperMind 云研究环境不是同一接入方式。A 股用官方批量历史 K 线 REST，B 股用 AKShare `stock_zh_b_daily`。

## 已提供的代码

仓库已有 `scripts/market_probe.py`、`src/stock_radar/indicators/rsi.py`、`src/stock_radar/rules/rsi.py`、`tests/`。**先阅读并运行现有代码，修复问题、扩充测试，不要抛弃已有脚手架重写。**

## 第一步：先跑离线验证（不调用网络）

执行：

```bash
python scripts/market_probe.py --provider demo
python scripts/demo_rsi.py
python -m unittest discover -s tests -v
```

如果未通过，直接修复，解释改动。demo 数据必须以 `DEMO` 标识，不可声称真实行情。

## 第二步：确认官方 API 契约

官方资料（必须逐页核对参数、数据响应字段和错误语义）：

1. <https://fuyao.aicubes.cn/docs/quickstart/>
2. <https://fuyao.aicubes.cn/docs/api-reference/prices/>
3. <https://fuyao.aicubes.cn/docs/mcp/tools/get_a_share_prices_historical_batch/>
4. <https://akshare-hh.readthedocs.io/en/latest/data/stock/stock.html>

A 股具体是：

- `GET https://fuyao.aicubes.cn/api/a-share/prices/historical-batch`
- `X-api-key` 请求头来自 `HITHINK_FINANCE_API_KEY` 环境变量
- `thscodes=600519.SH,688525.SH`，`interval=1d`，`start`/`end` 是 Unix 毫秒，`adjust=forward`
- 返回形态：`{code,message,data:{interval,adjust,item:[{thscode,timestamp,item:[{date_ms,open_price,high_price,low_price,close_price,volume,turnover}]}]}}`
- 每请求最多 10 只 A 股；**即使外层 `code=0`，也必须逐一校验返回分组是否存在及是否有数据**。

B 股具体是：

- `ak.stock_zh_b_daily(symbol='sh900901', start_date='YYYYMMDD', end_date='YYYYMMDD', adjust='qfq')`
- 测试 `900901.SH`，不要求用户当前持有这个股票。
- B 股数据来自第三方，调用失败要保留可复现错误，不进行频繁抓取。

## 第三步：真实 A 股探测（如有 Key）

环境变量 `HITHINK_FINANCE_API_KEY` 只从环境读取，不要问我把 Key 贴在聊天里，不要写入 `.py/.md/.json`。如果 `.env` 不存在，也不要生成假 Key。可提示用户在本地创建 `.env` 并自行填写，或通过安全环境变量运行。

运行：

```bash
python scripts/market_probe.py --provider hithink --a 600519.SH 688525.SH --days 240 --export-csv
```

验证：HTTP/API 层状态、实际数据日期、是否返回全部请求股票、OHLC 大小逻辑、每日时间顺序、是否有 ≥100 根有效 K 线、样本是否来自当天或上个交易日；超时/401/403/429 不能静默忽略。

**如没有 Key 或无法联网，不要宣称实测成功。** 离线测试可以完成，但报告明确写 `NOT_RUN_NO_CREDENTIALS` / `NETWORK_UNAVAILABLE`，留出后续补测命令。

## 第四步：真实 B 股探测

```bash
python -m pip install -e '.[probe]'
python scripts/market_probe.py --provider akshare-b --b 900901.SH --days 240 --export-csv
```

记录最新交易日、条数、空值、频率/复权参数、异常与运行时长。再次运行要注意第三方速率；不要批量疯狂调用。必要时对比不复权和前复权，仅作数据分析。

## 第五步：指标正确性核对

将抓到的收盘价导入参考 RSI 计算模块，输出 RSI6/12/24 最新几日，并写入报告。不得直接把测试用虚构价格展示为真实数据。

要求我后续可以用同花顺终端截图检查：

- 数据 `bar_date`、复权类型、数据源、运行时间写全；
- 同花顺指标可能采用不同的 SMA 初始化方式，程序差异须记录，不能伪装完全一致；
- 比较最近 3 个完整收盘日，记录手工核对表格。

## 交付格式（须生成具体文件）

1. 修复或增强 `scripts/market_probe.py` 和单元测试；
2. 输出 `reports/probe_result.json`（仅本地；已被 Git 忽略）；
3. 新增 `reports/接口实测报告.md`，明确 `PASS/FAIL/NOT_RUN`（未做的不要写 PASS）；
4. 如果实际接口和 `docs/04_数据接口调研与验证.md` 不一致，修正文档并附新官方参考链接；
5. 给出明确 `GO/CONDITIONAL GO/NO-GO` 和下一阶段前必须完成的事项。

## 限制

- **只做接口验证与数据质量，不写完整前端、不部署、不注册额外收费账户、不购买 API**。
- 不抓取登录后的网页、不开启未经授权的爬虫、不绕过反爬限制。
- 绝不提交 API Key、SendKey、真实持仓数据库或生成的行情 CSV 到 Git。
- 不要进行真实下单，不要发送实际“卖出”通知给用户。
- 发生错误时先解释问题并给出下一步，不能把 mock 返回当成取到真实股票数据。
- 实施过程中可修复确定性问题，不要反复要求我确认常规技术细节。

开始执行，并最终汇报：**哪些源真实通了、哪些没通、数据日期和样本数、限制/费用是否仍待确认、下一步能否开发。**
