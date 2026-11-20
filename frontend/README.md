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

部署路径是 [LiPume 的 stock-radar Pages](https://lipume.github.io/stock-radar/)。在仓库 Actions 变量中设置公开的 `VITE_API_BASE_URL`，值为你控制的 HTTPS 后端完整 API 根地址，包含 `/api/v1`。后端 CORS 白名单登记 `https://lipume.github.io`，变量修改后重新构建网页。未配置时，Pages 显示连接说明并禁用密码输入。手机远程访问需后端有可达的 HTTPS 地址。

浏览器支持安装时，Android 可通过浏览器菜单安装，iOS Safari 可选择“分享 → 添加到主屏幕”。离线时可打开已缓存的网页外壳；查询持仓、修改设置和检测均需联网。Service Worker 只预缓存构建文件；访问令牌只存在当前页面内存中，API 请求均指定 no-store。

## 使用与验收

1. 登录后添加六位代码，核实自动填入的交易所、A/B 股，可选填名称和备注。
2. 点击“立即检测”，在同一页面查看运行状态、最近任务、异常数和行情日期。
3. 打开持仓详情查看前复权日 K、RSI6/12/24、20/80 参考线以及该股信号历史。可切换 30、60、120 个收盘日；数据不足时图表仅绘制已有记录。
4. 编辑持仓可变更备注或停用监控，删除前会要求确认。设置页可以调整阈值并查看收盘自动扫描和微信通知配置状态。
5. 核对无网络、过期会话、缺少 SendKey 和行情失败时的提示。真机安装、终端 RSI 对照及微信到达需在目标设备上独立验收。

### 本机验证记录

2026-11-20 在 Chrome 的 360 × 800 手机视口使用独立 SQLite 库完成登录、添加、编辑、变更阈值、手动检测、信号列表、删除和退出。缺少行情凭据时页面显示“数据异常”，信号记录为空。离线重新打开后可见登录界面，浏览器缓存检查未发现 API 请求。已通过 ESLint、vue-tsc 和 Vite 生产构建。本机截图保存在 `reports/pwa-mobile-login.png` 与 `reports/pwa-mobile-overview.png`。实体手机安装仍待实机确认。

图表复核使用第一阶段本地 CSV 导入隔离库，以真实的 600519.SH 前复权日线绘制 K 线和 RSI6/12/24，验证了 120 日切换和 360px 宽度无横向溢出。截图见 `reports/pwa-mobile-chart.png`。
