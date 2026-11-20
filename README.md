# 澄石材料共变研究册

独立材料试件按生产序号分段，用孔隙率线性拟合吸水率，定位生产中途的变化；相关不解释为因果。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
澄石研究协作组，12名分析师与业务人员，日常使用 `stone-segment-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、输入冻结与分段计算；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：MaterialBatch / Specimen / CorrelationRun / ResearchNote。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 批次与独立试件录入/编辑/删除，每条试件填**唯一生产序号**；重名、重复序号与非法数值明确拒绝。
- 发起研究时按生产序号排序，冻结全部测值与**切分罚分**，写入 SHA256。输入修改不会改写历史结果。
- **分段**：每段连续 20 至 200 件，用孔隙率对吸水率做最小二乘线性拟合；某段孔隙率恒定时该段只拟合吸水率均值。
- **目标**：残差平方和(RSS) + 每次切分同一罚分，取最小。用动态规划 O(n·181) 求解，**十万件不枚举分段方案**。
- **必切位置**：由前向/后向最优代价判定——某位置只有在没有任何最优段跨越它时才标记；这是所有最优方案共同要求的切分，与展示的代表解无关。
- **中断续算**：前向、后向与必切校验按块写入检查点文件；重启后从已确认前缀继续，不重算。计算中改测值只影响下一次研究。
- **完整性**：分段与必切位置齐全（`result.complete`）才显示、复核和导出；不完整结果明确拒绝。
- 负责人复核不可覆盖；审计记录登录、数据改动、排队、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18243 ，API http://localhost:19243/docs 。数据库映射 17743。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

## 一天的工作剧本
1. 09:00 admin登录，查“澄石·细砂岩批次”（900 件，生产中途孔隙率—吸水率关系发生变化）。
2. 09:10 analyst核对 `stone-segment-202611-1.csv` 的真实生产序号，补齐缺失项。
3. 09:15 来源录入页检查 `孔隙率 (%)`、`吸水率 (%)` 与生产序号，必要时编辑。
4. 09:20 分析执行页保留默认罚分 25，命名本轮研究。
5. 09:21 冻结生产序号、测值与罚分并排队，worker实算；中断则从检查点续算。
6. 09:25 阅读目标值、代表分段与**必切位置**；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2；动态规划分段，O(n·181)|
|数据库|MySQL8|
|任务|Redis7持久队列，Python独立worker，检查点续算，心跳healthcheck|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；分段算法在 `backend/src/services/segmentation.py`，独立校验在 `backend/src/services/algorithm.py`；`backend/scripts/seed_demo.py` 自然键插入；`backend/migrations/README.md` 说明 schema v2。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 算法输入、失败态与复现
输入为选定批次全部观测（含生产序号）和切分罚分，输出核心数值、代表分段、必切位置与解释；参数经后端严格白名单和范围验证（罚分 0 至 1e9）。缺生产序号、重复序号、不足 20 件、非有限数值一律拒绝；无法按 20–200 件完成分段时任务 failed 并保留错误。参与计算的输入快照与罚分写入 SHA256，相同输入可复现。

独立oracle `algorithm.py` 用 O(n²) 记忆化动态规划独立求解最优目标值并交集全部最优方案的切分位置，用于核对 `segmentation.py`。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务并从检查点续算，数据库与Redis间未采用分布式事务。实际验证结果见 `TESTING.md`（`cd backend && python -m tests.run_all`）。

MIT License。
