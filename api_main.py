"""
FPL FantasyTrivia - FastAPI Backend
Main API server replacing Streamlit
"""
import os
os.environ['TRANSFORMERS_NO_TF'] = '1'          # skip TF/Keras import entirely
os.environ['TRANSFORMERS_NO_ADVISORY_WARNINGS'] = '1'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

from utils.console import ensure_utf8_stdio
ensure_utf8_stdio()  # before any startup print/log (emoji crash cp1252 on Windows)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from dataclasses import asdict, dataclass
from typing import AsyncIterator, List, Literal, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import asyncio
import json
import inspect
import time
import hashlib
import re
import unicodedata
import logging
import io
import pandas as pd
from threading import Event as ThreadEvent, Lock, Thread
from contextlib import asynccontextmanager
from urllib.parse import quote_plus, urlparse

# Import project modules
from config.settings import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    HUGGINGFACE_API_TOKEN, DATA_PATH, SEASONS, CURRENT_SEASON,
    GOOGLE_CUSTOM_SEARCH_API_KEY, GOOGLE_CUSTOM_SEARCH_CX
)
from graph.connection import Neo4jConnection
from typing import Optional


from graph.queries import CypherQueries, QueryExecutor
from graph.data_loader import FPLDataLoader
from preprocessing.intent_classifier import IntentClassifier, Intent, is_team_squad_question
from services import fixture_picks
from services import player_outlook, transfer_advice
from preprocessing.entity_extractor import EntityExtractor
# LAZY IMPORT: EmbeddingManager only when needed to avoid transformer/Keras dependency
# from embeddings.embedding_manager import EmbeddingManager
try:
    import redis  # type: ignore
except Exception:
    redis = None

from services.player_index import PlayerIndex
from services.avatars import AvatarService, ImageProxy, is_proxy_allowed, FPL_PHOTO_URL
from services.predictions import get_predictions_df
from services import season_service
from services import model_review
from services import answer_cards
from services import analytics
from services import explain
from services.security import RateLimiter, limits_from_env, require_admin
from trivia.trivia_generator import TriviaGenerator, TriviaQuestion as GeneratedTriviaQuestion, TriviaCategory, Difficulty
from llm.llm_manager import LLMManager, PromptBuilder
from llm.prompts import PromptTemplates
from ml.api_integration import MLAPIIntegration, register_ml_routes, PlayerPredictionRequest, PredictionResponse, TopPerformersRequest

import httpx

logger = logging.getLogger(__name__)

# Global state
app_state = {
    "neo4j_conn": None,
    "intent_classifier": None,
    "entity_extractor": None,
    "llm_manager": None,
    "embedding_manager": None,
    "embeddings_built": False,
    "embedding_building": False,
    "embedding_build_error": None,
    "trivia_cache": None,
    "player_index": None,
    "latest_season": None,
    "avatar_service": None,
    "image_proxy": None,
    "ml_integration": None,
}

embedding_build_lock = Lock()


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8/Latin-1 mojibake such as NÃºÃ±ez -> Núñez."""
    if not text:
        return text
    if "Ã" not in text and "Â" not in text:
        return text

    try:
        return text.encode("latin1").decode("utf-8")
    except Exception:
        return text


class TriviaQuestionCache:
    """Cache generated trivia questions for answer validation."""

    def __init__(self, ttl_seconds: int = 1800, repeat_window_seconds: int = 1800):
        self.ttl_seconds = ttl_seconds
        self.repeat_window_seconds = repeat_window_seconds
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._recent_questions: Dict[str, float] = {}
        self._redis = None

        redis_url = os.getenv("REDIS_URL")
        if redis_url and redis is not None:
            try:
                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                print("✅ Trivia cache using Redis")
            except Exception as exc:
                print(f"⚠️ Redis unavailable for trivia cache, falling back to memory: {exc}")
                self._redis = None

    def _serialize(self, question: GeneratedTriviaQuestion) -> Dict[str, Any]:
        payload = asdict(question)
        payload["category"] = question.category.value
        payload["difficulty"] = question.difficulty.value
        return payload

    def _deserialize(self, payload: Dict[str, Any]) -> Optional[GeneratedTriviaQuestion]:
        try:
            return GeneratedTriviaQuestion(
                question_id=payload["question_id"],
                question=payload["question"],
                correct_answer=payload["correct_answer"],
                options=list(payload.get("options", [])),
                category=TriviaCategory(payload["category"]),
                difficulty=Difficulty(payload["difficulty"]),
                explanation=payload["explanation"],
                source_query=payload["source_query"],
                metadata=payload.get("metadata", {}),
            )
        except Exception:
            return None

    def store(self, question: GeneratedTriviaQuestion) -> None:
        payload = self._serialize(question)
        if self._redis is not None:
            self._redis.setex(question.question_id, self.ttl_seconds, json.dumps(payload))
            return

        self._memory[question.question_id] = {
            "expires_at": time.time() + self.ttl_seconds,
            "payload": payload,
        }

    def get(self, question_id: str) -> Optional[GeneratedTriviaQuestion]:
        if self._redis is not None:
            raw = self._redis.get(question_id)
            if not raw:
                return None
            try:
                return self._deserialize(json.loads(raw))
            except Exception:
                return None

        record = self._memory.get(question_id)
        if not record:
            return None
        if record["expires_at"] < time.time():
            self._memory.pop(question_id, None)
            return None
        return self._deserialize(record["payload"])

    def _question_key(self, question_text: str) -> str:
        normalized = " ".join(question_text.lower().split())
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
        return f"trivia:recent:{digest}"

    def is_recent(self, question_text: str) -> bool:
        key = self._question_key(question_text)

        if self._redis is not None:
            try:
                return bool(self._redis.exists(key))
            except Exception:
                return False

        now = time.time()
        expired = [k for k, exp in self._recent_questions.items() if exp < now]
        for k in expired:
            self._recent_questions.pop(k, None)
        return key in self._recent_questions

    def mark_recent(self, question_text: str) -> None:
        key = self._question_key(question_text)

        if self._redis is not None:
            try:
                self._redis.setex(key, self.repeat_window_seconds, "1")
                return
            except Exception:
                pass

        self._recent_questions[key] = time.time() + self.repeat_window_seconds


NEO4J_RETRY_FIRST_DELAY = 2.0
NEO4J_RETRY_MAX_DELAY = 30.0


def _try_connect_neo4j() -> Optional[Neo4jConnection]:
    """One attempt at the configured Neo4j; returns a verified connection or None."""
    try:
        conn = Neo4jConnection(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    except Exception as e:
        logger.warning("Neo4j connection failed: %s", e)
        return None
    if conn.test_connection():
        return conn
    conn.close()
    return None


async def _attach_neo4j(conn: Neo4jConnection) -> None:
    """Make `conn` the app's connection and rebuild everything derived from the graph."""
    app_state["neo4j_conn"] = conn
    reset_graph_caches()
    _health_stats_cache["at"] = 0.0
    app_state["entity_extractor"] = None  # rebuilt lazily with the player names (missing if built while disconnected)
    ml = app_state.get("ml_integration")
    if ml is not None:  # it was handed the connection at startup, possibly None
        ml.neo4j_conn = conn
        if getattr(ml, "fixture_service", None) is not None:
            ml.fixture_service.conn = conn
    try:
        index = await asyncio.to_thread(get_player_index, conn)  # also feeds the entity extractor
        logger.info("Player index built (%d players)", len(index))
    except Exception as index_error:
        logger.warning("Player index build failed: %s", index_error)


async def _reconnect_neo4j_until_connected() -> None:
    """Retry with backoff until Neo4j answers, then attach it.

    After a VM reboot Docker restarts every container at once (depends_on/service_healthy only
    applies to `docker compose up`), so the backend can start before Neo4j accepts connections.
    Without this it stayed "disconnected" and every graph endpoint returned 503 until restarted.
    """
    delay = NEO4J_RETRY_FIRST_DELAY
    while app_state["neo4j_conn"] is None:
        await asyncio.sleep(delay)
        if app_state["neo4j_conn"] is not None:  # attached via the admin connect endpoint meanwhile
            return
        conn = await asyncio.to_thread(_try_connect_neo4j)
        if conn is not None:
            if app_state["neo4j_conn"] is None:  # the admin connect endpoint may have won the race
                await _attach_neo4j(conn)
                logger.info("Neo4j connected after retrying")
            else:
                conn.close()
            return
        delay = min(delay * 2, NEO4J_RETRY_MAX_DELAY)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, cleanup on shutdown."""
    # Startup
    print("🚀 Initializing FPL FantasyTrivia API...")
    
    # Initialize LLM manager first (lightweight)
    # Groq (GROQ_API_KEY) serves all current models; HuggingFace is optional
    llm_manager = LLMManager(api_token=HUGGINGFACE_API_TOKEN)
    if llm_manager.available:
        app_state["llm_manager"] = llm_manager
    else:
        print("⚠️  No LLM provider configured (set GROQ_API_KEY); answers will show raw graph data")

    app_state["trivia_cache"] = TriviaQuestionCache()

    # Page-view counts (aggregate only) from LIVE_DATA_DIR/analytics.json
    await asyncio.to_thread(analytics.get_store)

    # Avatar lookups (FPL photo codes load in the background)
    avatar_service = AvatarService(
        google_api_key=GOOGLE_CUSTOM_SEARCH_API_KEY, google_cx=GOOGLE_CUSTOM_SEARCH_CX
    )
    await avatar_service.start()
    app_state["avatar_service"] = avatar_service
    app_state["image_proxy"] = ImageProxy(avatar_service.client)
    
    # Connect to Neo4j; if it isn't up yet (e.g. right after a reboot), keep retrying in the background
    conn = await asyncio.to_thread(_try_connect_neo4j)
    reconnect_task = None
    if conn is not None:
        await _attach_neo4j(conn)
        print("✅ Neo4j connected")
    else:
        print("⚠️  Neo4j not reachable yet; retrying in the background")
        reconnect_task = asyncio.create_task(_reconnect_neo4j_until_connected())
    
    # Load prebuilt embeddings on startup (avoids building them in-process)
    print("🔮 Checking for prebuilt embeddings...")
    try:
        # Lazy import to avoid transformer/Keras dependency at startup
        from embeddings.embedding_manager import EmbeddingManager
        
        # Try MPNet first, then MiniLM
        for model_key in ["mpnet", "minilm"]:
            prebuilt_path = f"embeddings/prebuilt/{model_key}_embeddings.pkl"
            if os.path.exists(prebuilt_path):
                print(f"   Found {model_key} embeddings, loading...")
                # Load embeddings WITHOUT initializing the sentence-transformer model
                # The model is only needed for search queries, not for loading prebuilt vectors
                manager = EmbeddingManager.__new__(EmbeddingManager)
                manager.model_key = model_key
                manager.model_info = EmbeddingManager.MODELS[model_key]
                manager.model = None  # Defer model loading until first search
                manager.player_embeddings = {}
                manager.player_metadata = {}
                if manager.load_embeddings(prebuilt_path):
                    app_state["embedding_manager"] = manager
                    app_state["embeddings_built"] = True
                    print(f"✅ Loaded {len(manager.player_embeddings)} prebuilt {model_key} embeddings (model deferred)")
                    break
        else:
            print("   ℹ️  No prebuilt embeddings found (will build on request)")
    except ImportError as import_err:
        print(f"⚠️  EmbeddingManager import failed (transformer dependency issue): {import_err}")
        print("   ℹ️  Embeddings will be unavailable but ML predictions will work")
    except Exception as emb_error:
        print(f"⚠️  Failed to load prebuilt embeddings: {emb_error}")

    # Initialize ML prediction integration
    print("🤖 Initializing ML prediction engine...")
    try:
        ml_integration = MLAPIIntegration(
            neo4j_conn=app_state["neo4j_conn"],
            query_executor=None  # Not needed; queries run directly via neo4j_conn
        )
        # Try XGBoost v2 models (retrained with robustness features)
        xgb_models = {
            "GK": "ml/models/xgboost_gk_v2.pkl",
            "DEF": "ml/models/xgboost_def_v2.pkl",
            "MID": "ml/models/xgboost_mid_v2.pkl",
            "FWD": "ml/models/xgboost_fwd_v2.pkl"
        }
        
        model_loaded = False
        if all(os.path.exists(p) for p in xgb_models.values()):
            ml_integration.load_predictors_by_position(xgb_models)
            model_loaded = True
            print("✅ XGBoost v2 position-specific ML models loaded (retrained with robustness features)")
        else:
            # Fall back to linear regression
            for model_path in [
                "ml/models/linear_regression_v1.pkl",
            ]:
                if os.path.exists(model_path):
                    ml_integration.load_predictor(model_path)
                    model_loaded = True
                    print(f"✅ ML model loaded: {model_path}")
                    break
        if not model_loaded:
            print("⚠️  No trained ML model found — prediction endpoints will be unavailable")
        app_state["ml_integration"] = ml_integration
    except Exception as ml_error:
        print(f"⚠️  ML integration failed to initialize: {ml_error}")

    print("✅ API Ready!")

    yield

    # Shutdown
    if reconnect_task is not None and not reconnect_task.done():
        reconnect_task.cancel()
    await asyncio.to_thread(analytics.close_store)
    if app_state["avatar_service"]:
        await app_state["avatar_service"].close()
    if app_state["neo4j_conn"]:
        app_state["neo4j_conn"].close()
    print("👋 API Shutdown")


app = FastAPI(
    title="FPL FantasyTrivia API",
    description="Graph-RAG API for Fantasy Premier League Q&A and Trivia",
    version="1.0.0",
    lifespan=lifespan
)

# CORS: in production the frontend is served from the same origin (deploy/Caddyfile),
# so only local dev servers and any CORS_ORIGINS (comma-separated) need allowing.
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:3001",
] + [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ============================================================================
# ML Prediction Routes (registered at module load; integration resolved lazily)
# ============================================================================

class _LazyMLIntegration:
    """Thin proxy that forwards calls to the MLAPIIntegration stored in app_state.
    This lets us register routes at import time while the real instance is created
    during the lifespan startup."""

    @property
    def _real(self):
        return app_state.get("ml_integration")

    @property
    def predictor_loaded(self):
        real = self._real
        return real.predictor_loaded if real else False

    @property
    def predictor(self):
        real = self._real
        return real.predictor if real else None

    @property
    def predictors(self):
        real = self._real
        return real.predictors if real else {}

    async def predict_player_next_gameweek(self, request):
        # Serve the precomputed v3 predictions so every page shows the same numbers
        df = get_predictions_df()
        if df is not None:
            index = app_state.get("player_index")
            name = (index.resolve(request.player_name) if index else None) or request.player_name
            hit = df[df["name_lower"] == name.lower()]
            if not len(hit):
                raise HTTPException(status_code=404, detail=f"{name} is not in the current FPL game")
            row = hit.iloc[0]
            return PredictionResponse(
                player_name=row["name"], predicted_points=round(float(row["predicted_points"]), 2),
                model_version=str(row.get("prediction_method", "xgboost_v3")), team_name=row["team"],
                fixture_context={
                    "opponent_team": row["opponent"], "home_away": "home" if bool(row["is_home"]) else "away",
                    "match_date": row["kickoff_time"], "matchday": int(row["gameweek"]),
                },
            )
        if not self._real:
            raise HTTPException(status_code=503, detail="ML integration not initialized")
        return await self._real.predict_player_next_gameweek(request)

    async def predict_top_performers(self, request):
        if not self._real:
            raise HTTPException(status_code=503, detail="ML integration not initialized")
        return await self._real.predict_top_performers(request)

    async def predict_best_value(self, request):
        if not self._real:
            raise HTTPException(status_code=503, detail="ML integration not initialized")
        return await self._real.predict_best_value(request)


_lazy_ml = _LazyMLIntegration()
register_ml_routes(app, _lazy_ml)


# ============================================================================
# SIMPLE CSV-BASED ML ENDPOINTS (uses precomputed predictions)
# ============================================================================

def _filter_predictions(df, position: Optional[str], team: Optional[str], max_price: Optional[float],
                        q: Optional[str]):
    """The predictions rows for one position, club (any case), highest price (GBP m; the file's
    value is in tenths) and name search (accents ignored)."""
    if position:
        df = df[df['position'] == position.upper()]
    if team and team.strip():
        df = df[df['team'].astype(str).str.lower() == team.strip().lower()]
    if max_price is not None and 'value' in df.columns:
        df = df[pd.to_numeric(df['value'], errors='coerce') <= max_price * 10 + 1e-9]
    if q and q.strip():
        from utils.text import normalize_name
        wanted = normalize_name(q)
        # The full name or FPL's short name, since the table shows the short one ("Bruno G.")
        df = df[df['name'].astype(str).map(
            lambda n: wanted in normalize_name(n) or wanted in normalize_name(_web_name(n) or ""))]
    return df


@app.get("/api/ml/top-performers")
async def get_top_performers_csv(position: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                                 team: Optional[str] = Query(None, max_length=40),
                                 max_price: Optional[float] = Query(None, ge=3.0, le=20.0),
                                 q: Optional[str] = Query(None, max_length=60)):
    """
    Get top performers from precomputed predictions CSV.
    This endpoint provides faster responses by using pre-generated predictions.
    """
    df = get_predictions_df()
    if df is None:
        raise HTTPException(
            status_code=503,
            detail="Predictions not available. Please generate predictions first."
        )
    
    try:
        df = _filter_predictions(df, position, team, max_price, q)

        # Sort by predicted points and get top N
        df = df.nlargest(limit, 'predicted_points')
        
        # Format response
        predictions = []
        for _, row in df.iterrows():
            # Build fixture context
            fixture_context = {
                "opponent_team": row['opponent'],
                "home_away": "home" if bool(row['is_home']) else "away",
                "match_date": row['kickoff_time'],
                "matchday": int(row['gameweek']),
                "fixture_id": f"{row['team']}_vs_{row['opponent']}_GW{int(row['gameweek'])}"
            }
            
            predictions.append({
                "player_name": row['name'],
                "web_name": _web_name(row['name']),
                "predicted_points": round(float(row['predicted_points']), 2),
                "team_name": row['team'],
                "position": row['position'],
                "cost": round(float(row['value']) / 10.0, 1) if pd.notna(row.get('value')) else None,
                "model_version": row.get('prediction_method', 'xgboost_v3'),
                "fixture_context": fixture_context,
                **_availability_fields(row),
            })
        
        return {
            "predictions": predictions,
            "model_version": str(df['prediction_method'].mode().iat[0]) if 'prediction_method' in df.columns and len(df) else 'xgboost_v3',
            "total_predictions": len(predictions),
            "metadata": {
                "position_filter": position,
                "source": "precomputed_csv",
                "gameweek": int(df.iloc[0]['gameweek']) if len(df) > 0 else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error reading predictions CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ml/best-value")
async def get_best_value_csv(position: Optional[str] = None, limit: int = Query(10, ge=1, le=50),
                             team: Optional[str] = Query(None, max_length=40),
                             max_price: Optional[float] = Query(None, ge=3.0, le=20.0),
                             q: Optional[str] = Query(None, max_length=60)):
    """
    Get best value players from precomputed predictions CSV.
    Value is calculated as predicted_points / (value / 10).
    """
    df = get_predictions_df()
    if df is None:
        raise HTTPException(
            status_code=503,
            detail="Predictions not available. Please generate predictions first."
        )
    
    try:
        # Calculate value score (points per million)
        df = df.assign(value_score=df['predicted_points'] / (df['value'] / 10.0))
        df = _filter_predictions(df, position, team, max_price, q)
        
        # Sort by value score and get top N
        df = df.nlargest(limit, 'value_score')
        
        # Format response
        predictions = []
        for _, row in df.iterrows():
            # Build fixture context
            fixture_context = {
                "opponent_team": row['opponent'],
                "home_away": "home" if bool(row['is_home']) else "away",
                "match_date": row['kickoff_time'],
                "matchday": int(row['gameweek']),
                "fixture_id": f"{row['team']}_vs_{row['opponent']}_GW{int(row['gameweek'])}"
            }
            
            predictions.append({
                "player_name": row['name'],
                "web_name": _web_name(row['name']),
                "predicted_points": round(float(row['predicted_points']), 2),
                "team_name": row['team'],
                "position": row['position'],
                "value": round(float(row['value']) / 10.0, 1),
                "value_score": round(float(row['value_score']), 2),
                "model_version": row.get('prediction_method', 'xgboost_v3'),
                "fixture_context": fixture_context,
                **_availability_fields(row),
            })
        
        return {
            "predictions": predictions,
            "model_version": str(df['prediction_method'].mode().iat[0]) if 'prediction_method' in df.columns and len(df) else 'xgboost_v3',
            "total_predictions": len(predictions),
            "metadata": {
                "position_filter": position,
                "source": "precomputed_csv",
                "gameweek": int(df.iloc[0]['gameweek']) if len(df) > 0 else None
            }
        }
        
    except Exception as e:
        logger.error(f"Error reading predictions CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Pydantic Models
# ============================================================================

class ConnectionRequest(BaseModel):
    uri: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ConnectionResponse(BaseModel):
    success: bool
    message: str
    stats: Optional[Dict[str, int]] = None


MAX_QUESTION_CHARS = 1000
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 2000


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    model: str = Field(default="gpt-oss-120b", max_length=64)  # checked against LLMManager.MODELS
    retrieval_method: str = Field(default="Hybrid", max_length=32)  # "Baseline", "Embeddings", "Hybrid"
    embedding_model: str = Field(default="minilm", max_length=32)  # "minilm" or "mpnet"
    is_first_message: bool = False  # only allows a greeting; the system prompt is always used
    chat_history: List[Dict[str, str]] = Field(default_factory=list)  # [{"role": "user"/"assistant", "content": ...}]

    @field_validator("chat_history")
    @classmethod
    def _bound_history(cls, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Keep the client-sent transcript small and to plain user/assistant turns.

        The prompt only uses the last few turns, but an unbounded list or huge messages would
        still cost tokens; other roles (e.g. "system") are dropped rather than trusted.
        """
        cleaned = []
        for msg in history[-MAX_HISTORY_MESSAGES:]:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            cleaned.append({"role": role, "content": str(msg.get("content", ""))[:MAX_HISTORY_CHARS]})
        return cleaned


class QueryResponse(BaseModel):
    answer: str
    intent: str
    entities: Dict[str, Any]
    cypher_query: str
    kg_context: str
    embedding_context: Optional[str] = None
    ml_context: Optional[str] = None
    embedding_used: bool = False
    results: List[Dict[str, Any]]
    graph_data: Optional[Dict[str, Any]] = None
    model_type: Optional[str] = None
    # Visual blocks shown with the answer (player, comparison, leaderboard, prediction, team,
    # fixtures, season) and suggested next questions; see services/answer_cards.py
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class ImageSearchResponse(BaseModel):
    query: str
    image_url: Optional[str] = None
    source: Optional[str] = None


def looks_like_small_talk(question: str) -> bool:
    """Return True when the message is a normal chat response rather than an FPL query."""
    text = question.strip().lower()
    if not text:
        return True

    small_talk_patterns = [
        r"^(hi|hello|hey|yo|thanks|thank you|thx|nice|cool|wow|great|awesome|amazing)([!.?\s].*)?$",
        r"^(that's|that is)\s+(great|awesome|amazing|cool|nice)([!.?\s].*)?$",
        r"^(ok|okay|sure|got it|understood|perfect)([!.?\s].*)?$",
        r"^(lol|lmao|haha|haha+)([!.?\s].*)?$",
    ]

    return any(re.match(pattern, text) for pattern in small_talk_patterns)


async def run_db(conn, query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run a Neo4j query in a worker thread so it doesn't block the event loop."""
    return await asyncio.to_thread(conn.execute_query, query, params or {})


_STATUS_LABELS = {"d": "doubtful", "i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad"}


def _web_name(name: Any) -> Optional[str]:
    """FPL's display name for a current player ("Evanilson" for "Francisco Evanilson de Lima
    Barbosa") from the status file, or None. Display only: lookups keep the full name."""
    try:
        return season_service.status_lookup()["web_name"].get(str(name))
    except Exception:  # a missing or unreadable status file only drops the short name
        return None


def _availability_fields(row) -> Dict[str, Any]:
    """status / chance_of_playing_next_round / news from a predictions row, JSON-safe."""
    def clean(value):
        return None if value is None or (isinstance(value, float) and value != value) else value
    status = clean(row.get("status")) if hasattr(row, "get") else None
    chance = clean(row.get("chance_of_playing_next_round")) if hasattr(row, "get") else None
    news = clean(row.get("news")) if hasattr(row, "get") else None
    return {
        "status": str(status) if status is not None else "a",
        "chance_of_playing_next_round": int(float(chance)) if chance is not None else None,
        "news": str(news) if news else "",
    }


def _availability_note(row, inline: bool = False) -> str:
    """FPL's official availability for a predictions row (status/news columns), if any."""
    status = str(row.get("status") or "a") if hasattr(row, "get") else "a"
    if status in ("a", "nan"):
        return ""
    news = row.get("news")
    news = "" if news is None or str(news) == "nan" else str(news).strip()
    chance = row.get("chance_of_playing_next_round")
    chance_text = "" if chance is None or str(chance) == "nan" else f", {int(float(chance))}% chance of playing"
    label = _STATUS_LABELS.get(status, status)
    detail = f"{label}{chance_text}" + (f": {news}" if news else "")
    return f" [{detail}]" if inline else f"\n- Availability (official FPL news): {detail}"


def _top_predictions_context(df, n: int = 5) -> str:
    """The n highest predictions as a numbered list, best first, then the highest-ranked entry of each
    position in it. Without the ranks spelt out, the LLM named a well-known player as the "next" or the
    "midfield option" even when the list ranked another midfielder above him."""
    top = df.nlargest(n, "predicted_points")
    lines = [f"ML Predictions - Top {len(top)} performers for next gameweek, ranked best first:"]
    best: dict[str, tuple[int, str, float]] = {}
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        home_away = "H" if row["is_home"] else "A"
        lines.append(f"{rank}. {row['name']} ({row['position']}): {row['predicted_points']:.1f} pts "
                     f"vs {row['opponent']} ({home_away}){_availability_note(row, inline=True)}")
        best.setdefault(str(row["position"]), (rank, str(row["name"]), float(row["predicted_points"])))
    if len(best) > 1:
        order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
        picks = sorted(best.items(), key=lambda item: order.get(item[0], 4))
        lines.append("Highest-ranked in this list by position: "
                     + "; ".join(f"{pos} #{rank} {name} ({points:.1f} pts)" for pos, (rank, name, points) in picks))
    return "\n".join(lines) + "\n"


def _forecast_context(conn, names: List[str]) -> Optional[str]:
    """Every named player's forecast, price, fixtures and season so far, for a question that
    weighs them over the coming gameweek (services/player_outlook.py). None without a
    predictions file, so the single-player path's model fallback can answer. Blocking."""
    df = get_predictions_df()
    if df is None:
        return None
    outlooks = player_outlook.load_outlooks(conn, names, df)
    return player_outlook.forecast_context([outlooks[name] for name in names])


def _transfer_context(conn, transfer: "transfer_advice.TransferQuestion") -> str:
    """The Transfer Analysis for a transfer question (services/transfer_advice.py). Blocking."""
    outlooks = player_outlook.load_outlooks(conn, transfer.players, get_predictions_df())
    return transfer_advice.context(transfer, outlooks)


def get_player_index(conn=None) -> PlayerIndex:
    """Shared in-memory player index; built at startup and rebuilt after data loads."""
    index = app_state["player_index"]
    if index is None:
        conn = conn or app_state["neo4j_conn"]
        if conn is None:
            raise HTTPException(status_code=503, detail="Neo4j not connected")
        index = PlayerIndex.load(conn)
        app_state["player_index"] = index
        if app_state["entity_extractor"] is not None:
            app_state["entity_extractor"].player_index = index
    return index


async def get_player_index_async(conn=None) -> PlayerIndex:
    if app_state["player_index"] is not None:
        return app_state["player_index"]
    return await asyncio.to_thread(get_player_index, conn)


def get_latest_season(conn=None) -> str:
    """Most recent season that has match data, used when a question names no season."""
    season = app_state["latest_season"]
    if season is None:
        conn = conn or app_state["neo4j_conn"]
        rows = conn.execute_query(
            "MATCH (s:Season) WHERE EXISTS { ()-[:SEASON_STATS]->(s) } RETURN max(s.id) AS season"
        ) if conn else []
        season = (rows[0]["season"] if rows else None) or SEASONS[-1]
        app_state["latest_season"] = season
    return season


def default_required_season(method, params: Dict[str, Any], latest_season: Optional[str]) -> None:
    """Give season-scoped queries the latest season when the question names none.

    Without it a query like get_best_value_players(season) had a missing required param and
    fell back to an all-seasons ranking, which recommended players who have left the league
    ("captain Salah ... across all seasons 2020-21 through 2026-27").
    """
    if "season" in params or not latest_season:
        return
    param = inspect.signature(method).parameters.get("season")
    if param is not None and param.default is inspect.Parameter.empty:
        params["season"] = latest_season


ML_MODEL_LABEL = "Gradient-boosted trees"
ML_FALLBACK_LABEL = "Backup model"


def _ml_model_label() -> Optional[str]:
    """What the site calls the model behind user-facing predictions (shown on /predictions and
    Settings, so plain words rather than an internal version): the predictions file's model
    when present, else the legacy v2 stack (only a fallback)."""
    try:
        df = get_predictions_df()
        if df is not None and len(df):
            return ML_MODEL_LABEL
    except Exception:
        pass
    ml_integration = app_state.get("ml_integration")
    if ml_integration:
        if ml_integration.predictors:
            return ML_FALLBACK_LABEL
        if ml_integration.predictor:
            return ml_integration.predictor.model_type
    return None


def reset_graph_caches() -> None:
    """Drop everything derived from graph contents (after connect or data load)."""
    app_state["player_index"] = None
    app_state["latest_season"] = None
    season_service.clear_cache()


ANSWER_CARDS_TIMEOUT_SECONDS = 10


def _answer_card_inputs() -> Dict[str, Any]:
    """File-backed inputs of the answer cards, each cached by mtime: the predictions, FPL
    photo codes and web names from the status file, and the live season's meta.json."""
    inputs: Dict[str, Any] = {"predictions": None, "photo_codes": {}, "web_names": {}, "meta": {}}
    try:
        inputs["predictions"] = get_predictions_df()
    except Exception as exc:
        logger.warning("Predictions unavailable for answer cards: %s", exc)
    try:
        lookup = season_service.status_lookup()
        inputs["photo_codes"], inputs["web_names"] = lookup["code"], lookup["web_name"]
        inputs["meta"] = season_service.read_meta()
    except Exception as exc:
        logger.warning("Live season files unavailable for answer cards: %s", exc)
    return inputs


async def build_answer_cards(**context: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Cards and follow-up questions for an answer (services/answer_cards.py). Never raises:
    the answer goes out without them if they fail or take too long."""
    try:
        inputs = await asyncio.to_thread(_answer_card_inputs)
        ctx = answer_cards.CardContext(photo_for=_photo_for, **inputs, **context)
        return await asyncio.wait_for(answer_cards.build_cards(ctx), timeout=ANSWER_CARDS_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("Answer cards failed", exc_info=True)
        return [], []


def _team_of_week_for_question(conn, question: str, gameweek: Optional[int]) -> Optional[Dict[str, Any]]:
    """The squad a "best XI" question asks for (with photos), or None when there is none."""
    try:
        finished = season_service.get_season_current(conn)["finished_gameweeks"]
        mode, target = season_service.team_of_week_target(question, gameweek, finished)
        return season_service.get_team_of_the_week(conn, mode, target, photo_for=_photo_for)
    except season_service.NotFoundError as exc:
        logger.info("No team of the week for %r: %s", question, exc)
        return None


async def avatar_for(player_name: str, team_name: Optional[str] = None) -> Optional[str]:
    service: Optional[AvatarService] = app_state["avatar_service"]
    if service is None or not player_name:
        return None
    if team_name is None and app_state["player_index"] is not None:
        team_name = app_state["player_index"].team_of(player_name)
    return await service.get(player_name, team_name)


async def enrich_rows_with_avatars(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of rows with an `avatar` URL, looked up concurrently."""
    service: Optional[AvatarService] = app_state["avatar_service"]
    if service is None:
        return [dict(r) for r in rows]
    index = app_state["player_index"]
    return await service.enrich(rows, team_of=index.team_of if index else None)


class TriviaQuestion(BaseModel):
    question: str
    options: List[str]
    category: str
    difficulty: str
    question_id: str


class TriviaAnswerRequest(BaseModel):
    question_id: str
    answer: str


class TriviaAnswerResponse(BaseModel):
    correct: bool
    feedback: str
    correct_answer: Optional[str] = None


class EmbeddingBuildRequest(BaseModel):
    model: str = "minilm"


class EmbeddingBuildResponse(BaseModel):
    success: bool
    count: int
    message: str
    started: bool = False
    building: bool = False
    model: Optional[str] = None


class PlayerSearchRequest(BaseModel):
    query: str = Field(max_length=100)
    limit: int = Field(default=20, ge=1, le=50)
    include_avatars: bool = False  # Default to False for fast search


class PlayerComparisonRequest(BaseModel):
    player1: str
    player2: str
    season: Optional[str] = None


class DataLoadRequest(BaseModel):
    clear_existing: bool = True


class FixtureLoadRequest(BaseModel):
    csv_path: str
    season_id: str = "2026-27"
    clear_existing: bool = False


class FixtureLoadResponse(BaseModel):
    success: bool
    fixtures_loaded: int
    message: str


class NextFixtureResponse(BaseModel):
    fixtures: List[Dict[str, Any]]


# --- Season dashboard (services/season_service.py) ---------------------------

class SeasonCurrentResponse(BaseModel):
    season: str
    current_gameweek: Optional[int] = None
    next_gameweek: Optional[int] = None
    next_deadline: Optional[str] = None
    finished_gameweeks: List[int]
    updated_at: Optional[str] = None


class SeasonFixture(BaseModel):
    id: str
    gameweek: int
    kickoff_time: Optional[str] = None
    status: str
    home_team: str
    away_team: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    home_difficulty: Optional[int] = None
    away_difficulty: Optional[int] = None


class FixturesResponse(BaseModel):
    season: str
    gameweek: int
    gameweeks: List[int]
    fixtures: List[SeasonFixture]


class TableNextFixture(BaseModel):
    opponent: str
    home: bool
    kickoff_time: Optional[str] = None


class TableRow(BaseModel):
    position: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    form: List[str]
    # The form's matches: fixture_id (for the match page), result, home, score, opponent
    recent: List[Dict[str, Any]] = Field(default_factory=list)
    next_fixture: Optional[TableNextFixture] = None


class TableResponse(BaseModel):
    season: str
    updated_at: Optional[str] = None
    rows: List[TableRow]


class InjuredPlayer(BaseModel):
    name: str
    web_name: Optional[str] = None
    team: str
    position: Optional[str] = None
    status: str
    chance_of_playing_next_round: Optional[int] = None
    news: Optional[str] = None
    news_added: Optional[str] = None
    photo: Optional[str] = None


class InjuriesResponse(BaseModel):
    updated_at: Optional[str] = None
    players: List[InjuredPlayer]


class Opponent(BaseModel):
    team: str
    home: bool


class TeamOfWeekPlayer(BaseModel):
    name: str
    team: str
    position: str
    points: float
    cost: Optional[float] = None
    captain: bool
    vice_captain: bool
    opponents: List[Opponent]
    status: Optional[str] = None
    chance_of_playing_next_round: Optional[int] = None
    news: Optional[str] = None
    photo: Optional[str] = None


class TeamOfWeekResponse(BaseModel):
    mode: str
    gameweek: int
    formation: str
    total_points: float
    total_cost: Optional[float] = None
    budget: Optional[float] = None
    starters: List[TeamOfWeekPlayer]
    bench: List[TeamOfWeekPlayer]


# Keep in sync with season_service.LEADER_SORTS (tests/test_player_leaders.py checks it).
LeaderSort = Literal["total_points", "goals", "assists", "clean_sheets", "bonus", "points_per_game", "minutes"]
LeaderPosition = Literal["GK", "DEF", "MID", "FWD"]


class ModelReviewPlayer(BaseModel):
    name: str
    web_name: Optional[str] = None
    team: str
    position: str
    cost: Optional[float] = None
    predicted_points: Optional[float] = None
    points: Optional[int] = None
    minutes: Optional[int] = None
    multiplier: int
    captain: bool
    vice_captain: bool
    played: Optional[bool] = None
    subbed_in: bool
    subbed_out: bool
    opponents: List[Opponent]
    photo: Optional[str] = None


class AutoSub(BaseModel):
    out: str
    # `in` is a keyword
    in_: str = Field(alias="in", serialization_alias="in")

    model_config = {"populate_by_name": True}


class ModelReviewResponse(BaseModel):
    season: str
    gameweek: int
    gameweeks: List[int]
    status: str
    deadline: Optional[str] = None
    archived_at: Optional[str] = None
    source: str
    note: Optional[str] = None
    formation: str
    picked_formation: str
    predicted_points: float
    points: int
    bench_points: int
    fpl_average: Optional[int] = None
    fpl_highest: Optional[int] = None
    total_cost: Optional[float] = None
    budget: Optional[float] = None
    starters: List[ModelReviewPlayer]
    bench: List[ModelReviewPlayer]
    auto_subs: List[AutoSub]
    # Set while the gameweek is played and FPL's live points were merged in: {fetched_at}.
    live: Optional[Dict[str, Any]] = None


class PlayerLeader(BaseModel):
    name: str
    web_name: Optional[str] = None  # FPL's display name, for current players; lookups use `name`
    team: Optional[str] = None
    position: Optional[str] = None
    games: int
    minutes: int
    goals: int
    assists: int
    clean_sheets: int
    bonus: int
    total_points: int
    points_per_game: float
    price: Optional[float] = None
    status: Optional[str] = None
    chance_of_playing_next_round: Optional[int] = None
    news: Optional[str] = None
    photo: Optional[str] = None


class PlayerLeadersResponse(BaseModel):
    season: str
    sort: str
    position: Optional[str] = None
    team: Optional[str] = None
    max_price: Optional[float] = None
    players: List[PlayerLeader]


# ============================================================================
# Helper Functions
# ============================================================================

def get_intent_classifier():
    """Lazy load intent classifier."""
    if not app_state["intent_classifier"]:
        print("🔄 Loading intent classifier...")
        app_state["intent_classifier"] = IntentClassifier()
    return app_state["intent_classifier"]


def get_entity_extractor():
    """Lazy load entity extractor."""
    if not app_state["entity_extractor"]:
        print("🔄 Loading entity extractor...")
        extractor = EntityExtractor()
        
        # Player mentions are matched against the shared player index
        if app_state["neo4j_conn"]:
            try:
                index = get_player_index()
                extractor.player_index = index
                extractor.set_known_players({e.player_name for e in index.entries})
            except Exception as e:
                print(f"Warning: Failed to load player names: {e}")
        app_state["entity_extractor"] = extractor
    return app_state["entity_extractor"]


def get_neo4j_conn():
    """Dependency to get Neo4j connection."""
    if not app_state["neo4j_conn"]:
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    return app_state["neo4j_conn"]


# Link previews for social apps' crawlers and the club/match sitemap (Caddy sends them here)
from services import seo  # noqa: E402
seo.register(app, get_neo4j_conn)


def run_embedding_build(model_key: str, conn: Neo4jConnection) -> None:
    """Build embeddings asynchronously or load prebuilt ones."""
    try:
        logger.info("Starting background embedding build for model %s", model_key)
        app_state["embedding_build_error"] = None

        if not app_state["embedding_manager"]:
            logger.info("Initializing embedding manager...")
            # Lazy import
            from embeddings.embedding_manager import EmbeddingManager
            app_state["embedding_manager"] = EmbeddingManager(model_key=model_key)
            
            # Try to load prebuilt embeddings first
            prebuilt_path = f"embeddings/prebuilt/{model_key}_embeddings.pkl"
            if app_state["embedding_manager"].load_embeddings(prebuilt_path):
                logger.info("Successfully loaded prebuilt embeddings from %s", prebuilt_path)
                app_state["embeddings_built"] = True
                app_state["embedding_building"] = False
                return
            else:
                logger.info("No prebuilt embeddings found, building from scratch...")
        elif app_state["embedding_manager"].model_key != model_key:
            logger.info("Switching embedding model...")
            app_state["embedding_manager"].switch_model(model_key)
            
            # Try to load prebuilt for new model
            prebuilt_path = f"embeddings/prebuilt/{model_key}_embeddings.pkl"
            if app_state["embedding_manager"].load_embeddings(prebuilt_path):
                logger.info("Successfully loaded prebuilt embeddings for %s", model_key)
                app_state["embeddings_built"] = True
                app_state["embedding_building"] = False
                return

        logger.info("Fetching player data from Neo4j...")
        query, query_params = CypherQueries.get_player_embeddings_data()
        results = conn.execute_query(query, query_params)

        if not results:
            app_state["embedding_build_error"] = "No player data found. Load FPL data first."
            app_state["embeddings_built"] = False
            logger.error("No player data found for embedding build")
            return

        logger.info(f"Building embeddings for {len(results)} players...")
        app_state["embedding_manager"].build_player_embeddings(results, batch_size=16)
        app_state["embeddings_built"] = True
        logger.info(
            "✅ Embedding build finished for model %s with %d vectors",
            model_key,
            len(app_state["embedding_manager"].player_embeddings),
        )
    except MemoryError as exc:
        app_state["embeddings_built"] = False
        app_state["embedding_build_error"] = "Out of memory. Try restarting the server or giving it more memory."
        logger.exception("Embedding build failed due to memory error for model %s", model_key)
    except Exception as exc:
        app_state["embeddings_built"] = False
        app_state["embedding_build_error"] = str(exc)
        logger.exception("Embedding build failed for model %s: %s", model_key, str(exc))
    finally:
        app_state["embedding_building"] = False
        logger.info("Embedding build process completed (building flag reset)")


def build_graph_data(results: List[Dict], limit: int = 20) -> Dict[str, Any]:
    """Build graph visualization data from query results."""
    nodes = []
    edges = []
    node_ids = set()
    
    for i, result in enumerate(results[:limit]):
        # Player nodes
        if "player_name" in result:
            player = result["player_name"]
            if player not in node_ids:
                nodes.append({
                    "id": player,
                    "label": player,
                    "type": "player",
                    "data": {
                        "points": result.get("total_points", result.get("points", ""))
                    }
                })
                node_ids.add(player)
            
            # Position relationship
            if "position" in result:
                pos = result["position"]
                if pos not in node_ids:
                    nodes.append({"id": pos, "label": pos, "type": "position"})
                    node_ids.add(pos)
                edges.append({"from": player, "to": pos, "label": "PLAYS"})
            
            # Season relationship
            if "season" in result:
                season = result["season"]
                if season not in node_ids:
                    nodes.append({"id": season, "label": season, "type": "season"})
                    node_ids.add(season)
                edges.append({"from": player, "to": season, "label": "IN"})
        
        # Fixture nodes
        if "home_team" in result and "away_team" in result:
            home = result["home_team"]
            away = result["away_team"]
            
            if home not in node_ids:
                nodes.append({"id": home, "label": home, "type": "team"})
                node_ids.add(home)
            if away not in node_ids:
                nodes.append({"id": away, "label": away, "type": "team"})
                node_ids.add(away)
            
            fixture_id = f"fixture_{i}"
            nodes.append({
                "id": fixture_id,
                "label": f"{result.get('home_score', 0)}-{result.get('away_score', 0)}",
                "type": "fixture"
            })
            edges.append({"from": home, "to": fixture_id, "label": "HOME"})
            edges.append({"from": away, "to": fixture_id, "label": "AWAY"})
    
    return {"nodes": nodes, "edges": edges}


def add_tie_note_if_needed(
    intent: Intent,
    query_method: str,
    params: Dict[str, Any],
    results: List[Dict[str, Any]],
    context: str,
) -> str:
    """Annotate ranked results when the top value is tied so the LLM can mention all co-leaders."""
    if not results:
        return context

    is_goal_leader_query = intent == Intent.TOP_SCORERS or (
        query_method == "get_top_points_by_position" and params.get("sort_by") == "goals"
    )

    if not is_goal_leader_query:
        return context

    metric_key = next((candidate for candidate in ("total_goals", "goals", "answer") if candidate in results[0]), None)
    if not metric_key:
        return context

    top_value = results[0].get(metric_key)
    if top_value is None:
        return context

    name_key = "player_name" if "player_name" in results[0] else "answer"
    tied_names = []
    for row in results:
        if row.get(metric_key) == top_value and row.get(name_key):
            tied_names.append(str(row[name_key]))

    tied_names = list(dict.fromkeys(tied_names))
    if len(tied_names) < 2:
        return context

    if len(tied_names) == 2:
        tie_text = f"{tied_names[0]} and {tied_names[1]}"
    else:
        tie_text = f"{', '.join(tied_names[:-1])}, and {tied_names[-1]}"

    summary = f"**Top Scorers Summary**: {tie_text} share the top spot with {top_value} goals each. Mention all co-leaders."
    note = f"\n**Tie Note**: The top value is shared by {tie_text}. Mention all of them as co-leaders.\n"
    return summary + note + context


def enforce_tied_top_scorers_in_answer(
    answer: str,
    intent: Intent,
    query_method: str,
    params: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> str:
    """Guarantee tied top-scorer answers mention every co-leader."""
    if not answer or not results:
        return answer

    is_goal_leader_query = intent == Intent.TOP_SCORERS or (
        query_method == "get_top_points_by_position" and params.get("sort_by") == "goals"
    )
    if not is_goal_leader_query:
        return answer

    metric_key = next((candidate for candidate in ("total_goals", "goals", "answer") if candidate in results[0]), None)
    if not metric_key:
        return answer

    top_value = results[0].get(metric_key)
    if top_value is None:
        return answer

    name_key = "player_name" if "player_name" in results[0] else "answer"
    tied_names = []
    for row in results:
        if row.get(metric_key) == top_value and row.get(name_key):
            tied_names.append(str(row[name_key]))

    tied_names = list(dict.fromkeys(tied_names))
    if len(tied_names) < 2:
        return answer

    missing_names = [name for name in tied_names if name.lower() not in answer.lower()]
    if not missing_names:
        return answer

    if len(tied_names) == 2:
        tie_text = f"{tied_names[0]} and {tied_names[1]}"
    else:
        tie_text = f"{', '.join(tied_names[:-1])}, and {tied_names[-1]}"

    tie_sentence = f"Top scorers are {tie_text}, with {top_value} goals each."
    stripped_answer = answer.strip()
    if stripped_answer:
        return f"{tie_sentence} {stripped_answer}"
    return tie_sentence


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Lightweight liveness check (used by the container healthcheck)."""
    return {
        "message": "FPL FantasyTrivia API",
        "status": "running",
        "version": "v6.2-ml-integration",
        "commit": "aa9606b"
    }


@app.get("/debug/embeddings")
async def debug_embeddings():
    """Debug: show embedding state."""
    import os
    manager = app_state["embedding_manager"]
    files = {}
    for key in ["mpnet", "minilm"]:
        path = f"embeddings/prebuilt/{key}_embeddings.pkl"
        files[path] = {"exists": os.path.exists(path), "size_mb": round(os.path.getsize(path) / 1_000_000, 1) if os.path.exists(path) else 0}
    return {
        "embedding_manager_exists": manager is not None,
        "model": getattr(manager, "model_key", None) if manager else None,
        "model_loaded": getattr(manager, "model", None) is not None if manager else False,
        "player_embeddings_count": len(getattr(manager, "player_embeddings", {})) if manager else 0,
        "embeddings_built": app_state["embeddings_built"],
        "prebuilt_files": files,
    }


HEALTH_STATS_TTL_SECONDS = 60
_health_stats_cache: Dict[str, Any] = {"at": 0.0, "stats": None}


@app.get("/health")
async def health_check():
    """Detailed health check. The frontend polls this, so DB stats are cached briefly."""
    neo4j_status = "connected" if app_state["neo4j_conn"] else "disconnected"
    
    stats = None
    if app_state["neo4j_conn"]:
        if time.time() - _health_stats_cache["at"] < HEALTH_STATS_TTL_SECONDS:
            stats = _health_stats_cache["stats"]
        else:
            try:
                stats = await asyncio.to_thread(app_state["neo4j_conn"].get_database_stats)
                _health_stats_cache.update(at=time.time(), stats=stats)
            except Exception:
                neo4j_status = "error"
    
    # Get embedding count safely
    embedding_count = 0
    if app_state["embedding_manager"]:
        try:
            embedding_count = len(app_state["embedding_manager"].player_embeddings)
        except Exception as e:
            logger.warning(f"Failed to get embedding count: {e}")
            embedding_count = 0
    
    ml_model_type = _ml_model_label()

    return {
        "status": "healthy",
        "neo4j": neo4j_status,
        "neo4j_stats": stats,
        "llm_available": app_state["llm_manager"] is not None,
        "embeddings_built": app_state["embeddings_built"],
        "embedding_count": embedding_count,
        "embeddings_building": app_state["embedding_building"],
        "embedding_build_error": app_state["embedding_build_error"],
        "ml_available": bool(getattr(app_state.get("ml_integration"), "predictor_loaded", False)),
        "ml_model_type": ml_model_type,
    }


@app.post("/api/connection/connect", response_model=ConnectionResponse, dependencies=[Depends(require_admin)])
async def connect_neo4j(request: ConnectionRequest):
    """Connect to Neo4j database."""
    try:
        uri = (request.uri or NEO4J_URI).strip()
        username = (request.username or NEO4J_USER).strip()
        password = request.password if request.password is not None else NEO4J_PASSWORD

        if not password:
            password = NEO4J_PASSWORD

        conn = Neo4jConnection(uri, username, password)
        if conn.test_connection():
            # Close old connection
            if app_state["neo4j_conn"]:
                app_state["neo4j_conn"].close()

            await _attach_neo4j(conn)
            stats = await asyncio.to_thread(conn.get_database_stats)
            
            # Only return top-level int fields for Pydantic validation
            simple_stats = {
                "total_nodes": stats["total_nodes"],
                "total_relationships": stats["total_relationships"]
            }
            
            return ConnectionResponse(
                success=True,
                message="Connected successfully",
                stats=simple_stats
            )
        else:
            return ConnectionResponse(success=False, message="Connection test failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connection/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_neo4j():
    """Disconnect from Neo4j."""
    if app_state["neo4j_conn"]:
        app_state["neo4j_conn"].close()
        app_state["neo4j_conn"] = None
    reset_graph_caches()
    _health_stats_cache["at"] = 0.0
    return {"success": True, "message": "Disconnected"}


# Per-client limits for the LLM endpoint: every answer spends the shared Groq quota.
# Override with QUERY_RATE_LIMITS="count/seconds,..." (default 10/min and 120/hour).
query_limiter = RateLimiter(limits_from_env("QUERY_RATE_LIMITS", "10/60,120/3600"))


def greeting_response() -> QueryResponse:
    """The reply to a first message that is only small talk ("hi", "thanks")."""
    return QueryResponse(
        answer="Hi! 👋 I'm your FPL assistant — ask about players, teams, stats, or say 'compare' to pit two players against each other. What would you like to know?",
        intent=Intent.GENERAL_QUESTION.value,
        entities={"players": [], "teams": [], "seasons": [], "stats": [], "positions": []},
        cypher_query="",
        kg_context="",
        embedding_context=None,
        embedding_used=False,
        results=[],
        graph_data=None,
        follow_ups=list(answer_cards.STARTER_QUESTIONS),
    )


@dataclass
class PreparedAnswer:
    """A question with everything gathered except the LLM's text (see _prepare_answer)."""
    intent: Intent
    query_method: Optional[str]
    params: Dict[str, Any]
    entities: Any
    results: List[Dict[str, Any]]
    executed_query: str
    cypher_context: str
    ml_context: Optional[str]
    embedding_context: str
    embedding_used: bool
    prompt: Optional[str]          # None when no LLM is configured
    cards_task: asyncio.Task

    def finish(self, text: str) -> str:
        """The final answer from the LLM's text (tied top scorers enforced), or the raw graph
        data when there is no LLM."""
        if self.prompt is None:
            return f"**Knowledge Graph data:**\n\n{self.cypher_context}"
        return enforce_tied_top_scorers_in_answer(
            answer=text, intent=self.intent, query_method=self.query_method, params=self.params, results=self.results,
        )

    async def response(self, answer: str) -> QueryResponse:
        cards, follow_ups = await self.cards_task
        return QueryResponse(
            answer=answer,
            intent=self.intent.value,
            entities=self.entities.to_dict(),
            cypher_query=self.executed_query,
            kg_context=self.cypher_context,
            embedding_context=self.embedding_context,
            ml_context=self.ml_context,
            embedding_used=self.embedding_used,
            results=self.results[:50],  # Limit results sent to frontend
            graph_data=build_graph_data(self.results),
            model_type=_ml_model_label(),
            cards=cards,
            follow_ups=follow_ups,
        )


async def _prepare_answer(request: QueryRequest, conn) -> PreparedAnswer:
    """Steps 1-6 of the Q&A pipeline: intent, entities, the Cypher query, ML and embedding
    context, and the prompt. The answer cards start building here, in the background."""
    cards_task: Optional[asyncio.Task] = None
    try:
        # Step 1: Intent Classification (lazy load)
        intent_classifier = await asyncio.to_thread(get_intent_classifier)
        intent_result = intent_classifier.classify(request.question, chat_history=request.chat_history)

        # If the user's text explicitly mentions clean sheets, prefer the CLEAN_SHEETS intent
        q_lower = request.question.lower()
        if 'clean sheet' in q_lower or 'clean sheets' in q_lower:
            intent_result.intent = Intent.CLEAN_SHEETS
        
        # Step 2: Entity Extraction (lazy load)
        entity_extractor = await asyncio.to_thread(get_entity_extractor)
        entities = entity_extractor.extract(request.question, chat_history=request.chat_history)
        # A player question that names clubs but no player is about the clubs
        intent_result.intent = intent_classifier.refine_with_entities(
            intent_result.intent, entities.players, entities.teams)
        
        # Step 3: Get query parameters
        params = entity_extractor.get_query_parameters(entities)
        # "Top scorers" means goals; without this the generic query ranks by FPL points
        if intent_result.intent == Intent.TOP_SCORERS and "sort_by" not in params:
            params["sort_by"] = "goals"
        # "Who is the Coventry City goalkeeper?", "Who plays for Villa?": the club's squad,
        # not the top-players fallback (a player intent with no player) or no query at all
        squad = is_team_squad_question(request.question, intent_result.intent, entities.players,
                                       entities.teams, entities.positions)
        # "Who's expected to score most in each match next gameweek?": predictions grouped by
        # fixture (services/fixture_picks.py), whatever intent the wording scored highest
        per_fixture = not entities.players and fixture_picks.is_per_fixture_question(request.question)
        if per_fixture:
            intent_result.intent = Intent.PREDICTION
        squad = squad and not per_fixture
        if squad:
            intent_result.intent = Intent.TEAM_ANALYSIS
            if len(entities.positions) > 1:
                params.pop("position", None)  # "defenders and midfielders": the whole squad
            params.setdefault("season", CURRENT_SEASON)
            params.setdefault("limit", 40)

        # "Should I sell Haaland for Isak and Palmer for Rice?": the moves in the order asked,
        # answered from this season and the forecasts (services/transfer_advice.py), no Cypher
        transfer = None
        if not (squad or per_fixture):
            mentions = await asyncio.to_thread(entity_extractor.player_mention_spans, request.question)
            named = {name for _start, _end, name in mentions}
            transfer = transfer_advice.parse(request.question, mentions,
                                             pronoun_players=[p for p in entities.players if p not in named])
        if transfer is not None:
            intent_result.intent = Intent.TRANSFER_ADVICE
            entities.players = transfer.players
            params.update(season=CURRENT_SEASON, transfer_moves=[[m.sell, m.buy] for m in transfer.moves])

        # Several players weighed over the coming gameweek ("Is he a better captain than
        # Haaland this week?"): every one's forecast goes to the LLM, and they're compared on
        # this season (a comparison card with the predictions first)
        forecast = (transfer is None and not per_fixture and len(entities.players) >= 2
                    and intent_result.intent != Intent.TEAM_OF_THE_WEEK
                    and (intent_result.intent == Intent.PREDICTION
                         or player_outlook.is_next_gameweek_question(request.question)))
        if forecast:
            params["forecast"] = True
            params.setdefault("season", await asyncio.to_thread(get_latest_season, conn))
            if intent_result.intent in (Intent.PLAYER_STATS, Intent.PLAYER_SEARCH, Intent.GENERAL_QUESTION,
                                        Intent.UNKNOWN, Intent.RECOMMENDATION):
                intent_result.intent = Intent.PLAYER_COMPARISON

        # Step 4: Execute Cypher query
        query_executor = QueryExecutor(conn)
        cypher_context = ""
        executed_query = ""
        executed_method = None  # the CypherQueries method that ran, after any fallback
        results = []

        query_method = "get_team_squad" if squad else intent_classifier.get_query_type_for_intent(intent_result.intent)

        if query_method:
            # Ensure required parameters
            if "limit" not in params:
                params["limit"] = 10
            if "limit_per_position" not in params:
                params["limit_per_position"] = 5

            # Special handling for different query types
            latest_season = await asyncio.to_thread(get_latest_season, conn)
            if query_method == "get_player_season_stats" and "gameweek" in params:
                query_method = "get_player_gameweek_performance"
                if "season" not in params:
                    params["season"] = latest_season
            elif query_method == "get_player_season_stats" and "season" not in params:
                query_method = "get_player_all_seasons_stats"
            
            if query_method == "get_most_transferred_players":
                if "season" not in params:
                    params["season"] = latest_season
                if "gameweek" not in params:
                    params["gameweek"] = 1
                if "out" in request.question.lower():
                    params["direction"] = "out"
                else:
                    params["direction"] = "in"
            
            if query_method == "get_most_selected_players":
                if "season" not in params:
                    params["season"] = latest_season
                if "gameweek" not in params:
                    params["gameweek"] = 1
            
            # Execute query
            method = getattr(CypherQueries, query_method)
            sig = inspect.signature(method)
            
            default_required_season(method, params, latest_season)
            required_params = [
                p.name for p in sig.parameters.values() 
                if p.default == inspect.Parameter.empty
            ]
            missing_params = [p for p in required_params if p not in params]

            if missing_params:
                query, query_params = CypherQueries.get_top_players_all_positions(
                    season=params.get("season"),
                    gameweek=params.get("gameweek"),
                    limit_per_position=5
                )
                executed_method = "get_top_players_all_positions"
            else:
                valid_params = {k: v for k, v in params.items() if k in sig.parameters}
                query, query_params = method(**valid_params)
                executed_method = query_method

            results = await run_db(conn, query, query_params)
            # The whole squad, not the first 25 (forwards are listed last)
            cypher_context = PromptBuilder.format_kg_context(results, max_items=max(25, len(results)) if squad else 25)
            cypher_context = add_tie_note_if_needed(intent_result.intent, query_method, params, results, cypher_context)
            executed_query = query

        ml_context = None
        # Best XI: the optimiser's predicted squad (or a finished gameweek's dream team) is the context
        if intent_result.intent == Intent.TEAM_OF_THE_WEEK:
            team = await asyncio.to_thread(_team_of_week_for_question, conn, request.question, params.get("gameweek"))
            if team is not None:
                params.update(season=CURRENT_SEASON, gameweek=team["gameweek"], team_mode=team["mode"])
                results = season_service.team_of_week_rows(team)
                if team["mode"] == "predicted":
                    ml_context = season_service.team_of_week_context(team)
                else:
                    cypher_context = season_service.team_of_week_context(team)

        # Cards (photos, stats, predictions) come from structured data, not the answer text,
        # so they are built while the rest of the pipeline and the LLM run
        cards_task = asyncio.create_task(build_answer_cards(
            question=request.question, intent=intent_result.intent.value, method=executed_method,
            params=dict(params), entities=entities.to_dict(), results=list(results), conn=conn,
        ))

        # Step 5: ML Predictions
        predictions_df = get_predictions_df() if per_fixture else None
        if per_fixture and predictions_df is not None:
            per = fixture_picks.requested_count(request.question)
            ml_context = fixture_picks.context(
                fixture_picks.top_by_fixture(predictions_df.to_dict("records"), per), per)
        elif transfer is not None:
            ml_context = await asyncio.to_thread(_transfer_context, conn, transfer)
            cypher_context = "Not used for transfer questions: the Transfer Analysis below has this season's numbers."
        elif forecast:
            ml_context = await asyncio.to_thread(_forecast_context, conn, entities.players)
        if ml_context is None and intent_result.intent == Intent.PREDICTION:
            ml_integration = app_state.get("ml_integration")
            # The predictions file needs no model in memory; the legacy stack is only its fallback
            if get_predictions_df() is not None or (ml_integration and ml_integration.predictor_loaded):
                try:
                    # If a player was extracted, get single player prediction
                    if entities.players:
                        player_name = entities.players[0]
                        
                        # Try to get from CSV first (faster)
                        try:
                            df = get_predictions_df()
                            if df is not None:
                                player_pred = df[df['name_lower'] == player_name.lower()]
                                
                                if len(player_pred) > 0:
                                    row = player_pred.iloc[0]
                                    opponent = row['opponent']
                                    home_away = "Home" if row['is_home'] else "Away"
                                    ml_context = f"ML Prediction for {row['name']}:\n- Predicted points: {row['predicted_points']:.1f} pts\n- Next match: vs {opponent} ({home_away})\n- Gameweek: {int(row['gameweek'])}"
                                    ml_context += _availability_note(row)
                                else:
                                    # The file covers every player in the current FPL game, so
                                    # a miss means he isn't in it (left the league, retired)
                                    ml_context = (f"{player_name} is not in the current FPL game "
                                                  "(no longer at a Premier League club), so there is no prediction.")
                            else:
                                # No CSV, use Neo4j
                                pred_req = PlayerPredictionRequest(player_name=player_name)
                                pred = await ml_integration.predict_player_next_gameweek(pred_req)
                                ml_context = f"- {pred.player_name}: {pred.predicted_points:.1f} predicted points for the next gameweek"
                        except Exception as csv_err:
                            logger.warning(f"CSV prediction lookup failed, using Neo4j: {csv_err}")
                            pred_req = PlayerPredictionRequest(player_name=player_name)
                            pred = await ml_integration.predict_player_next_gameweek(pred_req)
                            ml_context = f"- {pred.player_name}: {pred.predicted_points:.1f} predicted points for the next gameweek"
                    else:
                        # Get top performers from CSV
                        try:
                            df = get_predictions_df()
                            if df is not None:
                                # Filter by position if specified
                                pos = entities.positions[0] if entities.positions else None
                                if pos:
                                    df = df[df['position'] == pos.upper()]
                                
                                ml_context = _top_predictions_context(df, 5)
                            else:
                                # Fallback to Neo4j
                                pos = entities.positions[0] if entities.positions else None
                                top_req = TopPerformersRequest(position=pos, top_k=5)
                                top_preds = await ml_integration.predict_top_performers(top_req)
                                ml_context = "Top predicted performers for the next gameweek:\n" + \
                                             "\n".join([f"- {p.player_name}: {p.predicted_points:.1f} pts" for p in top_preds.predictions])
                        except Exception as csv_err:
                            logger.warning(f"CSV top performers lookup failed, using Neo4j: {csv_err}")
                            pos = entities.positions[0] if entities.positions else None
                            top_req = TopPerformersRequest(position=pos, top_k=5)
                            top_preds = await ml_integration.predict_top_performers(top_req)
                            ml_context = "Top predicted performers for the next gameweek:\n" + \
                                         "\n".join([f"- {p.player_name}: {p.predicted_points:.1f} pts" for p in top_preds.predictions])
                except Exception as ml_err:
                    logger.error(f"ML prediction failed during query: {ml_err}")
                    import traceback
                    logger.error(traceback.format_exc())

        # Step 6: Embedding search
        embedding_context = ""
        embedding_used = False
        
        # A transfer answer rests on this season and the forecasts; similar players (often from
        # earlier seasons) only invited career numbers into it
        if request.retrieval_method in ["Embeddings", "Hybrid"] and transfer is None:
            current_manager = app_state["embedding_manager"]
            if not current_manager:
                # No manager yet — create a lazy one with prebuilt
                # Lazy import to avoid dependency issue
                from embeddings.embedding_manager import EmbeddingManager
                manager = EmbeddingManager.__new__(EmbeddingManager)
                manager.model_key = request.embedding_model
                manager.model_info = EmbeddingManager.MODELS[request.embedding_model]
                manager.model = None
                manager.player_embeddings = {}
                manager.player_metadata = {}
                prebuilt_path = f"embeddings/prebuilt/{request.embedding_model}_embeddings.pkl"
                manager.load_embeddings(prebuilt_path)
                app_state["embedding_manager"] = manager
                if manager.player_embeddings:
                    app_state["embeddings_built"] = True
            elif current_manager.model_key != request.embedding_model:
                # Switch to different prebuilt model without loading transformer
                from embeddings.embedding_manager import EmbeddingManager
                manager = EmbeddingManager.__new__(EmbeddingManager)
                manager.model_key = request.embedding_model
                manager.model_info = EmbeddingManager.MODELS[request.embedding_model]
                manager.model = None
                manager.player_embeddings = {}
                manager.player_metadata = {}
                prebuilt_path = f"embeddings/prebuilt/{request.embedding_model}_embeddings.pkl"
                manager.load_embeddings(prebuilt_path)
                app_state["embedding_manager"] = manager
                if manager.player_embeddings:
                    app_state["embeddings_built"] = True
            
            if app_state["embeddings_built"]:
                try:
                    key_player = None
                    if entities.players:
                        key_player = entities.players[0]
                    elif results and 'player_name' in results[0]:
                        key_player = results[0]['player_name']
                    
                    if key_player:
                        season = entities.seasons[0] if entities.seasons else await asyncio.to_thread(get_latest_season, conn)
                        similar_players = await asyncio.to_thread(
                            app_state["embedding_manager"].find_similar_to_player,
                            key_player, season=season, top_k=10, exclude_self=False
                        )
                    else:
                        similar_players = await asyncio.to_thread(
                            app_state["embedding_manager"].find_similar_players,
                            request.question, top_k=5
                        )
                    embedding_context = PromptBuilder.format_embedding_context(similar_players)
                    embedding_used = True
                except Exception as e:
                    print(f"Embedding search failed: {e}")

        prompt = None
        if app_state["llm_manager"] and app_state["llm_manager"].available:
            data_scope = f"the {params['season']} season" if "season" in params else f"all seasons ({', '.join(SEASONS)})"
            prompt = PromptTemplates.qa_template(
                question=request.question,
                kg_context=cypher_context,
                embedding_context=embedding_context if embedding_context else None,
                # A transfer question's analysis has its own section and answering rules
                ml_context=None if transfer is not None else ml_context,
                data_scope=data_scope,
                is_first_message=request.is_first_message,
                chat_history=request.chat_history,
                model_key=request.model,  # Pass model key for model-specific prompting
                transfer_context=ml_context if transfer is not None else None,
            )

        return PreparedAnswer(
            intent=intent_result.intent, query_method=query_method, params=params, entities=entities,
            results=results, executed_query=executed_query, cypher_context=cypher_context,
            ml_context=ml_context, embedding_context=embedding_context, embedding_used=embedding_used,
            prompt=prompt, cards_task=cards_task,
        )
    except BaseException:
        if cards_task is not None:
            cards_task.cancel()
        raise


@app.post("/api/query", response_model=QueryResponse, dependencies=[Depends(query_limiter.dependency)])
async def query_fpl(request: QueryRequest, conn=Depends(get_neo4j_conn)):
    """Process FPL query and return answer."""
    # Only return the initial greeting when this is the user's first message in the session
    if request.is_first_message and looks_like_small_talk(request.question):
        return greeting_response()
    prepared: Optional[PreparedAnswer] = None
    try:
        prepared = await _prepare_answer(request, conn)

        # Step 7: Generate LLM response
        text = ""
        if prepared.prompt is not None:
            response = await asyncio.to_thread(
                app_state["llm_manager"].generate,
                prepared.prompt,
                model_key=request.model,
                timeout=60
            )
            text = response.text if response.success else f"Error generating response: {response.error}"
        return await prepared.response(prepared.finish(text))

    except HTTPException:
        raise
    except Exception:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail="The assistant couldn't answer that right now. Please try again.")
    finally:
        if prepared is not None and not prepared.cards_task.done():
            prepared.cards_task.cancel()


ANSWER_STREAM_IDLE_SECONDS = 90
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, data: Any) -> str:
    """One server-sent event; `data` is JSON (already encoded when it is a str)."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_llm(llm: LLMManager, prompt: str, model_key: str, loop: asyncio.AbstractEventLoop,
                events: "asyncio.Queue", stop: ThreadEvent) -> None:
    """Thread body: feeds the LLM's streamed pieces into the event loop's queue."""
    def put(item) -> None:
        try:
            loop.call_soon_threadsafe(events.put_nowait, item)
        except RuntimeError:
            pass  # the loop has closed (server shutting down)

    try:
        for piece in llm.generate_stream(prompt, model_key=model_key, should_stop=stop.is_set):
            if stop.is_set():
                return
            put(("delta", piece))
        put(("end", None))
    except Exception as exc:
        put(("error", str(exc)))


async def _answer_events(request: QueryRequest, conn) -> AsyncIterator[str]:
    """The streamed answer: `cards` (with follow-ups) as soon as they are built, the answer
    in `delta` pieces as the LLM writes it, then `answer` (the final text, which can differ
    from the pieces after post-processing) and `done` (the full QueryResponse)."""
    if request.is_first_message and looks_like_small_talk(request.question):
        response = greeting_response()
        yield _sse("cards", {"cards": [], "follow_ups": response.follow_ups})
        yield _sse("answer", {"text": response.answer})
        yield _sse("done", response.model_dump_json())
        return

    prepared: Optional[PreparedAnswer] = None
    stop = ThreadEvent()
    try:
        prepared = await _prepare_answer(request, conn)
        events: asyncio.Queue = asyncio.Queue()
        prepared.cards_task.add_done_callback(lambda _task: events.put_nowait(("cards", None)))
        writing = prepared.prompt is not None
        if writing:
            Thread(
                target=_stream_llm,
                args=(app_state["llm_manager"], prepared.prompt, request.model, asyncio.get_running_loop(), events, stop),
                daemon=True,
            ).start()

        pieces: List[str] = []
        error: Optional[str] = None
        cards_sent = False
        while writing or not cards_sent:
            try:
                kind, value = await asyncio.wait_for(events.get(), timeout=ANSWER_STREAM_IDLE_SECONDS)
            except asyncio.TimeoutError:
                kind, value = "error", "the model stopped responding"
            if kind == "cards" and not cards_sent:
                cards_sent = True
                cards, follow_ups = prepared.cards_task.result()  # build_answer_cards never raises
                yield _sse("cards", {"cards": cards, "follow_ups": follow_ups})
            elif kind == "delta":
                pieces.append(value)
                yield _sse("delta", {"text": value})
            elif kind in ("end", "error"):
                # On an error the cards are still worth waiting for: they show the data anyway
                writing = False
                error = value if kind == "error" else None

        text = "".join(pieces).strip()
        if error:
            logger.error("Streamed answer failed: %s", error)
            text = f"{text}\n\n_(The answer was cut short.)_" if text else f"Error generating response: {error}"
        answer = prepared.finish(text)
        yield _sse("answer", {"text": answer})
        yield _sse("done", (await prepared.response(answer)).model_dump_json())
    except HTTPException as exc:
        yield _sse("error", {"detail": exc.detail})
    except Exception:
        logger.exception("Streamed query failed")
        yield _sse("error", {"detail": "The assistant couldn't answer that right now. Please try again."})
    finally:
        stop.set()
        if prepared is not None and not prepared.cards_task.done():
            prepared.cards_task.cancel()


@app.post("/api/query/stream", dependencies=[Depends(query_limiter.dependency)])
async def query_fpl_stream(request: QueryRequest, conn=Depends(get_neo4j_conn)):
    """POST /api/query as server-sent events (see _answer_events). Same request body and rate
    limit; a failure after the stream has started arrives as an `error` event."""
    return StreamingResponse(_answer_events(request, conn), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/embeddings/build", response_model=EmbeddingBuildResponse, dependencies=[Depends(require_admin)])
async def build_embeddings(request: EmbeddingBuildRequest, conn=Depends(get_neo4j_conn)):
    """Build player embeddings from Neo4j data."""
    try:
        with embedding_build_lock:
            if app_state["embedding_building"]:
                raise HTTPException(status_code=409, detail="Embedding build already in progress")

            app_state["embedding_building"] = True
            app_state["embedding_build_error"] = None

        # Initialize embedding manager if not already present
        if not app_state["embedding_manager"]:
            # Lazy import
            from embeddings.embedding_manager import EmbeddingManager
            app_state["embedding_manager"] = EmbeddingManager(model_key=request.model)

        build_thread = Thread(
            target=run_embedding_build,
            args=(request.model, conn),
            daemon=True,
            name=f"embedding-build-{request.model}",
        )
        build_thread.start()

        return EmbeddingBuildResponse(
            success=True,
            count=len(app_state["embedding_manager"].player_embeddings) if app_state["embedding_manager"] else 0,
            message=f"Embedding build started in the background for {request.model}. Refresh status to see progress.",
            started=True,
            building=True,
            model=request.model,
        )
        
    except Exception as e:
        app_state["embedding_building"] = False
        logger.error(f"Failed to start embedding build: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start embedding build: {str(e)}")


@app.get("/api/trivia/new", response_model=TriviaQuestion)
def get_trivia_question(difficulty: Optional[str] = None, conn=Depends(get_neo4j_conn)):
    """Generate a new trivia question, optionally filtered by difficulty."""
    try:
        trivia_gen = TriviaGenerator(conn)
        trivia_cache: TriviaQuestionCache = app_state["trivia_cache"]
        if not trivia_cache:
            raise HTTPException(status_code=503, detail="Trivia cache not initialized")

        # Map difficulty string to enum
        difficulty_enum = None
        if difficulty:
            try:
                difficulty_enum = Difficulty(difficulty.lower())
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid difficulty: {difficulty}. Use easy, medium, or hard.")

        # Avoid serving very recent duplicate questions.
        question = None
        max_attempts = 12
        for _ in range(max_attempts):
            candidate = trivia_gen.generate_question(difficulty=difficulty_enum)
            if not candidate:
                continue
            if not trivia_cache.is_recent(candidate.question):
                question = candidate
                break
            question = candidate  # fallback
        
        if not question:
            raise HTTPException(status_code=500, detail="Failed to generate question")

        trivia_cache.mark_recent(question.question)
        trivia_cache.store(question)
        
        return TriviaQuestion(
            question=question.question,
            options=question.options,
            category=question.category.value,
            difficulty=question.difficulty.value,
            question_id=question.question_id
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trivia/answer", response_model=TriviaAnswerResponse)
def check_trivia_answer(request: TriviaAnswerRequest, conn=Depends(get_neo4j_conn)):
    """Check trivia answer (simplified - store questions in session/cache in production)."""
    try:
        trivia_cache: TriviaQuestionCache = app_state["trivia_cache"]
        if not trivia_cache:
            raise HTTPException(status_code=503, detail="Trivia cache not initialized")

        stored_question = trivia_cache.get(request.question_id)
        if not stored_question:
            raise HTTPException(status_code=404, detail="Trivia question not found or expired")

        trivia_gen = TriviaGenerator(conn)
        correct, feedback = trivia_gen.check_answer(stored_question, request.answer)
        return TriviaAnswerResponse(
            correct=correct,
            feedback=feedback,
            correct_answer=stored_question.correct_answer if not correct else None
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class PlayerStatsRequest(BaseModel):
    player_name: str
    season: Optional[str] = None


@app.post("/api/players/stats")
async def get_player_stats(request: PlayerStatsRequest, conn=Depends(get_neo4j_conn)):
    """Fetch stats for a specific player, either for a single season or combined career."""
    try:
        index = await get_player_index_async(conn)
        player_name = index.resolve(request.player_name) or request.player_name

        if request.season:
            query, params = CypherQueries.get_player_season_stats(player_name, request.season)
        else:
            query, params = CypherQueries.get_player_career_stats(player_name)
        results, avatar = await asyncio.gather(run_db(conn, query, params), avatar_for(player_name))

        if not results:
            return {"stats": None}

        stats = dict(results[0])
        if stats.get("avg_ict") is not None:
            stats["avg_ict_index"] = round(float(stats["avg_ict"]), 2)
        stats["avatar"] = avatar
        return {"stats": stats}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _attach_predicted_points(rows: List[Dict[str, Any]]) -> None:
    """Add next-gameweek predicted points from the predictions file (v3).

    Players missing from the file aren't in the current FPL game and get none. The live
    v2 model is only an emergency fallback when no predictions file exists at all.
    """
    df = get_predictions_df()
    if df is not None:
        for row in rows:
            name = row.get("player_name")
            hit = df[df["name_lower"] == name.lower()] if name else None
            if hit is not None and len(hit):
                row["predicted_points"] = round(float(hit.iloc[0]["predicted_points"]), 2)
        return

    missing = [row for row in rows if row.get("player_name")]
    ml_integration = app_state.get("ml_integration")
    if not missing or not (ml_integration and ml_integration.predictor_loaded):
        return

    async def predict(row: Dict[str, Any]) -> None:
        try:
            pred = await ml_integration.predict_player_next_gameweek(
                PlayerPredictionRequest(player_name=row["player_name"])
            )
            row["predicted_points"] = pred.predicted_points
        except Exception:
            pass

    await asyncio.gather(*(predict(r) for r in missing))


@app.post("/api/players/search")
async def search_players(request: PlayerSearchRequest, conn=Depends(get_neo4j_conn)):
    """Search players by name: accent-insensitive, typo tolerant, popular players first."""
    try:
        index = await get_player_index_async(conn)
        results = index.search(request.query, request.limit)
        if not request.include_avatars:
            # Attach avatars that need no network call (FPL photos, cached lookups); the
            # frontend fetches the rest per row so results render immediately.
            service: Optional[AvatarService] = app_state["avatar_service"]
            if service is not None:
                for row in results:
                    row["avatar"] = service.peek(row["player_name"], row.get("team_name"))
            return {"players": results}

        results = await enrich_rows_with_avatars(results)
        await _attach_predicted_points(results)
        return {"players": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/players/compare")
async def compare_players(request: PlayerComparisonRequest, conn=Depends(get_neo4j_conn)):
    """Compare two players, resolving accented / partial names automatically."""
    try:
        index = await get_player_index_async(conn)
        p1 = index.resolve(request.player1) or request.player1
        p2 = index.resolve(request.player2) or request.player2
        query, params = CypherQueries.compare_players(p1, p2, request.season)
        results = await run_db(conn, query, params)
        return {"comparison": await enrich_rows_with_avatars(results)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/images/search", response_model=ImageSearchResponse)
async def search_image(query: str, conn=Depends(get_neo4j_conn)):
    """Return an avatar URL for a player name (or free text mentioning a player)."""
    text = repair_mojibake(query)
    resolved = text
    try:
        index = await get_player_index_async(conn)
        resolved = index.resolve(text) or next(iter(index.mentions(text, limit=1)), text)
    except HTTPException:
        raise
    except Exception:
        pass

    image_url = await avatar_for(resolved)
    if not image_url:
        source = None
    elif "premierleague.com" in image_url:
        source = "fpl_official"
    elif "wikimedia.org" in image_url:
        source = "wikipedia"
    else:
        source = "google_custom_search"
    return ImageSearchResponse(query=resolved, image_url=image_url, source=source)


@app.get("/api/images/proxy")
async def proxy_image(url: str):
    """
    Stream an allow-listed external image from the backend origin.
    Only known image hosts are fetched (no arbitrary URLs), and responses are
    cacheable by the browser/CDN for a week.
    """
    if not is_proxy_allowed(url):
        raise HTTPException(status_code=400, detail="Image host not allowed")
    proxy: Optional[ImageProxy] = app_state["image_proxy"]
    if proxy is None:
        raise HTTPException(status_code=503, detail="Image proxy not ready")

    try:
        content, content_type = await proxy.fetch(url)
    except PermissionError:
        raise HTTPException(status_code=400, detail="Image redirected to a host that is not allowed")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 429:
            raise HTTPException(status_code=503, detail="Image source is rate-limited. Try again in a few moments.",
                                headers={"Retry-After": "60"})
        if status in (403, 404):
            raise HTTPException(status_code=status, detail="Image not available at source")
        raise HTTPException(status_code=502, detail=f"Image proxy failed: HTTP {status}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Image source timed out")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image proxy failed: {exc}")

    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.post("/api/admin/reload", dependencies=[Depends(require_admin)])
async def admin_reload(conn=Depends(get_neo4j_conn)):
    """Rebuild graph-derived caches after the daily pipeline updated the graph.

    Disabled unless ADMIN_TOKEN is set; callers send it in the X-Admin-Token header.
    Prediction files are picked up automatically by mtime and need no reload.
    """
    started = time.time()
    reset_graph_caches()
    _health_stats_cache["at"] = 0.0
    index = await asyncio.to_thread(get_player_index, conn)
    if app_state["entity_extractor"]:
        app_state["entity_extractor"].set_known_players({e.player_name for e in index.entries})
    if app_state["avatar_service"]:
        await app_state["avatar_service"].refresh_fpl_codes()
    latest = await asyncio.to_thread(get_latest_season, conn)
    return {"players_indexed": len(index), "latest_season": latest, "seconds": round(time.time() - started, 2)}


@app.post("/api/data/load", dependencies=[Depends(require_admin)])
async def load_fpl_data(request: DataLoadRequest, conn=Depends(get_neo4j_conn)):
    """Load FPL data into Neo4j."""
    try:
        # Takes minutes; run it off the event loop so the API stays responsive
        loader = FPLDataLoader(conn)
        stats = await asyncio.to_thread(loader.load_all, DATA_PATH, request.clear_existing)
        # A wipe drops the current season; re-apply it from data/live when present
        from pipeline.load_current import has_live_data, load_current_season
        if has_live_data():
            await asyncio.to_thread(load_current_season, conn)
            stats = await asyncio.to_thread(conn.get_database_stats)

        reset_graph_caches()
        _health_stats_cache["at"] = 0.0
        index = await asyncio.to_thread(get_player_index, conn)
        if app_state["entity_extractor"]:
            app_state["entity_extractor"].set_known_players({e.player_name for e in index.entries})
        
        return {"success": True, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/database")
def get_database_stats(conn=Depends(get_neo4j_conn)):
    """Get Neo4j database statistics."""
    try:
        stats = conn.get_database_stats()
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Fixture Management Endpoints
# ============================================================================

@app.post("/api/fixtures/load", response_model=FixtureLoadResponse, dependencies=[Depends(require_admin)])
def load_fixtures(request: FixtureLoadRequest, conn=Depends(get_neo4j_conn)):
    """
    Load fixture data from CSV file into Neo4j.
    
    This endpoint is admin-only and requires a valid CSV file with fixture schedule data.
    
    Request Body:
    - csv_path: Path to the CSV file containing fixture data
    - season_id: Season identifier (e.g., "2026-27")
    - clear_existing: Whether to clear existing fixtures for the season (default: False)
    
    CSV Format:
    - Required columns: Matchday, Home Team, Away Team, Date
    - Optional columns: Time (defaults to 15:00 if not provided)
    
    Returns:
    - success: Whether the operation succeeded
    - fixtures_loaded: Number of fixtures successfully loaded
    - message: Descriptive message about the operation
    """
    try:
        # Get ML integration to access fixture service
        ml_integration = app_state.get("ml_integration")
        if not ml_integration:
            raise HTTPException(
                status_code=503,
                detail="Fixture service not initialized"
            )
        
        fixture_service = ml_integration.fixture_service
        
        # Validate CSV file exists
        if not os.path.exists(request.csv_path):
            raise HTTPException(
                status_code=400,
                detail=f"CSV file not found: {request.csv_path}"
            )
        
        # Optional: Clear existing fixtures for the season if requested
        if request.clear_existing:
            logger.info(f"Clearing existing fixtures for season {request.season_id}")
            # Only CSV-keyed schedule nodes: FPL-keyed fixtures (pipeline.load_current) carry
            # the season's PLAYED_IN rows and must survive a CSV reload
            clear_query = """
            MATCH (f:Fixture {season_id: $season_id})
            WHERE f.id STARTS WITH 'fixture-'
            DETACH DELETE f
            """
            conn.execute_write(clear_query, {"season_id": request.season_id})
        
        # Load fixtures from CSV
        logger.info(f"Loading fixtures from {request.csv_path} for season {request.season_id}")
        fixtures_loaded = fixture_service.load_fixtures_from_csv(
            csv_path=request.csv_path,
            season_id=request.season_id
        )
        
        return FixtureLoadResponse(
            success=True,
            fixtures_loaded=fixtures_loaded,
            message=f"Successfully loaded {fixtures_loaded} fixtures for season {request.season_id}"
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        # Handle validation errors from fixture service
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to load fixtures: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load fixtures: {str(e)}")


def _serialize_fixture(row: Dict[str, Any]) -> Dict[str, Any]:
    """Neo4j returns its own DateTime type; convert to an ISO string for JSON."""
    fixture = dict(row)
    match_date = fixture.get("match_date")
    if hasattr(match_date, "to_native"):
        match_date = match_date.to_native()
    if isinstance(match_date, datetime):
        fixture["match_date"] = match_date.isoformat()
    return fixture


@app.get("/api/fixtures/next", response_model=NextFixtureResponse)
def get_next_fixtures(team: Optional[str] = None, conn=Depends(get_neo4j_conn)):
    """
    Get upcoming fixtures for all teams or a specific team.
    
    Query Parameters:
    - team (optional): Team name to filter fixtures for a specific team
    
    Returns:
    - fixtures: List of upcoming fixtures with home_team, away_team, match_date, and matchday
    
    If a team is specified, returns only fixtures for that team.
    If no team is specified, returns the next fixture for each team in the league.
    """
    try:
        # Get ML integration to access fixture service
        ml_integration = app_state.get("ml_integration")
        if not ml_integration:
            raise HTTPException(
                status_code=503,
                detail="Fixture service not initialized"
            )
        
        fixture_service = ml_integration.fixture_service
        current_date = datetime.now(timezone.utc)
        
        if team:
            # Get next fixture for specific team
            fixture_context = fixture_service.get_team_next_match(team, current_date)
            
            if not fixture_context:
                return NextFixtureResponse(fixtures=[])
            
            # Query to get full fixture details including both teams
            fixture_query = """
            MATCH (f:Fixture {id: $fixture_id})
            MATCH (f)-[:PLAYS_HOME]->(home:Team)
            MATCH (f)-[:PLAYS_AWAY]->(away:Team)
            RETURN home.name AS home_team,
                   away.name AS away_team,
                   f.kickoff_time AS match_date,
                   f.matchday AS matchday
            """
            results = conn.execute_query(
                fixture_query,
                {"fixture_id": fixture_context.fixture_id}
            )
            
            if not results:
                return NextFixtureResponse(fixtures=[])
            
            return NextFixtureResponse(fixtures=[_serialize_fixture(results[0])])
        
        else:
            # Next fixture for every team in one query (was two queries per team)
            all_query = """
            MATCH (t:Team)<-[:PLAYS_HOME|PLAYS_AWAY]-(f:Fixture)
            WHERE f.kickoff_time > $current_date
            WITH t, f ORDER BY f.kickoff_time ASC
            WITH t, collect(f)[0] AS f
            WITH DISTINCT f
            MATCH (f)-[:PLAYS_HOME]->(home:Team)
            MATCH (f)-[:PLAYS_AWAY]->(away:Team)
            RETURN home.name AS home_team,
                   away.name AS away_team,
                   f.kickoff_time AS match_date,
                   f.matchday AS matchday
            ORDER BY match_date
            """
            rows = conn.execute_query(all_query, {"current_date": current_date})
            fixtures = [_serialize_fixture(row) for row in rows]
            return NextFixtureResponse(fixtures=fixtures)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get next fixtures: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get next fixtures: {str(e)}")


# ============================================================================
# Season dashboard (read-only; logic and caching in services/season_service.py)
# ============================================================================

def _photo_for(name: str, team: Optional[str], fpl_code: Optional[int]) -> Optional[str]:
    """Photo URL without a network call: the avatar cache/FPL feed, else the graph's FPL code."""
    service: Optional[AvatarService] = app_state["avatar_service"]
    if service:
        return service.peek(name, team, fpl_code)
    return FPL_PHOTO_URL.format(code=fpl_code) if fpl_code else None


def _season_call(fn, *args, **kwargs):
    """Map season_service errors: unknown gameweek -> 404, Neo4j down -> 503."""
    from neo4j.exceptions import DriverError
    try:
        return fn(*args, **kwargs)
    except season_service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DriverError as exc:
        logger.error(f"Neo4j unavailable: {exc}")
        raise HTTPException(status_code=503, detail="Neo4j unavailable, try again shortly")


@app.get("/api/season/current", response_model=SeasonCurrentResponse)
def season_current(conn=Depends(get_neo4j_conn)):
    """Current/next gameweek, next deadline and finished gameweeks of the live season."""
    return _season_call(season_service.get_season_current, conn)


@app.get("/api/fixtures", response_model=FixturesResponse)
def season_fixtures(gameweek: Optional[int] = None, conn=Depends(get_neo4j_conn)):
    """One gameweek's fixtures (default: next, else current gameweek)."""
    return _season_call(season_service.get_fixtures, conn, gameweek)


@app.get("/api/table", response_model=TableResponse)
def season_table(conn=Depends(get_neo4j_conn)):
    """League table computed from the season's finished fixtures."""
    return _season_call(season_service.get_table, conn)


@app.get("/api/injuries", response_model=InjuriesResponse)
def season_injuries(team: Optional[str] = None, conn=Depends(get_neo4j_conn)):
    """Current-squad players flagged by FPL (injured, suspended, doubtful, ...)."""
    return _season_call(season_service.get_injuries, conn, team, photo_for=_photo_for)


@app.get("/api/team-of-the-week", response_model=TeamOfWeekResponse)
def team_of_the_week(mode: str = "predicted", gameweek: Optional[int] = None):
    """Best FPL squad for the next gameweek by predicted points (budget 100.0m, max 3
    per club), or the best XI of a finished gameweek by actual points (mode=actual)."""
    if mode not in ("predicted", "actual"):
        raise HTTPException(status_code=422, detail="mode must be 'predicted' or 'actual'")
    conn = app_state["neo4j_conn"]
    if conn is None and mode == "actual":
        raise HTTPException(status_code=503, detail="Neo4j not connected")
    return _season_call(season_service.get_team_of_the_week, conn, mode, gameweek, photo_for=_photo_for)


@app.get("/api/model-review", response_model=ModelReviewResponse, response_model_by_alias=True)
def model_review_endpoint(gameweek: Optional[int] = Query(None, ge=1, le=38)):
    """The model's squad saved before a past gameweek's deadline, scored like FPL (auto-subs,
    captain/vice), with FPL's average and highest score. Default: the latest saved pick
    whose deadline has passed. 404 when there's none."""
    return _season_call(model_review.get_model_review, gameweek, photo_for=_photo_for, live=_live_points())


def _live_points():
    """FPL's live points for a gameweek being played (services/live_points.py, cached a
    minute); LIVE_POINTS=0 turns them off, leaving the pipeline's season CSV."""
    if os.getenv("LIVE_POINTS", "1").strip() == "0":
        return None
    from services import live_points
    return live_points.gameweek_live


@app.get("/api/ml-manager")
def ml_manager_endpoint():
    """The season-long team the points model manages under FPL's rules from GW9 on: the
    current (planned, locked or scored) gameweek, chips, history and season totals, read
    from $LIVE_DATA_DIR/ml_manager (logic in services/fpl_manager.py). 404 until the
    manager exists."""
    import importlib

    # Imported per call (sys.modules makes that cheap), so the app still starts without it.
    try:
        fpl_manager = importlib.import_module("services.fpl_manager")
    except ImportError as exc:
        logger.error(f"ML manager unavailable: {exc}")
        raise HTTPException(status_code=503, detail="The ML manager isn't available")
    not_found = (season_service.NotFoundError, getattr(fpl_manager, "NotFoundError", season_service.NotFoundError))
    try:
        return fpl_manager.get_manager_view(photo_for=_photo_for, live=_live_points())
    except not_found as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "The ML manager hasn't started yet")


@app.get("/api/clubs")
def clubs(season: str = Query(CURRENT_SEASON, max_length=16), conn=Depends(get_neo4j_conn)):
    """The clubs with players in a season, A-Z, for the tables' club filters."""
    return _season_call(season_service.get_clubs, conn, season)


def _predicted_by_name() -> Dict[str, float]:
    """Next gameweek's predicted points by player name (empty without a predictions file)."""
    df = get_predictions_df()
    if df is None or not len(df):
        return {}
    return {str(n): round(float(v), 1) for n, v in df.groupby("name")["predicted_points"].sum().items()}


@app.get("/api/teams")
def teams_index(season: str = Query(CURRENT_SEASON, max_length=16), conn=Depends(get_neo4j_conn)):
    """Every club of a season with its table row and slug (services/teams.py)."""
    from services import teams
    try:
        return _season_call(teams.teams_index, conn, season)
    except teams.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/teams/{slug}")
def team_detail(slug: str, season: str = Query(CURRENT_SEASON, max_length=16), conn=Depends(get_neo4j_conn)):
    """One club's season: standing, form, results and fixtures (with match ids), position by
    gameweek, goals and xG, squad and top scorers (services/teams.py). Unknown club -> 404."""
    from services import teams
    if not re.fullmatch(r"[a-z0-9-]{2,40}", slug):
        raise HTTPException(status_code=404, detail="No such club")
    try:
        return _season_call(teams.team_page, conn, slug, season, photo_for=_photo_for,
                            predicted_for=_predicted_by_name)
    except teams.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/matches/{fixture_id}")
def match_detail(fixture_id: str, conn=Depends(get_neo4j_conn)):
    """One match of any season (services/matches.py): score, scorers, cards, bonus, xG and
    line-ups with FPL points once played (FPL's live points while it's on); before kickoff,
    form, last meetings and predicted players. Unknown fixture -> 404."""
    from services import matches
    if not re.fullmatch(r"[A-Za-z0-9-]{3,40}", fixture_id):
        raise HTTPException(status_code=404, detail="No such match")
    try:
        return _season_call(matches.match_page, conn, fixture_id, photo_for=_photo_for,
                            predictions=get_predictions_df, live=_live_points())
    except matches.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/news")
def news(team: Optional[str] = Query(None, max_length=40)):
    """Guardian football headlines (GUARDIAN_API_KEY; cached, with a daily budget and the last
    good answer as fallback) and FPL news from our own data: availability updates and price
    changes (services/news.py). `team` is a club's graph name."""
    from services import news as news_service
    return news_service.get_news(team, photo_for=_photo_for)


@app.get("/api/players/leaders", response_model=PlayerLeadersResponse)
def player_leaders(
    season: str = Query(CURRENT_SEASON, max_length=16),
    position: Optional[LeaderPosition] = None,
    sort: LeaderSort = "total_points",
    limit: int = Query(50, ge=1, le=100),
    team: Optional[str] = Query(None, max_length=40),
    max_price: Optional[float] = Query(None, ge=3.0, le=20.0),
    conn=Depends(get_neo4j_conn),
):
    """Season leaders table from SEASON_STATS: one row per player, optionally one
    position (that season's), one club and a highest average price (GBP m), sorted by
    `sort` then total points. Availability (status/news) is only filled for the current
    season. Unknown season -> 404."""
    return _season_call(season_service.get_player_leaders, conn, season, position, sort, limit,
                        photo_for=_photo_for, team=team, max_price=max_price)


def _predicted_points_for(name: str) -> Optional[float]:
    """Next gameweek's predicted points from the predictions file (None when he isn't in it)."""
    df = get_predictions_df()
    if df is None or not name:
        return None
    hit = df[df["name_lower"] == name.lower()]
    return round(float(hit["predicted_points"].sum()), 2) if len(hit) else None


@app.get("/api/players/profile")
def player_profile(
    name: str = Query(..., min_length=2, max_length=100),
    season: Optional[str] = Query(None, max_length=16),
    conn=Depends(get_neo4j_conn),
):
    """One player's season (or career, without `season`) from his PLAYED_IN rows: totals,
    per-90 rates, where his points came from, and points by gameweek (or by season). Logic in
    services/player_profile.py. Unknown player or season -> 404."""
    from services import player_profile as pp

    index = app_state.get("player_index")
    resolved = (index.resolve(name) if index else None) or name
    try:
        return _season_call(pp.get_player_profile, conn, resolved, season, photo_for=_photo_for,
                            predicted_for=_predicted_points_for)
    except pp.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ============================================================================
# Model explainability (SHAP + LIME over the saved model inputs; logic in services/explain.py)
# ============================================================================

def _explain_call(fn, *args):
    try:
        return fn(*args)
    except explain.ExplainUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/ml/explain/summary")
def ml_explain_summary():
    """Global SHAP view of the next gameweek's predictions: mean |SHAP| per feature and group,
    a beeswarm sample of the top features, the model's facts and test metrics. 503 until the
    pipeline has written the model inputs."""
    return _explain_call(explain.summary)


@app.get("/api/ml/explain/player")
def ml_explain_player(element: Optional[int] = Query(None, ge=1),
                      name: Optional[str] = Query(None, min_length=2, max_length=100)):
    """SHAP waterfall and LIME surrogate for each of one player's fixtures, plus the steps from
    the model's output to the site's predicted points. By FPL element id, or by name."""
    if element is None:
        if not name:
            raise HTTPException(status_code=422, detail="Pass element or name")
        element = _explain_call(explain.find_element, name)
        index = app_state.get("player_index")
        if element is None and index is not None:
            resolved = index.resolve(name)
            element = _explain_call(explain.find_element, resolved) if resolved else None
        if element is None:  # e.g. a surname, when it names one player only
            hits = _explain_call(explain.search, name, 2)
            element = hits[0]["element"] if len(hits) == 1 else None
        if element is None:
            raise HTTPException(status_code=404, detail=f"{name} isn't in this gameweek's predictions")
    try:
        return _explain_call(explain.player, element)
    except KeyError:
        raise HTTPException(status_code=404, detail="That player isn't in this gameweek's predictions")


@app.get("/api/ml/explain/dependence")
def ml_explain_dependence(feature: str = Query(..., max_length=64)):
    """Every upcoming row's value of one feature against its SHAP value."""
    try:
        return _explain_call(explain.dependence, feature)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown feature: {feature}")


@app.get("/api/ml/explain/players")
def ml_explain_players(q: str = Query("", max_length=100), limit: int = Query(20, ge=1, le=50)):
    """Players in this gameweek's model inputs whose name contains q, by predicted points."""
    return {"players": _explain_call(explain.search, q, limit)}


# ============================================================================
# Page-view analytics (aggregate counts only; logic in services/analytics.py)
# ============================================================================

class PageViewRequest(BaseModel):
    path: str = Field(max_length=200)
    referrer: Optional[str] = Field(default=None, max_length=500)
    # Sent only by visitors who accepted the consent banner (ignored unless consented is true):
    # the browser's IANA timezone and its local-storage flags for a first visit ever / of the day
    consented: bool = False
    timezone: Optional[str] = Field(default=None, max_length=64)
    new_visitor: bool = False
    first_today: bool = False
    # The owner's own browser (the /stats page's "this browser is me"): counted apart
    owner: bool = False
    # False: only the visitor flags (a visitor accepting on a page whose view was already counted)
    view: bool = True


class AnalyticsDay(BaseModel):
    date: str
    views: int
    visitors: int
    owner_views: int


class AnalyticsPath(BaseModel):
    path: str
    views: int


class AnalyticsReferrer(BaseModel):
    host: str
    views: int


class AnalyticsCountry(BaseModel):
    code: Optional[str]
    country: str
    visitors: int


class AnalyticsZone(BaseModel):
    zone: str
    visitors: int


class AnalyticsSummaryResponse(BaseModel):
    days: int
    total_views: int
    consented_views: int
    visitors: int
    new_visitors: int
    owner_views: int
    by_day: List[AnalyticsDay]
    top_paths: List[AnalyticsPath]
    top_referrers: List[AnalyticsReferrer]
    top_countries: List[AnalyticsCountry]
    top_zones: List[AnalyticsZone]


pageview_limiter = RateLimiter(limits_from_env("PAGEVIEW_RATE_LIMITS", "60/60,600/3600"))


@app.post("/api/analytics/pageview", status_code=204,
          dependencies=[Depends(pageview_limiter.dependency)])
def record_pageview(body: PageViewRequest, request: Request):
    """Count one page view (sent by the frontend for every route change).

    Stores no IP, user agent or identifier: the normalised path and the external referring host
    are added to that day's totals, and with consent the browser's timezone and visitor flags.
    """
    analytics.record_pageview(body.path, body.referrer, own_host=request.headers.get("host"),
                              consented=body.consented, raw_zone=body.timezone, new_visitor=body.new_visitor,
                              first_today=body.first_today, owner=body.owner, view=body.view)
    return Response(status_code=204)


@app.get("/api/admin/analytics", response_model=AnalyticsSummaryResponse,
         dependencies=[Depends(require_admin)])
def admin_analytics(days: int = Query(30, ge=1, le=400)):
    """Page views over the last `days` UTC days: daily views, visitors and owner views (zero-filled,
    oldest first), totals, and the top 20 paths, external referrers, countries and timezones."""
    return analytics.get_store().summary(days)


# ============================================================================
# WebSocket for Real-time Updates (Optional)
# ============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket for real-time query streaming."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            # Process query and stream response
            await websocket.send_text(f"Echo: {data}")
    except WebSocketDisconnect:
        print("Client disconnected")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)