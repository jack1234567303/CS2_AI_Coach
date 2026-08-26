"""全局配置:路径、LLM Key 等。全部支持环境变量覆盖。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CS2COACH_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = DATA_DIR / "coach.db"
MATCHES_DIR = DATA_DIR / "matches"
UPLOADS_DIR = DATA_DIR / "uploads"

DATA_DIR.mkdir(parents=True, exist_ok=True)
MATCHES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ---- LLM 配置(AI Coach,Phase 3)----
# LLM 只用于解释分析结果,绝不参与 Demo 解析。
LLM_PROVIDER = os.environ.get("CS2COACH_LLM_PROVIDER", "auto")  # auto | openai | anthropic | none
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("CS2COACH_OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.environ.get("CS2COACH_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

# ---- 分析参数(单位:demo tick 与 CS2 世界单位)----
# 见 analyzer/common.py,可按需要调整后重新分析。
