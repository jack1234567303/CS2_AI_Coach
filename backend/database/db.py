"""SQLite 元数据索引 + 比赛数据文件存储。

设计:
- DB 只存轻量元数据(状态、比分、玩家列表),便于列表查询
- 完整 MatchData / 分析结果以 JSON 文件存于 data/matches/{id}/
  (正式环境换 PostgreSQL 时,把文件换成 JSONB 列即可,接口不变)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Optional

from backend.common.models import MatchData
from backend.config import DB_PATH, MATCHES_DIR

_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id     TEXT PRIMARY KEY,
    created_at   REAL NOT NULL,
    status       TEXT NOT NULL,           -- parsing / ready / error
    source       TEXT NOT NULL,           -- awpy | sample
    map_name     TEXT DEFAULT '',
    score        TEXT DEFAULT '',         -- 展示用 "13-11"
    team_names   TEXT DEFAULT '[]',
    players      TEXT DEFAULT '[]',       -- [{steamid, name, team}]
    rounds       INTEGER DEFAULT 0,
    error        TEXT DEFAULT '',
    data_path    TEXT DEFAULT ''
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def new_match_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def match_dir(match_id: str) -> Path:
    d = MATCHES_DIR / match_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_match(meta: dict, match: MatchData) -> None:
    """写入元数据行 + MatchData JSON。"""
    data_path = match_dir(match.match_id) / "match.json"
    data_path.write_text(match.model_dump_json(), encoding="utf-8")
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO matches "
            "(match_id, created_at, status, source, map_name, score, team_names, players, rounds, error, data_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (match.match_id, meta.get("created_at", time.time()), "ready", match.source,
             match.map_name,
             " - ".join(str(v) for v in match.final_score.values()),
             json.dumps(match.team_names, ensure_ascii=False),
             json.dumps([p.model_dump() for p in match.players], ensure_ascii=False),
             len(match.rounds), "", str(data_path)),
        )


def set_status(match_id: str, status: str, error: str = "") -> None:
    with _conn() as c:
        c.execute("UPDATE matches SET status=?, error=? WHERE match_id=?", (status, error, match_id))


def create_pending(match_id: str, source: str) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO matches (match_id, created_at, status, source, map_name) "
                  "VALUES (?,?,?,?,?)", (match_id, time.time(), "parsing", source, ""))


def list_matches() -> List[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT match_id, created_at, status, source, map_name, score, team_names, players, rounds, error "
            "FROM matches ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        out.append({
            "match_id": r["match_id"], "created_at": r["created_at"], "status": r["status"],
            "source": r["source"], "map_name": r["map_name"], "score": r["score"],
            "team_names": json.loads(r["team_names"] or "[]"),
            "players": json.loads(r["players"] or "[]"),
            "rounds": r["rounds"], "error": r["error"],
        })
    return out


def get_match_meta(match_id: str) -> Optional[dict]:
    for m in list_matches():
        if m["match_id"] == match_id:
            return m
    return None


def load_match(match_id: str) -> Optional[MatchData]:
    meta = get_match_meta(match_id)
    if not meta:
        return None
    p = MATCHES_DIR / match_id / "match.json"
    if not p.exists():
        return None
    return MatchData.model_validate_json(p.read_text(encoding="utf-8"))


def save_analysis(match_id: str, steamid: str, analysis_json: str) -> None:
    d = match_dir(match_id) / "analysis"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{steamid}.json").write_text(analysis_json, encoding="utf-8")


def load_analysis(match_id: str, steamid: str) -> Optional[dict]:
    p = MATCHES_DIR / match_id / "analysis" / f"{steamid}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


init_db()
