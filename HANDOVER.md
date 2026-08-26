# CS2 AI Coach 交接文档

> 面向后续维护/开发的 Agent 或工程师。撰写日期:2026-08-24。项目版本 v0.1(方案 Phase 2 完成 + Phase 3 基础版)。
> 总体方案见 `README.md`(用户视角)与最初开发方案(用户需求文档)。本文档是**实现细节与维护视角**的单一权威来源。

---

## 1. 项目现状一览

| 阶段 | 状态 | 说明 |
|---|---|---|
| Phase 1:Demo 上传 → awpy 解析 → JSON | ✅ 完成 | 支持 awpy 1.x/2.x 双 API;完美平台 ZIP |
| Phase 2:行为分析(死亡/Peek/Trade/道具/站位) | ✅ 完成 | 六个分析模块,全部带回合/tick 级引用 |
| Phase 3:AI Coach | ✅ 基础版 | LLM(OpenAI/Anthropic)撰写报告,无 Key 时规则模板兜底 |
| 多模态(截图+视觉模型) | ❌ 未开始 | 方案明确"不要现在实现" |
| PostgreSQL / 鉴权 / 多用户 | ❌ 未开始 | 当前 SQLite + 单用户假设 |

**运行环境**:Windows 11 + Python 3.13 + Git Bash;Node.js 24(经 `winget --scope user` 安装在
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_*/node-v24.19.0-win-x64`,新终端可直接用 node/npm)。
**本机网络受限**(GitHub 等不可直连)——影响:awpy 地图标定数据未能下载、雷达底图走降级。

常用命令(项目根目录):

```bash
pip install -r requirements.txt          # 依赖:fastapi uvicorn pydantic httpx awpy(2.0.2, 含 polars/demoparser2 0.42.0)
python -m pytest backend/tests -q        # 测试(20 项,应全绿)
python -m uvicorn backend.main:app --port 8000    # 后端(同时托管 frontend/dist)
cd frontend && npm install && npm run build       # 前端构建(dist 由后端自动挂载)
# 开发模式:cd frontend && npm run dev(5173,已代理 /api → 8000)
```

无 Demo 时的演示入口:`POST /api/matches/sample`(确定性生成 24 回合 mirage 比赛,内置脚本化场景,测试依赖它做精确断言)。

---

## 2. 目录与模块职责

```
backend/
├── main.py                    FastAPI 入口(上传/解析/分析/报告/地图图片 端点)
├── config.py                  路径与 LLM 配置(环境变量:OPENAI_API_KEY / ANTHROPIC_API_KEY /
│                              CS2COACH_LLM_PROVIDER=auto|openai|anthropic|none / *_BASE_URL / *_MODEL)
├── common/models.py           ★ 全部规范化数据模型(MatchData/KillEvent/DamageEvent/GrenadeEvent/
│                              Frame/PlayerFrame/RoundData/Problem/Evidence/RecoveredPosition)
├── parser/awpy_adapter.py     ★ awpy 适配器(v1+v2 双支持、ZIP 解压、空坐标三层恢复)
├── sample_data.py             示例比赛生成器(固定 seed,确定性;测试的"金数据")
├── analyzer/
│   ├── common.py              ★ MatchContext:回合索引、帧查询、队伍聚类、阵营推断
│   ├── stats_analysis.py      Module1 基础统计(K/D ADR KAST HS% Rating近似 Entry Trade)
│   ├── death_analysis.py      Module2 死亡原因(8 类规则分类)
│   ├── peek_analysis.py       Module3 Peek(交火聚类/先手/支援/协同闪/重复peek)
│   ├── utility_analysis.py    Module4 道具(烟时长/闪转化代理/火雷伤害)
│   ├── position_analysis.py   Module5 站位(热力点/区域统计/方向/CT前压/覆盖度)
│   ├── teamwork_analysis.py   Trade/漏补检测
│   └── engine.py              编排 → FullAnalysis(problems 按严重度排序 + data_notes)
├── database/db.py             SQLite 元数据 + JSON 文件存储
├── ai/coach.py                LLM 报告 + 确定性模板报告;SYSTEM_PROMPT 禁止编造
└── tests/                     pytest(适配器字段映射 + 示例场景断言 + 空坐标恢复)
frontend/                      React18+Vite5+Tailwind3(HashRouter;src/pages + src/components)
data/                          运行时:coach.db / matches/{id}/match.json+analysis/ / uploads/{id}/*.dem
```

★ = 改动时最需要谨慎的文件。

**数据流**:ZIP → `extract_dem_from_zip` → awpy `Demo.parse()` → `convert_awpy2`(规范化)→ `MatchData`
存 `data/matches/{id}/match.json` → 按需 `run_full_analysis(match, steamid)` → 缓存 `analysis/{steamid}.json`
→ 前端渲染;`POST /api/matches/{id}/coach/{steamid}` 生成报告(输入只含 Feature+分析结果,不含原始 Tick)。

### API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 含 LLM 配置状态 |
| POST | `/api/matches/upload` | multipart ZIP/DEM;后台解析(BackgroundTasks),状态 parsing→ready/error |
| POST | `/api/matches/sample` | 生成示例比赛 |
| GET | `/api/matches` | 列表(前端 5s 轮询) |
| GET | `/api/matches/{id}` | 详情+记分板(analyze_stats 即时计算) |
| GET | `/api/matches/{id}/analysis/{steamid}` | 完整分析(带缓存) |
| POST | `/api/matches/{id}/coach/{steamid}` | `{report, llm_used}` |
| GET | `/api/maps/{map}.png` | 雷达底图(读 awpy 已下载数据,离线可用;没有则 404→前端降级) |

---

## 3. 已实现的关键机制(按重要程度)

### 3.1 awpy 2.x 适配(`parser/awpy_adapter.py`)

awpy 2.x 与 1.x API 完全不同(2.x:`awpy.demo.Demo` + polars DataFrame 属性;1.x:`DemoParser.parse()` dict)。
`parse_demo_file` 自动探测:先试 v2 导入,失败走 v1。**解析层完全复用 awpy,不重写解析**。

**空坐标三层恢复**(踩坑最深的部位,改动务必跑 `test_awpy2_adapter.py`):

1. demoparser2 对**死亡玩家**返回 null X/Y/Z/place → `_fill_ticks_positions`:按 (steamid, round_num)
   polars `forward_fill`(尸体留在死亡点);
2. 事件里为 null 的坐标 → `_fill_event_positions`:与同回合、同玩家最近非空 tick 位置做
   `join_asof(backward, by=[steamid, round_num])` 回填;
   ⚠️ 历史教训:join 时 steamid 列被改名 `_sid`,**join 后必须 rename 回原名**,否则全部击杀无法归属玩家(已修,有回归断言);
3. **完美平台 Demo 的疑难杂症**:个别玩家(一场 4/10)整场无任何坐标,连 is_alive/health/team_name 都空,
   换 demoparser2 版本无效 → Demo 本身损坏(事件侧正常)。恢复手段:**道具投掷原点**
   (grenades df 同 entity_id 的首条轨迹行;实测与投掷者真实位置偏差中位数 28 单位)。
   产出 `MatchData.recovered_positions`,仅用于热力图 presence 展示。

**其他适配细节**:
- grenades df 在部分版本是**逐 tick 轨迹流**(百万行)→ 按 entity_id 去重留最早一行(该逻辑在 `convert_awpy2` 的 deduped_throws 段);
- `attacker_place/victim_place`(demoparser2 直接叫 place)、`attackerblind` 等字段名均有大小写/命名兼容(`_g()` 助手);
- tick_rate 由 header 的 playback_ticks/playback_time 估算,兜底 64;
- 帧采样 ~4 帧/秒(`tick_rate//4`),控制 match.json 体积(整场约 10-15MB);
- 玩家 team_name / 比分:以"第 1 回合同阵营"定义 A/B 队,按每回合该队实际阵营计分(处理换边)。

### 3.2 分析层共享基础(`analyzer/common.py` MatchContext)

- **队伍聚类**:union-find,按"每回合同阵营"配对。阵营来源 = 帧 + 击杀/伤害事件,并对缺失者做
  **对手阵营反推**(杀我的人是 CT ⇒ 我这回合是 T)。⚠️ 反推公式是 `5 - side`(2↔3),曾错写成 `3 - side`。
- 帧查询 `frame_at(round, tick)`:bisect 最近帧;`alive_teammates` / `nearest_teammate_distance` 供多模块复用。
- **可调参数**(都在 `analyzer/common.py` 顶部,常量):
  `SUPPORT_DISTANCE=800`(支援距离)、`TRADE_WINDOW_S=5`、`FLASH_SUPPORT_WINDOW_S=3` +
  `FLASH_SUPPORT_RADIUS=1200`、`ENGAGEMENT_GAP_S=3`(交火聚类间隔)、`AGGRESSIVE_TIME_S=20`(CT 前压判定)。
  改动后需删除 `data/matches/*/analysis` 重新计算,并同步更新测试期望。

### 3.3 六个分析模块要点

- **stats**:KAST 按回合算(kill/assist/survive/traded);Rating 是 HLTV2.0 社区线性近似
  (系数已按"平均玩家=1.0"校准,**不要**改回 0.0082 的 ADR 系数);无帧数据的玩家回合数
  回退用队友回合数(否则 ADR 会虚高成千)。
- **death**:优先级分类链 clutch→blind→traded→**position_unknown**→peek→late_trade→isolated→protected。
  `position_unknown` 专给"Demo 无坐标玩家",不生成 Problem,不算玩家问题。
- **peek**:伤害事件按 3s 间隔聚成 engagement;无保护 peek = 先手 ∧ 无协同闪 ∧ 最近队友>800。
  目标位置缺失时跳过闪光半径与无保护判定(`pos_missing` 守卫)。
- **utility**:闪光效果是**代理指标**(victim_blind 击杀 + 落地 3s 内协同击杀),输出中已如实标注;
  烟雾用实体生命周期(平均时长/提前失效)。
- **position**:点位带 `side`(前端 T/CT 切换);归一化优先 awpy 标定(需联网下载数据),
  否则 minmax;`coverage ∈ full/recovered/none` + 中文提示,前端概览页和热力图页都会展示。
- **teamwork**:漏补 = 队友在 1200 内倒地、目标存活、5s 内无人补。

### 3.4 AI Coach(`ai/coach.py`)

- 输入 `build_coach_input()`:只含统计/问题/evidence(回合+tick+描述),**绝不发送原始 tick 数据**(方案红线);
- LLM:OpenAI 兼容接口或 Anthropic,httpx 直调(无额外依赖),失败/无 Key → `_template_report`
  确定性模板(四段式:优势/最大问题/数据依据/训练建议),每条建议引用回合号;
- LLM 返回内容**未在真实 Key 下实测过**(环境无 Key)——上线前建议先用 gpt-4o-mini 验证 prompt 效果。

### 3.5 前端

- `Heatmap.jsx`:canvas 径向渐变热力;底图三级降级 `/api/maps/{map}.png` → awpy GitHub raw → 网格;
  图层开关(活动/击杀/死亡)+ 阵营切换(全部/T/CT,exact 匹配,注意"T 方"是"CT 方"的子串,
  测试点击要用 `{ exact: true }`)。
- `MatchPage`:比赛间路由切换不 remount,`steamid` 状态残留问题已通过在 effect 里清 error 修复——
  **改动 useEffect 依赖时注意保持 setError('') 的清理逻辑**。
- 比赛列表 5s 轮询(解析状态),自动化测试点击列表项时可能因重渲染失败,用 URL 直达更稳。

---

## 4. 测试体系

```bash
python -m pytest backend/tests -q        # 20 项
```

- `test_analyzers.py`:基于**确定性示例比赛**精确断言(每个死亡分类的回合号、问题类型、闪光利用率等)。
  示例数据(`sample_data.py`)的任何改动都会影响这批断言——这是故意的(金数据模式),改场景要同步改测试。
  场景清单见 `sample_data.py` 文件头注释(R2/R8 单摸、R3/R6/R10/R14/R19 无保护peek 等)。
- `test_awpy2_adapter.py`:合成 awpy 2.x 形状数据(polars DataFrame + list 两种)验证字段映射、
  轨迹去重、空坐标回填、steamid 保留。
- **没有真实 Demo 的自动化测试**(唯一一份真实 dust2 Demo 在 `data/uploads/20260824-000348-dffaf7/`,
  含 4 名无坐标玩家,是绝佳的回归样本,别删)。

---

## 5. 已知限制与待完成清单

### 高优先(功能性)
1. **player_blind 事件未接入**:适配器已在 `parse_with_awpy2` 里请求 `player_blind` 事件(demo.events 里有),
   但 `convert_awpy2` **尚未消费**。接入后可用真实"白敌秒数"替换 utility 的代理指标
   (按 entityid 关联闪光爆裂 → thrower,对敌方求和)。这是闪光分析精度的最大改进点。
2. **LLM 报告未实测**:配置 Key 后验证 prompt/输出格式;建议在 coach.py 加单元测试 mock httpx。
3. **awpy 地图标定数据未下载**(本机断网):联网后跑
   `python -c "from awpy.data.utils import create_data_dir_if_not_exists; create_data_dir_if_not_exists()"`
   → `~/.awpy/maps/map-data.json` + 底图 png,热力图自动切换为精确标定(`normalization: awpy`)。
4. **多 dem 的 ZIP**:完美平台若一场导出多个 .dem(如上下半场),当前只取文件名最短的一个;
   应扩展为逐个解析合并或提示用户。
5. `parse_frames` 参数在 v2 路径无效(v2 总是解析 ticks)——要么实现(跳过 ticks 解析提速),
   要么从 API 参数里删掉,避免误导。

### 中优先(工程质量)
6. 上传解析用 FastAPI BackgroundTasks(单进程内存任务):重启丢任务、无进度条(前端只能轮询状态)。
   可换 独立线程池 + 进度表,或任务队列。
7. 解析耗内存(1.2M 行 tick 转 dict):`convert_awpy2` 的 `_rows` 全量物化,大 Demo(加时赛)可能吃 2GB+;
   可改流式或只在 polars 侧做采样后再物化。
8. PostgreSQL 迁移(方案要求):db.py 已隔离 SQL,换 JSONB 列即可;`init_db()` 模块导入即执行,注意测试隔离。
9. 前端:报告页 marked 渲染未做 XSS 清洗(内容来自自家 LLM/模板,风险低,但接入用户自定义输入前必须加 DOMPurify);
   无路由懒加载;无错误边界(ErrorBoundary)。
10. 队伍名是"A 队/B 队"(v2 路径):真实 Demo 若含队名(记分板事件)可替换。

### 低优先(方案明确留到以后)
11. 多 Demo 趋势分析(玩家多场对比)。
12. 多模态:事件截图 + 视觉模型(方案第八节)。
13. 示例数据仅 mirage;可扩展其他地图(区域表在 `sample_data.py` `_RAW_AREAS`,含到真实雷达的仿射变换)。
14. `sample_data.py` 里 `_weapon_for` 是死代码,可删。

---

## 6. 维护者容易踩的坑(血泪清单)

1. **改 MatchData 模型字段** ⇒ 已存盘的 `match.json` 不兼容 ⇒ 老比赛全部加载失败。
   处理:给新字段默认值(向后兼容),或写迁移脚本重解析(`parse_demo_file` 幂等,dem 仍在 uploads/)。
2. **分析结果有缓存**:改分析逻辑后必须 `rm -rf data/matches/*/analysis`,否则前端看到旧结果。
3. **join_asof/polars 操作后检查列是否还在**(steamid 丢失 bug 的教训);给 `test_awpy2_adapter.py` 加断言。
4. **demoparser2 版本敏感**:锁定 0.42.0(requirements 未显式锁,建议加 `demoparser2==0.42.0`)。
   升级前用真实 dust2 Demo 回归(重点看:4 个无坐标玩家的 recovered_positions、kills steamid 完整性)。
5. **示例数据确定性**:`_jit` 用 crc32(不要换回 `hash()`,Python 字符串 hash 跨进程随机);
   走位路线(`apply_routes`/`truncate_routes`)与场景脚本有耦合——脚本 move 之所以生效是因为
   "列表顺序后写入者优先",truncate 只是防御;动场景时先跑测试看分类断言。
6. **阵营反推是 `5 - side`** 不是 `3 - side`。
7. Windows 下杀 uvicorn:后台任务杀 shell 不杀子进程,`netstat -ano | grep :8000` 找 PID 再 `taskkill //PID x //F`。
8. 本机 Git Bash 输出中文可能乱码(仅显示问题,JSON 数据正常),验证以 API 返回结构为准。
9. 前端改动后要 `npm run build` 才会被后端托管(8000 端口);dev 模式走 5173。
10. `backend/config.py` 在**导入时**就创建 data 目录;测试环境想隔离用 `CS2COACH_DATA_DIR` 环境变量。

---

## 7. 现存数据(这台机器上)

- `data/coach.db`:2 场比赛元数据——`20260824-000348-dffaf7`(用户上传的真实完美平台 dust2,22 回合,
  4 名无坐标玩家)+ 1 场 sample;
- `data/uploads/20260824-000348-dffaf7/9210462212476496268_0.dem`:原始 Demo(**保留**,回归用);
- 无 LLM Key、无 `.env`(LLM 相关全部走环境变量)。

## 8. 建议的下一步(按序)

1. 联网下载 awpy 地图数据(第 5.3 条)→ 热力图有底图;
2. 接入 player_blind(第 5.1 条)→ 闪光分析精确化;
3. 配 LLM Key 实测报告质量(第 5.2 条);
4. 再要一份**正常**的完美平台 Demo 验证全量玩家坐标正常;
5. 然后才是新功能(趋势分析/PostgreSQL/多模态)。
