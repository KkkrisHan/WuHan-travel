# 武汉数命 SPU 城市 AI 接待员

面向武汉旅行者的本地优先 AI 城市接待员，也是 SAIG 数命 SPU 国际 AI 大赛线上赛作品。用户可以了解武汉、SPU 社区与 SAIG 大赛，探索武汉地点，并让 AI 协助生成和调整旅行计划。

## 功能

- **城市介绍**：展示武汉、数命 SPU 和 SAIG 大赛相关内容；AI 接待员支持普通问答。
- **武汉地图**：使用高德地图 JS API，地图视图裁剪至武汉市完整行政边界。搜索、热点和点击选点会按本地武汉边界规则过滤；武汉以外的坐标不能加入武汉旅行。
- **旅行规划**：设置出行日期、同行人、兴趣、住宿和必去地点，生成候选行程并进行优化；支持地图路线、站点编辑、撤销与恢复。
- **景点信息**：AI 可以介绍地点；可用的来源信息会展示引用。营业时间、价格、预约和实时信息在没有可靠来源时会标记为尚未核实。
- **本地优先保存**：旅行和聊天主要保存在当前浏览器；后端 SQLite 保存地图标记、任务和景点知识数据。

## 技术栈

- 前端：React、TypeScript、Vite
- 后端：Python、FastAPI、SQLite、LangGraph
- AI：DeepSeek 或 OpenAI 官方 API
- 地图：高德地图 JavaScript API 2.0 与高德 Web 服务

## 本地启动

需要 Node.js 22 LTS 和 Python 3.11 或更新版本。以下命令适用于 Windows PowerShell；前后端分别在两个终端启动。

**后端**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

**前端**

```powershell
cd frontend
npm ci
if (!(Test-Path .env)) { Copy-Item .env.example .env }
npm run dev
```

打开 `http://localhost:5173`。后端默认仅监听 `127.0.0.1:8000`。已有 `.env` 时启动命令不会覆盖它。

## 首次连接地图和 AI 服务

Dashboard 的介绍内容无需 API Key。地图与 AI 分开配置，只有第一次使用相关功能时才会提示：

1. 首次进入地图时，填写 **高德 JS API Key** 和与它配套的 **JS API 安全密钥**。
2. 只有在使用后端地点搜索、住宿搜索、路线规划或地理编码时，才需要 **高德 Web Service Key**。只打开地图时可以先不填写。
3. 首次发送 AI 消息、生成景点介绍或使用 AI 行程功能时，选择 DeepSeek 或 OpenAI，填写 API Key，并确认模型 ID。
4. 服务验证并保存后，应用会自动继续刚才的操作。之后可从 Dashboard 的「服务连接」修改或移除配置。

三种高德凭据用途不同，不能互换。JS Key 会发送到浏览器以加载地图，无法完全保密；建议在高德控制台设置域名白名单。JS 安全密钥和 Web Service Key 留在本机后端。AI 配置只连接所选供应商的官方 API 地址，不接受任意 Base URL。

界面保存的配置会优先于环境变量和 `.env`。Windows 将配置保存到当前用户注册表，并写入 `HKCU\Environment`；其他系统保存到用户配置目录的 `WuhanTravel/services.json`。移除服务会立即停用应用中的凭据，但不会删除现有环境变量。环境变量和本地配置都不是加密保险库，无法防御以同一用户权限运行的恶意程序。

配置优先级为：界面保存配置 → 当前进程环境变量 → 后端 `.env` → 应用默认值。为兼容旧安装，后端也会读取 `frontend/.env` 中旧的 `VITE_AMAP_JS_KEY` 和 `VITE_AMAP_SECURITY_CODE`；DeepSeek Key 还兼容 `wuhantravel` 备用变量。界面配置不会写入浏览器 `localStorage`、Git 或应用日志。

`.env.example` 仅包含变量名和空值。一般不需要手动填写 Key；如需在启动前配置，使用 `backend/.env`。前端 `.env` 只用于设置 `VITE_API_URL` 等非秘密前端参数。

## 武汉行政边界

本地边界数据位于 `frontend/src/data/wuhanBoundary.json`：

- 来源：OpenStreetMap 武汉市 relation 3076268，采集日期 2026-11-19。
- 行政区划代码：420100；坐标系：GCJ-02。
- 结构：MultiPolygon，保留多个轮廓和内环；原始 WGS84 顶点已转换为 GCJ-02。
- 许可与署名：ODbL 1.0，© OpenStreetMap contributors。

应用初始化时加载一次边界，地点判断在浏览器本地完成，不会为每次点击或搜索调用行政区查询 API。地图同时使用高德区域 Mask、视野缩放限制和武汉外部纯色遮罩。行政区边界数据本身仍受来源数据精度限制。

## 本地数据和隐私

- 旅行、行程、聊天和部分界面状态保存在当前浏览器的 IndexedDB 或浏览器状态中；清除浏览器数据前请先导出或备份需要保留的内容。
- 后端 SQLite 保存个人地图标记、短期任务状态和景点知识数据。默认数据库位置与 `TASK_DATABASE` 配置有关。
- AI 请求会发送到用户选择的供应商；高德搜索、路线或地理编码请求会发送到高德服务。
- 配置 Key 不存入 Git 或浏览器 `localStorage`。Windows 用户级环境变量可被同一 Windows 用户权限下运行的程序读取。

## 开发验证

```powershell
cd frontend
npm run build
npm run lint
npm run test:geo
```

```powershell
cd backend
python -m unittest discover -s tests -v
```

## 项目边界

- 地图热点依赖高德底图的 POI 覆盖；并非所有文字、道路或建筑都可点击。
- 行程结果用于旅行参考，不承诺全局最优。高德路线服务不可用时，交通信息可能标记为未知或使用示意路线。
- 官方公告、营业状态、门票与预约信息只在有可靠来源时展示；其他情况会标记为待核实。
- 本项目默认供本机开发使用。若部署到其他设备，应单独配置可信网络边界、HTTPS、持久化存储和访问控制；不要直接将开发服务器暴露到公网。
