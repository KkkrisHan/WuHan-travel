# 漫游武汉 · Wuhan AI Concierge

面向武汉自由行的本地优先 Web App。旅行计划、行程收藏、聊天和行程保存在浏览器 IndexedDB；个人地图标记按旅行保存到后端 SQLite，FastAPI 也保存短期任务、SSE 事件和候选结果。前端使用 React、TypeScript、Vite 和高德地图 JS API；后端使用 FastAPI、SQLite 与 LangGraph。

## 本地启动

需要 Node.js、Python 3.11 或更新版本。以下命令在 PowerShell 中分别于两个终端运行。

**后端**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

**前端**

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

打开 `http://localhost:5173`。后端健康检查在 `http://localhost:8000/api/health`。

## 接入真实服务

在 `backend/.env` 设置：

| 变量 | 用途 |
|---|---|
| `AMAP_WEB_SERVICE_KEY` | POI 全类别搜索、步行/公交/驾车路线和导出静态地图。仅服务端可见。 |
| `AMAP_JS_SECURITY_CODE` | 高德 JS API 安全密钥。后端通过 `/_AMapService` 代理转发，避免前端明文暴露。 |
| `OPENAI_API_KEY` | OpenAI 兼容模型服务；用于对话、地点介绍和要求提取。 |
| `OPENAI_BASE_URL`、`OPENAI_MODEL` | 模型地址和型号。 |
| `FRONTEND_ORIGIN` | 允许访问 API 的前端来源，多个来源用逗号分隔。 |
| `TASK_DATABASE` | 临时任务 SQLite 文件路径。 |

在 `frontend/.env` 设置 `VITE_AMAP_JS_KEY` 为高德 Web 端 JS API Key，并将 `VITE_AMAP_SERVICE_HOST` 指向后端 `http://localhost:8000/_AMapService`。正式环境应使用 HTTPS、域名白名单和同域或可信代理。`VITE_AMAP_SECURITY_CODE` 仅供本地临时调试；它会进入浏览器代码，正式环境请留空并使用服务端代理。两类高德 Key 不可混用。

没有高德 Key 时，页面明确显示“地点示意视图”，只可搜索少量内置武汉地点；交通耗时标记为待核实，地图导出不可用。没有模型 Key 时，助手显示“基础模式”，使用保守的模板回答。**这些降级结果不代表实时地图或已核实的官方信息。**

## 已实现的流程

- Dashboard：继续旅行、搜索、重命名、复制和删除计划。
- 四步向导和探索模式；计划数据刷新后仍在本机。
- 地点搜索、地图定位、地点卡、收藏、待安排地点、行程 Marker 联动、分栏拖动和全屏。
- 地图热点和任意坐标选点：优先查询热点 POI 详情；普通点击搜索 500 米内 POI，按距离筛选 150 米内的候选；无合适 POI 时逆地理编码并创建坐标地点。个人地图标记可添加、取消并在刷新后恢复。
- 对话与独立地点介绍任务；多任务并行、SSE 进度、取消、失败重试。动态事实无可靠来源时显示“未知 / 尚未核实”。
- LangGraph 聊天与规划工作流；从旅行要求提取地点，生成两套候选行程，提供地图预览、差异提示、采用前 revision 校验和规划锁。
- 手动增删/调整顺序、简单自然语言移除地点、优化预览、撤销/重做和历史版本恢复。
- 按日期导出 Markdown；通过浏览器打印或保存 PDF。配置高德 Web 服务 Key 后附静态地图与来源标识。

## 当前边界

- 个人地图标记使用 `GET /api/places`、`POST /api/places`、`DELETE /api/places/{id}`，请求需带旅行的 `X-Trip-ID` 和 `X-Trip-Secret`。首次写入绑定旅行密钥；重复 POI ID 或重复六位小数坐标只保存一条。删除旅行时同步清理这些标记。行程内原有“收藏”仍由 IndexedDB 管理，两者用途不同。
- 地图热点取决于高德底图可点击 POI 的覆盖范围；部分文字、道路和建筑没有热点。周边搜索与逆地理编码使用现有 JS API Key 和安全代理配置；没有有效高德配置时仍只有示意视图。

- 官方公告、营业状态、门票和预约信息未接入可靠的官方来源，始终显示待核实。不会把高德 POI 当作官方公告。
- 固定时间活动会被识别并警告，但尚不能自动排入时间轴；采用含未满足硬性安排的方案时需要确认。这类方案应视为草稿。
- 候选方案是可解释的路线组合，不保证全局最优；若高德路线不可用，交通耗时保持未知。
- PDF 通过浏览器打印对话框保存；Markdown 中的地图使用内嵌图片，部分 Markdown 阅读器可能限制 data URI 图片。
- 正式计划只在当前浏览器；Markdown/PDF 不等于可恢复的结构化备份。后端任务按单进程运行，生产部署请使用一个 Uvicorn worker，并配置 HTTPS、持久化磁盘和外部限流。

## 验证

```powershell
cd frontend
npm run build
npm run lint
```

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

API 测试覆盖地点搜索、任务令牌、SSE、规划、固定活动警告、简单修改提案、优化任务和删除鉴权。浏览器端已手动验证创建、选点、规划、采用、刷新、介绍、简单 AI 修改、撤销和窄屏布局；真实高德和模型接口仍需使用你的 Key 在目标环境验收。

## 文档

产品与技术基线见 `docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/DECISIONS.md`、`docs/DEVELOPMENT.md`。

## 景点知识库

后端复用 `TASK_DATABASE` 指向的 SQLite，新增 `poi_places`、`poi_sources`、`poi_facts`、`poi_sessions` 四张表。开放规则可按旅行日期、星期、跨年季节和指定日期查询；特殊闭园优先于常规规则，日场与夜场分开存储。过期或冲突规则不作为已确认开放时段。所有字段须保留来源 URL、核实时间和状态。

在 `backend` 目录运行：

```powershell
python -m app.poi_knowledge.refresh --all
python -m app.poi_knowledge.refresh --place "黄鹤楼"
python -m app.poi_knowledge.refresh --all --expired-only
python -m app.poi_knowledge.refresh --failures
```

采集器先用高德确定 POI，再复用 DeepSeek Search/Fetch 保存可追溯的事实；名称重复且无法唯一定位时报告失败。`official_seed.py` 记录了人工核对过的黄鹤楼日场和湖北省博物馆公告。运行全量命令会载入这些有明确来源的规则；已固定核实日期，后续不会仅因重新运行而自动延长有效期。没有 2026-11-22 夜上黄鹤楼可靠场次证据，因此知识库不会把夜游作为已开放时段。

行程生成先查本地库，仅对仍缺资料的必去地点做限时联网查询，并把逐日开放情况交给唯一一次 AI 规划。规划后只计算与校验时间、路线；冲突会给出原因。暂未自动创建 Windows 计划任务，可按上面的命令自行配置任务计划程序。
