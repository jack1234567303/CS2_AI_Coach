"""FastAPI 入口:上传 Demo -> awpy 解析 -> 分析 -> AI 报告。

启动:uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.ai.coach import generate_report
from backend.analyzer.common import MatchContext
from backend.analyzer.engine import run_full_analysis
from backend.analyzer.stats_analysis import analyze_stats
from backend.common.models import MatchData
from backend.database import db
from backend.parser.awpy_adapter import ParseError, is_zip, parse_zip_file
from backend.sample_data import generate_sample_match

app = FastAPI(title="CS2 AI Coach", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 开发阶段;上线收紧
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_match(match_id: str) -> MatchData:
    data = db.load_match(match_id)
    if data is None:
        meta = db.get_match_meta(match_id)
        if meta and meta["status"] == "parsing":
            raise HTTPException(409, "该比赛仍在解析中(带帧解析可能需要几分钟),请稍后刷新")
        if meta and meta["status"] == "error":
            raise HTTPException(422, f"解析失败:{meta['error']}")
        raise HTTPException(404, "比赛不存在")
    return data


def _analysis_or_compute(match: MatchData, steamid: str) -> dict:
    cached = db.load_analysis(match.match_id, steamid)
    if cached is not None:
        return cached
    if not any(p.steamid == steamid for p in match.players):
        raise HTTPException(404, f"玩家 {steamid} 不在本场比赛中")
    analysis = run_full_analysis(match, steamid)
    obj = analysis.model_dump()
    db.save_analysis(match.match_id, steamid, json.dumps(obj, ensure_ascii=False))
    return obj


# ---------------- API ----------------

@app.get("/api/health")
def health():
    return {"ok": True, "llm_provider": config.LLM_PROVIDER,
            "openai_configured": bool(config.OPENAI_API_KEY),
            "anthropic_configured": bool(config.ANTHROPIC_API_KEY)}


@app.post("/api/matches/sample")
def create_sample_match():
    """生成示例比赛(开发/演示用,不依赖 awpy)。"""
    match_id = db.new_match_id()
    match = generate_sample_match(match_id)
    db.save_match({"created_at": time.time()}, match)
    return {"match_id": match_id, "status": "ready", "map": match.map_name,
            "score": match.final_score}


@app.post("/api/matches/upload")
async def upload_demo(background: BackgroundTasks, file: UploadFile = File(...),
                      parse_frames: bool = True):
    """上传完美平台比赛 Demo ZIP,后台解析。"""
    raw = await file.read()
    if not is_zip(raw):
        # 直接是 .dem 也接受
        if raw[:4] not in (b"PBDE", b"PBMS"):
            raise HTTPException(400, "请上传包含 .dem 的 ZIP(完美平台导出包)或 .dem 文件")
    match_id = db.new_match_id()
    suffix = ".zip" if is_zip(raw) else ".dem"
    up_dir = config.UPLOADS_DIR / match_id
    up_dir.mkdir(parents=True, exist_ok=True)
    up_path = up_dir / ("demo" + suffix)
    up_path.write_bytes(raw)

    # 占位行,后台任务完成后更新
    db.create_pending(match_id, "awpy")

    background.add_task(_parse_task, match_id, up_path, parse_frames)
    return {"match_id": match_id, "status": "parsing",
            "note": "解析在后台进行,带帧解析约需 1-5 分钟,请轮询 GET /api/matches"}


def _parse_task(match_id: str, zip_path: Path, parse_frames: bool):
    try:
        match = parse_zip_file(zip_path, config.UPLOADS_DIR, match_id,
                               parse_frames=parse_frames)
        db.save_match({"created_at": time.time()}, match)
    except ParseError as e:
        db.set_status(match_id, "error", str(e))
    except Exception as e:  # noqa: BLE001
        db.set_status(match_id, "error", f"未知解析错误: {e}")
    finally:
        # 清理上传的压缩包(dem 保留在 match 目录由 parse_zip_file 管理)
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/api/matches")
def list_matches():
    return db.list_matches()


@app.get("/api/matches/{match_id}")
def match_detail(match_id: str):
    match = _require_match(match_id)
    ctx = MatchContext(match)
    stats = analyze_stats(match, ctx)
    return {
        "match_id": match.match_id, "map": match.map_name, "source": match.source,
        "tick_rate": match.tick_rate, "duration_seconds": match.duration_seconds,
        "rounds": len(match.rounds), "score": match.final_score,
        "teams": stats.teams, "players": [p.model_dump() for p in stats.players],
    }


@app.get("/api/matches/{match_id}/analysis/{steamid}")
def get_analysis(match_id: str, steamid: str):
    match = _require_match(match_id)
    return _analysis_or_compute(match, steamid)


@app.post("/api/matches/{match_id}/coach/{steamid}")
def coach_report(match_id: str, steamid: str, use_llm: bool = True):
    match = _require_match(match_id)
    analysis = _analysis_or_compute(match, steamid)
    # 重建 FullAnalysis 对象以复用强类型字段
    from backend.analyzer.engine import FullAnalysis
    fa = FullAnalysis.model_validate(analysis)
    result = generate_report(match, fa, use_llm=use_llm)
    return result


# ---------------- 前端静态托管(构建后生效) ----------------
@app.get("/api/maps/{map_name}")
def map_image(map_name: str):
    """地图雷达图:优先使用 awpy 已下载的地图数据(离线可用)。"""
    map_name = map_name.removesuffix(".png")
    try:
        from awpy.data import MAPS_DIR
        for name in {map_name, map_name.removeprefix("de_")}:
            cand = MAPS_DIR / f"{name}.png"
            if cand.exists():
                return FileResponse(cand, media_type="image/png")
    except Exception:
        pass
    raise HTTPException(404, "地图图片不可用(可运行 awpy 下载地图数据,或前端使用网络源)")


_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(_dist / "index.html")
