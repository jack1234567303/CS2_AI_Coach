# CS2 AI Coach

基于 CS2 Demo 文件的 AI 辅助分析系统。上传完美平台比赛 Demo ZIP,系统自动解析、重建比赛事件、分析玩家行为并生成个人训练建议。

当前版本:**阶段二 —— 事件理解 + 战术行为分析**(已完成)

> 📋 **交接/维护文档见 [HANDOVER.md](./HANDOVER.md)**(实现细节、已知限制、待办清单、踩坑记录)

> 不做:视频理解、POV 录像分析、视觉模型。
> Demo 解析完全复用开源项目 [awpy](https://github.com/pnxenopoulos/awpy),本项目不重复造轮子。

## 功能

| 模块 | 内容 |
|---|---|
| 基础能力 | K/D、ADR、HS%、KAST、Rating(近似)、突破首杀成功率、补枪率 |
| 死亡原因 | 逐次死亡分类:残局孤军 / 被白死亡 / 无保护 peek / 补枪迟到 / 孤立单摸 / 正常交换… |
| Peek 分析 | 交火聚类、是否先手、队友支援距离、协同闪光、血量对比、重复 peek |
| 道具分析 | 烟雾时长与失效、闪光白杀转化、燃烧瓶/手雷伤害 |
| 站位分析 | 死亡/击杀/活动热力图(0-1 归一化)、区域死亡热点、被击杀方向、CT 前压倾向 |
| 团队配合 | 补枪窗口(5s)检测、漏补场景、被补率 |
| AI Coach | 结构化问题 → LLM(OpenAI / Claude)生成 Markdown 报告;无 Key 时输出规则模板报告 |

所有分析结论强制附带数据依据(回合 / Tick / 事件描述),AI 不凭空评价玩家。

## 目录结构

```
CS2_AI_Coach/
├── backend/
│   ├── main.py               # FastAPI 入口
│   ├── config.py             # 配置(路径、LLM Key)
│   ├── common/models.py      # 规范化数据模型(分析层唯一依赖)
│   ├── parser/awpy_adapter.py# awpy 适配器(ZIP解压/解析/字段收敛)
│   ├── sample_data.py        # 示例比赛生成器(开发演示/测试)
│   ├── analyzer/
│   │   ├── common.py         # MatchContext:帧查询/队友判定/距离
│   │   ├── stats_analysis.py     # Module 1 基础能力
│   │   ├── death_analysis.py     # Module 2 死亡原因
│   │   ├── peek_analysis.py      # Module 3 Peek
│   │   ├── utility_analysis.py   # Module 4 道具
│   │   ├── position_analysis.py  # Module 5 站位热力图
│   │   ├── teamwork_analysis.py  # Trade/Entry
│   │   └── engine.py             # 分析编排
│   ├── database/db.py        # SQLite 索引 + JSON 文件存储
│   ├── ai/coach.py           # LLM 报告 + 规则模板
│   └── tests/                # pytest
├── frontend/                 # React + Vite + Tailwind
├── data/                     # 运行时数据(db / 比赛JSON / 上传)
└── requirements.txt
```

## 快速开始

### 后端(Python 3.10+)

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

API 文档:http://127.0.0.1:8000/docs

无需 Demo 即可体验:首页点"生成示例比赛",或:

```bash
curl -X POST http://127.0.0.1:8000/api/matches/sample
```

### 前端(Node 18+)

```bash
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173(已代理 /api 到 8000)
```

或构建后由后端直接托管:

```bash
npm run build      # 产物 frontend/dist,后端自动挂载到 /
```

> Windows 无 Node 时可安装:`winget install --id OpenJS.NodeJS.LTS --scope user`(新开终端生效)。

### 雷达底图(可选)

站位热力图的地图底图按顺序尝试:后端 `/api/maps/{map}.png`(读取 awpy 已下载的地图数据,离线可用)→ awpy GitHub 仓库 → 纯网格背景。联网环境下运行一次以下命令即可获得底图与精确坐标标定:

```bash
python -c "from awpy.data.utils import create_data_dir_if_not_exists; create_data_dir_if_not_exists()"
```

### 解析真实 Demo

首页上传完美平台 ZIP(或裸 .dem)。带帧解析约 1-5 分钟,期间状态为 `parsing`,列表页自动轮询刷新。

### 运行测试

```bash
python -m pytest backend/tests -q
```

## AI Coach 配置(可选)

LLM 只负责解释分析结果,绝不参与 Demo 解析;输入只包含 Feature + Analysis Result(不含原始 Tick 数据)。

```bash
export OPENAI_API_KEY=sk-...          # 或 ANTHROPIC_API_KEY=...
# 可选:
export CS2COACH_LLM_PROVIDER=auto     # auto | openai | anthropic | none
export CS2COACH_OPENAI_MODEL=gpt-4o-mini
export OPENAI_BASE_URL=https://api.openai.com/v1
```

未配置 Key 时自动退回确定性规则模板,报告同样带回合级引用。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/matches/upload` | 上传 ZIP/DEM,后台解析 |
| POST | `/api/matches/sample` | 生成示例比赛 |
| GET | `/api/matches` | 比赛列表(含解析状态) |
| GET | `/api/matches/{id}` | 比赛详情 + 记分板 |
| GET | `/api/matches/{id}/analysis/{steamid}` | 完整行为分析(带缓存) |
| POST | `/api/matches/{id}/coach/{steamid}` | 生成 Markdown 教练报告 |

## 分析口径说明

- 支援距离:800 CS2 单位(≈20m)内视为可即时支援
- 补枪窗口:队友倒地后 5 秒
- 协同闪光:交火前 3 秒内、落点 1200 单位内的队友闪光
- 闪光"白敌时间"在 demo 无直接事件时,使用 `victim_blind` 击杀 + 落地 3 秒内协同击杀作为代理指标(输出中明确标注)
- Rating 为 HLTV 2.0 社区线性近似(平均玩家≈1.0),仅供横向参考
- 队友判定基于"多回合同侧共现"聚类(CS2 中场换边)

## 路线图

- Phase 1 ✅ Demo 上传 → awpy 解析 → 规范化 JSON
- Phase 2 ✅ 行为分析:死亡原因 / Peek / Trade / 道具 / 站位
- Phase 3 ✅(基础)AI Coach 报告(LLM + 模板兜底)
- 未来(不在当前范围):多模态(事件截图 + 视觉模型)、PostgreSQL、多 Demo 趋势分析
