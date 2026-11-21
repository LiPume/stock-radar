# 持仓雷达手机端

Vue 3、TypeScript、Vant 4 和 ECharts 提供持仓总览、编辑、收盘检测、日 K 与 RSI 详情、信号记录和设置。股票需要登录后自行录入。首次打开和重新加载页面都需要管理员密码。

## 本地运行

在 `frontend/` 使用 Node 22.12 以上版本。后端按仓库根 README 启动，监听 `127.0.0.1:8765`。

```sh
npm ci
npm run dev
```

打开 [本地开发页](http://127.0.0.1:5173/stock-radar/)。开发服务器将 `/api` 转发到本机 FastAPI。使用以下命令验证和生成发布包。

```sh
npm run lint
npm run typecheck
npm run build
```

构建产物位于 `frontend/dist/`。完成构建后启动或重启 FastAPI，即可从[本地持仓雷达](http://127.0.0.1:8765/stock-radar/) 使用同源版本。测试生产模式的前端也可运行 `npm run preview`，默认端口 4173。

## 手机与 GitHub Pages

正式入口为 [Stock Radar 手机应用](https://lipume.github.io/stock-radar/)。本站 main 默认连接香港后端。迁往其他 HTTPS 后端时，设置仓库 Actions 变量 `VITE_API_BASE_URL`（包含 `/api/v1`）并重新构建。后端 CORS 白名单登记 `https://lipume.github.io`。

浏览器支持安装时，Android 可通过浏览器菜单安装，iOS Safari 可选择“分享 → 添加到主屏幕”。离线时可打开已缓存的网页外壳；查询持仓、修改设置和检测均需联网。Service Worker 只预缓存构建文件；访问令牌只存在当前页面内存中，API 请求均指定 no-store。

手机启动或回到前台会检查版本，也可以打开 **设置 → 应用版本 → 检查更新**。发现新版可选择「稍后」继续当前操作。选择「立即更新」会重新载入应用，需要重新登录。使用早期没有检查按钮的安装版时，连网打开，让浏览器下载更新，再关闭应用窗口并重新打开。

## 发布和线上验收

仓库 Settings → Pages → Source 选择 **GitHub Actions**。`.github/workflows/ci.yml` 是唯一发布流程。拉取请求运行测试。推送到 main 后，后端测试和前端构建分别执行，通过后将 `frontend/dist` 发布至原网址。不要另设从分支或 docs 目录构建的 Pages 来源。

发布任务从原网址读取版本标记，核对本次 SHA、Vue 入口及资源，测试浏览器跨域访问香港后端的健康检查与匿名拒绝，再用合成持仓测试手机图表。旧版迁移测试在 Chromium 注册旧版本的 Service Worker，将同一来源切换到本次部署包，并比对公网与部署包的字节。生产口令与私人截图只用于本机的独立真实持仓验收。公开版本记录位于 [version.json](https://lipume.github.io/stock-radar/version.json)。

## 使用与验收

1. 登录后添加六位代码，核实自动填入的交易所、A/B 股，可选填名称和备注。
2. 点击“立即检测”，在同一页面查看运行状态、最近任务、异常数和行情日期。
3. 打开持仓详情查看前复权日 K、5/10/20 日收盘均线、RSI6/12/24 和 20/80 参考线。默认近 20 个交易日，可切换近 5、10、20、30、60、120 日。上下两张图共用区间，点击曲线名称可单独显示或隐藏。均线先按完整历史计算，再截取所选日期。
4. 编辑持仓可变更备注或停用监控，删除前会要求确认。设置页可以调整阈值并查看收盘自动扫描和微信通知配置状态。
5. 核对无网络、过期会话、缺少 SendKey 和行情失败时的提示。真机安装、终端 RSI 对照及微信到达需在目标设备上独立验收。

### 本机验证记录

2026-11-20 在 Chrome 的 360 × 800 手机视口使用独立 SQLite 库完成登录、添加、编辑、变更阈值、手动检测、信号列表、删除和退出。缺少行情凭据时页面显示“数据异常”，信号记录为空。离线重新打开后可见登录界面，浏览器缓存检查未发现 API 请求。已通过 ESLint、vue-tsc 和 Vite 生产构建。本机截图保存在 `reports/pwa-mobile-login.png` 与 `reports/pwa-mobile-overview.png`。实体手机安装仍待实机确认。

图表复核使用第一阶段本地 CSV 导入隔离库，以真实的 600519.SH 前复权日线绘制 K 线和 RSI6/12/24，验证了 120 日切换和 360px 宽度无横向溢出。截图见 `reports/pwa-mobile-chart.png`。
