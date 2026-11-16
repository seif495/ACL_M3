"""
API Integration for ML Predictions
Extends the FastAPI backend with ML prediction endpoints
"""
from fastapi import HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple
from collections import defaultdict
from datetime import datetime, timezone
import asyncio
import logging
import os

from ml.predictor import FPLPredictor, PlayerPrediction
from services.fixture_service import FixtureService, FixtureContext
from services.cache import PredictionCache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared Cypher fragments
# ---------------------------------------------------------------------------
# Candidate players: current-season squad members (PLAYS_FOR) of teams that have
# at least one upcoming 2026-27 fixture after $current_date. The team set is
# computed once (a few hundred future fixtures -> ~20 teams) and players are
# expanded from those teams, instead of probing the future fixtures once per player.
_UPCOMING_TEAM_PLAYERS = """
MATCH (uf:Fixture)-[:PLAYS_HOME|PLAYS_AWAY]->(team:Team)
WHERE uf.season_id = "2026-27" AND uf.kickoff_time > $current_date
WITH DISTINCT team
MATCH (team)<-[:PLAYS_FOR]-(p:Player)-[:PLAYS_POSITION]->(pos:Position)
"""

# Season restriction via the Fixture -> Gameweek -> Season path (same semantics as
# the original `(f)-[:PART_OF]->(gw:Gameweek)-[:IN_SEASON]->(s:Season) WHERE s.id = $season`).
_SEASON_PATH = "-[:PART_OF]->(:Gameweek)-[:IN_SEASON]->(:Season {id: $season})"


def _recent_perfs_subquery(season: Optional[str]) -> str:
    """Per-player top-4 most recent PLAYED_IN rels with minutes > 0.

    A COLLECT {} subquery sorts and limits each player's own matches (Top-k per
    player) instead of globally sorting every match row of every player.
    """
    season_path = _SEASON_PATH if season else ""
    return f"""COLLECT {{
    MATCH (p)-[r:PLAYED_IN]->(f:Fixture){season_path}
    WHERE r.minutes > 0
    WITH r, f
    ORDER BY f.kickoff_time DESC
    LIMIT 4
    RETURN r
}}"""


# Pydantic models for API
class PlayerPredictionRequest(BaseModel):
    """Request for single player prediction."""
    player_name: str
    player_data: Dict[str, Any] = {}  # Optional; fetched from Neo4j if empty


class TopPerformersRequest(BaseModel):
    """Request for top performers prediction."""
    position: Optional[str] = None
    top_k: int = Field(default=10, ge=1, le=50)
    season: Optional[str] = None


class BestValueRequest(BaseModel):
    """Request for best value players."""
    position: Optional[str] = None
    max_price: Optional[float] = None
    top_k: int = Field(default=10, ge=1, le=50)


class PredictionResponse(BaseModel):
    """Response for predictions."""
    player_name: str
    predicted_points: float
    confidence_interval: Optional[List[float]] = None
    # {low, high, p_return, p_haul}: what players predicted this much scored (services/prediction_ranges.py)
    usual_range: Optional[Dict[str, float]] = None
    features_used: Optional[Dict[str, Any]] = None
    model_version: str = "v1"
    fixture_context: Optional[Dict[str, Any]] = None  # NEW: Fixture context for date-aware predictions
    team_name: Optional[str] = None  # Player's team name


class TopPerformersResponse(BaseModel):
    """Response for top performers."""
    predictions: List[PredictionResponse]
    metadata: Dict[str, Any]


class BestValueResponse(BaseModel):
    """Response for best value players."""
    predictions: List[Dict[str, Any]]
    metadata: Dict[str, Any]


class MLAPIIntegration:
    """
    Integration layer for ML predictions in the API.
    Manages predictor lifecycle and query execution.
    """
    
    def __init__(self, neo4j_conn, query_executor, fixture_service: Optional[FixtureService] = None, cache: Optional[PredictionCache] = None):
        """
        Initialize ML API integration.
        
        Args:
            neo4j_conn: Neo4j connection instance
            query_executor: Query executor for fetching player data
            fixture_service: FixtureService for determining next matches (optional, created if not provided)
            cache: PredictionCache for caching predictions (optional, created if not provided)
        """
        self.neo4j_conn = neo4j_conn
        self.query_executor = query_executor
        self.fixture_service = fixture_service or FixtureService(neo4j_conn)
        self.cache = cache or PredictionCache()
        self.predictor = None
        self.predictors = {}
        self.predictor_loaded = False
        
    def load_predictor(self, model_path: str = "ml/models/xgboost_mid_v2.pkl", 
                      enable_promoted_teams_v2: bool = True):
        """
        Load ML predictor on API startup.
        
        Args:
            model_path: Path to trained model (defaults to v2 XGBoost model)
            enable_promoted_teams_v2: Enable promoted teams handler v2 with translation models
        """
        try:
            self.predictor = FPLPredictor(
                model_path=model_path, 
                model_type="xgboost",
                enable_promoted_teams_handler=True,
                enable_minutes_models=True
            )
            
            # Try to upgrade to v2 handler if available
            if enable_promoted_teams_v2:
                try:
                    if os.path.exists('ml/models/promoted_archetypes_v2.pkl'):
                        from ml.promoted_teams_handler_v2 import PromotedTeamsHandlerV2
                        self.predictor.promoted_teams_handler = PromotedTeamsHandlerV2()
                        logger.info("✅ Upgraded to promoted teams handler v2 (translation models)")
                except Exception as e:
                    logger.warning(f"Could not load v2 handler: {e}. Using v1 handler.")
            
            self.predictor_loaded = True
            logger.info("✅ ML Predictor loaded successfully (v2 XGBoost)")
        except Exception as e:
            logger.error(f"❌ Failed to load ML predictor: {e}")
            logger.warning("ML prediction endpoints will return errors")

    def load_predictors_by_position(self, model_paths: dict = None, 
                                    enable_promoted_teams_v2: bool = True):
        """
        Load multiple XGBoost v2 predictors by position.
        
        Args:
            model_paths: Optional dict of {position: model_path}. If None, uses v2 defaults.
            enable_promoted_teams_v2: Enable promoted teams handler v2 with translation models
        """
        # Default to v2 models if no paths provided
        if model_paths is None:
            model_paths = {
                'GK': 'ml/models/xgboost_gk_v2.pkl',
                'DEF': 'ml/models/xgboost_def_v2.pkl',
                'MID': 'ml/models/xgboost_mid_v2.pkl',
                'FWD': 'ml/models/xgboost_fwd_v2.pkl'
            }
        
        try:
            # The v2 handler is read-only after construction (archetype DB + two
            # translation models), so load it once and share it across positions
            # instead of unpickling the same files once per position.
            shared_v2_handler = None
            if enable_promoted_teams_v2:
                try:
                    if os.path.exists('ml/models/promoted_archetypes_v2.pkl'):
                        from ml.promoted_teams_handler_v2 import PromotedTeamsHandlerV2
                        shared_v2_handler = PromotedTeamsHandlerV2()
                except Exception as e:
                    logger.warning(f"Could not load v2 handler: {e}")

            for pos, path in model_paths.items():
                self.predictors[pos] = FPLPredictor(
                    model_path=path,
                    model_type="xgboost",
                    enable_promoted_teams_handler=True,
                    enable_minutes_models=True
                )

                # Upgrade to the (shared) v2 handler if available
                if shared_v2_handler is not None:
                    self.predictors[pos].promoted_teams_handler = shared_v2_handler

                logger.info(f"✅ Loaded {pos} model: {path}")
            
            self.predictor_loaded = len(self.predictors) > 0
            if self.predictor_loaded:
                logger.info(f"✅ XGBoost v2 Predictors loaded for positions: {list(self.predictors.keys())}")
                if enable_promoted_teams_v2:
                    logger.info("✅ Promoted teams handler v2 enabled (translation models + expanded archetypes)")
        except Exception as e:
            logger.error(f"❌ Failed to load XGBoost predictors: {e}")
            self.predictor_loaded = False
    
    def _check_predictor(self):
        """Ensure predictor is loaded."""
        if not self.predictor_loaded:
            raise HTTPException(
                status_code=503,
                detail="ML predictor not loaded. Train a model first."
            )
    
    async def get_player_data_for_prediction(
        self,
        player_name: str,
        season: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch player data from Neo4j for prediction.

        Stats are stored as properties on the :PLAYED_IN relationship.
        Position is stored on the :PLAYS_POSITION -> Position node.

        The blocking Neo4j call runs in a worker thread so it does not stall
        the event loop.
        """
        return await asyncio.to_thread(self._fetch_player_data, player_name, season)

    def _fetch_player_data(
        self,
        player_name: str,
        season: Optional[str] = None
    ) -> Dict[str, Any]:
        """Synchronous body of get_player_data_for_prediction."""
        # Only traverse Fixture -> Gameweek -> Season when a season filter is
        # requested; every PLAYED_IN fixture has exactly one such path, so the
        # unfiltered result is identical without the three extra hops per match.
        season_path = _SEASON_PATH if season else ""
        query = f"""
        MATCH (p:Player {{name: $player_name}})-[:PLAYS_POSITION]->(pos:Position)
        MATCH (p)-[r:PLAYED_IN]->(f:Fixture){season_path}
        WHERE r.minutes > 0
        WITH p, pos, r, f
        ORDER BY f.kickoff_time DESC
        LIMIT 4
        RETURN
            p.name                  AS name,
            pos.code                AS position,
            AVG(r.minutes)          AS minutes,
            AVG(r.goals_scored)     AS goals_scored,
            AVG(r.assists)          AS assists,
            AVG(r.total_points)     AS form,
            AVG(r.bps)              AS bps,
            AVG(r.ict_index)        AS ict_index,
            AVG(r.influence)        AS influence,
            AVG(r.creativity)       AS creativity,
            AVG(r.threat)           AS threat,
            SUM(r.clean_sheets)     AS clean_sheets,
            SUM(r.bonus)            AS bonus,
            SUM(r.goals_conceded)   AS goals_conceded,
            SUM(r.saves)            AS saves,
            SUM(r.yellow_cards)     AS yellow_cards,
            SUM(r.red_cards)        AS red_cards,
            AVG(r.value)            AS value
        """

        results = self.neo4j_conn.execute_query(
            query,
            {"player_name": player_name, "season": season}
        )

        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"Player '{player_name}' not found or no recent data available"
            )

        return results[0]
    
    @staticmethod
    def _prepare_for_inference(player_data: dict) -> dict:
        """
        Fill in columns that the FeatureEngineer pipeline expects but that
        are not returned by the aggregated Neo4j query.

        The feature engineer was designed for per-gameweek rows; during
        inference we feed a single aggregated row, so we just set sentinel
        values so that sort/groupby operations don't crash.
        """
        defaults = {
            "kickoff_time": "2000-01-01 00:00:00",  # arbitrary past date for sorting
            "was_home": 0,
            "GW": 19,                                # mid-season default
            "total_points": player_data.get("form", 0),  # form already is rolling avg
        }
        return {**defaults, **player_data}
    
    async def _get_player_stats_with_fixture(
        self,
        player_name: str,
        fixture_context: Optional[FixtureContext],
        season: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get player data enriched with fixture context.
        
        Args:
            player_name: Player's full name
            fixture_context: Next match fixture context (can be None)
            season: Season identifier (optional)
        
        Returns:
            Dictionary with player stats enriched with fixture context
        """
        # Get base player stats
        player_data = await self.get_player_data_for_prediction(player_name, season)
        
        # Enrich with fixture context if available
        if fixture_context is not None:
            player_data["opponent_team"] = fixture_context.opponent_team
            player_data["was_home"] = 1 if fixture_context.home_away == "home" else 0
            player_data["fixture_id"] = fixture_context.fixture_id
            player_data["matchday"] = fixture_context.matchday
            
            logger.debug(
                f"Enriched player data with fixture context: "
                f"opponent={fixture_context.opponent_team}, "
                f"home_away={fixture_context.home_away}"
            )
        else:
            # No fixture context - use defaults
            player_data["opponent_team"] = None
            player_data["was_home"] = 0
            player_data["fixture_id"] = None
            player_data["matchday"] = None
            
            logger.debug(f"No fixture context available for {player_name}")
        
        return player_data

    async def predict_player_next_gameweek(
        self,
        request: PlayerPredictionRequest
    ) -> PredictionResponse:
        """
        Predict points for a single player's next gameweek.

        Args:
            request: Prediction request

        Returns:
            Prediction response with fixture context
        """
        self._check_predictor()

        try:
            # NEW: Get next fixture for player
            fixture_context = None
            try:
                fixture_context = await asyncio.to_thread(
                    self.fixture_service.get_next_match,
                    request.player_name,
                    current_date=datetime.now(timezone.utc)
                )
            except Exception as e:
                # Log warning but don't fail - gracefully degrade to prediction without fixture context
                logger.warning(f"Could not retrieve fixture context for {request.player_name}: {e}")
            
            # If no fixture context, we cannot cache (no fixture_id), so skip cache and proceed with prediction
            if fixture_context is None:
                logger.debug(f"No fixture context for {request.player_name}, skipping cache")
                
                # Get player data without fixture context
                if not request.player_data:
                    player_data = await self._get_player_stats_with_fixture(
                        request.player_name,
                        None
                    )
                else:
                    player_data = dict(request.player_data)

                # Fill sentinel columns required by the feature engineering pipeline
                player_data = self._prepare_for_inference(player_data)

                # Predict
                pos = player_data.get("position", "MID")
                predictor = self.predictors.get(pos) or (list(self.predictors.values())[0] if self.predictors else self.predictor)
                prediction = await asyncio.to_thread(predictor.predict_next_gameweek, player_data)

                return PredictionResponse(
                    player_name=prediction.player_name,
                    predicted_points=prediction.predicted_points,
                    features_used=prediction.features_used,
                    model_version=prediction.model_version,
                    fixture_context=None
                )
            
            # Check cache before generating prediction
            model_version = "xgboost_v2"  # Using retrained v2 models
            cache_key = PredictionCache.generate_prediction_key(
                player_id=request.player_name,
                fixture_id=fixture_context.fixture_id,
                model_version=model_version
            )
            
            cached_prediction = self.cache.get(cache_key)
            if cached_prediction is not None:
                logger.info(f"Cache HIT for {request.player_name} (fixture: {fixture_context.fixture_id})")
                # Return cached prediction (should be a PredictionResponse)
                return cached_prediction
            
            logger.info(f"Cache MISS for {request.player_name} (fixture: {fixture_context.fixture_id})")
            
            # Get player data with fixture context
            if not request.player_data:
                player_data = await self._get_player_stats_with_fixture(
                    request.player_name,
                    fixture_context
                )
            else:
                # If player_data is provided, still enrich it with fixture context
                player_data = dict(request.player_data)
                if fixture_context is not None:
                    player_data["opponent_team"] = fixture_context.opponent_team
                    player_data["was_home"] = 1 if fixture_context.home_away == "home" else 0
                    player_data["fixture_id"] = fixture_context.fixture_id
                    player_data["matchday"] = fixture_context.matchday

            # Fill sentinel columns required by the feature engineering pipeline
            player_data = self._prepare_for_inference(player_data)

            # Predict
            pos = player_data.get("position", "MID")
            predictor = self.predictors.get(pos) or (list(self.predictors.values())[0] if self.predictors else self.predictor)
            prediction = await asyncio.to_thread(predictor.predict_next_gameweek, player_data)

            # Convert fixture_context to dict for API response
            fixture_context_dict = fixture_context.to_dict() if fixture_context else None

            response = PredictionResponse(
                player_name=prediction.player_name,
                predicted_points=prediction.predicted_points,
                features_used=prediction.features_used,
                model_version=prediction.model_version,
                fixture_context=fixture_context_dict  # NEW: Include fixture context in response
            )
            
            # Store prediction in cache with TTL based on fixture timing
            ttl = PredictionCache.calculate_cache_ttl(fixture_context.match_date)
            self.cache.set(cache_key, response, ttl)
            logger.debug(f"Cached prediction for {request.player_name} with TTL={ttl}s")
            
            return response

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @staticmethod
    def _top_performers_query(position: Optional[str], season: Optional[str]) -> str:
        """Cypher for the top-performers candidate pool.

        Same rows as the original query (players on a team with an upcoming
        2026-27 fixture, averages over their last 4 appearances with
        minutes > 0, at least 2 such appearances), but:
          * the upcoming-fixture team set is computed once, not per player;
          * each player's last 4 appearances come from a per-player Top-4
            (COLLECT subquery) instead of a global sort of every match row;
          * Fixture -> Gameweek -> Season is only traversed when a season
            filter is requested;
          * no arbitrary LIMIT: the old un-ordered `LIMIT 200` silently dropped
            most eligible players (506 rows -> 200) before ranking.
        """
        position_clause = "WHERE pos.code = $position" if position else ""
        return f"""
        {_UPCOMING_TEAM_PLAYERS}
        {position_clause}
        WITH p, pos, team, {_recent_perfs_subquery(season)} AS recent_perfs
        WHERE size(recent_perfs) >= 2
        UNWIND recent_perfs AS perf
        RETURN
            p.name                  AS name,
            pos.code                AS position,
            team.name               AS team_name,
            AVG(perf.minutes)       AS minutes,
            AVG(perf.goals_scored)  AS goals_scored,
            AVG(perf.assists)       AS assists,
            AVG(perf.total_points)  AS form,
            AVG(perf.bps)           AS bps,
            AVG(perf.ict_index)     AS ict_index,
            AVG(perf.influence)     AS influence,
            AVG(perf.creativity)    AS creativity,
            AVG(perf.threat)        AS threat,
            SUM(perf.clean_sheets)  AS clean_sheets,
            SUM(perf.bonus)         AS bonus,
            AVG(perf.value)         AS value
        """

    @staticmethod
    def _best_value_query(position: Optional[str], max_price: Optional[float]) -> str:
        """Cypher for the best-value candidate pool (see _top_performers_query).

        value in the graph is stored as integer * 10 (e.g. 85 = 8.5m); the price
        filter applies to the 4-match average, as before, but is now a
        parameter ($max_value) and keeps team_name in scope. (The old
        interpolated `WITH ... WHERE value <= X` dropped team_name, so every
        request with max_price failed with "Variable `team_name` not defined".)
        """
        position_clause = "WHERE pos.code = $position" if position else ""
        price_filter = "WHERE value <= $max_value" if max_price else ""
        return f"""
        {_UPCOMING_TEAM_PLAYERS}
        {position_clause}
        WITH p, pos, team, {_recent_perfs_subquery(None)} AS recent_perfs
        WHERE size(recent_perfs) >= 2
        UNWIND recent_perfs AS perf
        WITH
            p.name                  AS name,
            pos.code                AS position,
            team.name               AS team_name,
            AVG(perf.minutes)       AS minutes,
            AVG(perf.goals_scored)  AS goals_scored,
            AVG(perf.assists)       AS assists,
            AVG(perf.total_points)  AS form,
            AVG(perf.bps)           AS bps,
            AVG(perf.ict_index)     AS ict_index,
            AVG(perf.value)         AS value
        {price_filter}
        RETURN name, position, team_name, minutes, goals_scored, assists, form, bps, ict_index, value
        """

    def _predict_grouped(self, players_data: List[Dict[str, Any]], position: Optional[str], method: str) -> list:
        """Run `method` ('predict_top_performers' / 'predict_best_value') once per position group."""
        grouped = defaultdict(list)
        for p in players_data:
            grouped[p.get("position", "MID")].append(p)
        results = []
        for pos, group_players in grouped.items():
            predictor = self.predictors.get(pos) or list(self.predictors.values())[0]
            results.extend(getattr(predictor, method)(
                group_players,
                position=pos if position else None,
                top_k=len(group_players)
            ))
        return results

    def _top_performers_sync(self, request: TopPerformersRequest) -> Tuple[List[Dict[str, Any]], List[PlayerPrediction]]:
        """Blocking part of predict_top_performers (Neo4j query + model inference)."""
        query = self._top_performers_query(request.position, request.season)
        params = {
            "position": request.position,
            "season": request.season,
            "current_date": datetime.now(timezone.utc),
        }
        players_data = self.neo4j_conn.execute_query(query, params)

        if not players_data:
            raise HTTPException(
                status_code=404,
                detail="No player data found for prediction"
            )

        # Prepare for inference (adds total_points, etc.)
        players_data = [self._prepare_for_inference(p) for p in players_data]

        if self.predictors:
            all_preds = self._predict_grouped(players_data, request.position, "predict_top_performers")
            all_preds.sort(key=lambda x: x.predicted_points, reverse=True)
            predictions = all_preds[:request.top_k]
        else:
            predictions = self.predictor.predict_top_performers(
                players_data,
                position=request.position,
                top_k=request.top_k
            )
        return players_data, predictions

    async def _next_fixture_dict(self, player_name: str, current_date: datetime) -> Optional[Dict[str, Any]]:
        """FixtureService.get_next_match in a worker thread; None on any error."""
        try:
            fixture_context = await asyncio.to_thread(
                self.fixture_service.get_next_match,
                player_name,
                current_date=current_date
            )
            return fixture_context.to_dict() if fixture_context else None
        except Exception as e:
            logger.debug(f"Could not get fixture for {player_name}: {e}")
            return None

    async def predict_top_performers(
        self,
        request: TopPerformersRequest
    ) -> TopPerformersResponse:
        """
        Predict top K performers for next gameweek.

        Args:
            request: Top performers request

        Returns:
            List of predictions
        """
        self._check_predictor()

        try:
            players_data, predictions = await asyncio.to_thread(self._top_performers_sync, request)

            # Fixture context per prediction: the lookups are independent, so run
            # them concurrently (bounded by the default thread pool) instead of
            # one sequential Neo4j round trip per player.
            current_date = datetime.now(timezone.utc)
            fixture_dicts = await asyncio.gather(*[
                self._next_fixture_dict(pred.player_name, current_date) for pred in predictions
            ])

            # team_name from the rows used for prediction (first match wins, as before)
            team_by_name: Dict[str, Optional[str]] = {}
            for p_data in players_data:
                team_by_name.setdefault(p_data.get('name'), p_data.get('team_name'))

            response_predictions = [
                PredictionResponse(
                    player_name=pred.player_name,
                    predicted_points=pred.predicted_points,
                    features_used=pred.features_used,
                    model_version=pred.model_version,
                    fixture_context=fixture_context_dict,
                    team_name=team_by_name.get(pred.player_name)
                )
                for pred, fixture_context_dict in zip(predictions, fixture_dicts)
            ]

            return TopPerformersResponse(
                predictions=response_predictions,
                metadata={
                    "position_filter": request.position,
                    "total_players_analyzed": len(players_data),
                    "top_k": request.top_k
                }
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Top performers prediction failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=str(e))
    
    def _best_value_sync(self, request: BestValueRequest) -> BestValueResponse:
        """Blocking body of predict_best_value (Neo4j query + model inference)."""
        query = self._best_value_query(request.position, request.max_price)
        params = {
            "position": request.position,
            "current_date": datetime.now(timezone.utc),
            "max_value": request.max_price * 10 if request.max_price else None,
        }
        players_data = self.neo4j_conn.execute_query(query, params)

        if not players_data:
            return BestValueResponse(
                predictions=[],
                metadata={"position_filter": request.position, "total_players_analyzed": 0}
            )

        # Prepare for inference
        players_data = [self._prepare_for_inference(p) for p in players_data]

        # Predict best value
        if self.predictors:
            all_results = self._predict_grouped(players_data, request.position, "predict_best_value")
            all_results.sort(key=lambda x: x['points_per_million'], reverse=True)
            results = all_results[:request.top_k]
        else:
            results = self.predictor.predict_best_value(
                players_data,
                position=request.position,
                top_k=request.top_k
            )

        # Format results for consistency.
        # FPLPredictor.predict_best_value already returns `value` in millions
        # (graph value / 10), so it must not be divided by 10 again here.
        formatted_results = [
            {
                "player_name": r["name"],
                "predicted_points": r["predicted_points"],
                "cost": r["value"],
                "value": r["points_per_million"],
                "model_version": "xgboost_v2"
            }
            for r in results
        ]

        return BestValueResponse(
            predictions=formatted_results,
            metadata={
                "position_filter": request.position,
                "total_players_analyzed": len(players_data),
                "top_k": request.top_k
            }
        )

    async def predict_best_value(
        self,
        request: BestValueRequest
    ) -> BestValueResponse:
        """
        Predict best value players (points per million).
        Only includes players whose teams have upcoming fixtures.

        Args:
            request: Best value request

        Returns:
            BestValueResponse with fixture context
        """
        self._check_predictor()

        try:
            return await asyncio.to_thread(self._best_value_sync, request)
        except Exception as e:
            logger.error(f"Best value prediction failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))


# FastAPI route registration helper
def register_ml_routes(app, ml_integration: MLAPIIntegration):
    """
    Register ML prediction routes to FastAPI app.
    
    Args:
        app: FastAPI application
        ml_integration: ML API integration instance
    """
    
    @app.post("/api/ml/predict/player", response_model=PredictionResponse)
    async def predict_player(request: PlayerPredictionRequest):
        """Predict next gameweek points for a specific player."""
        return await ml_integration.predict_player_next_gameweek(request)
    
    @app.post("/api/ml/predict/top-performers", response_model=TopPerformersResponse)
    async def predict_top_performers(request: TopPerformersRequest):
        """Predict top K performers for next gameweek."""
        return await ml_integration.predict_top_performers(request)
    
    @app.post("/api/ml/predict/best-value", response_model=BestValueResponse)
    async def predict_best_value(request: BestValueRequest):
        """Predict best value players (points per million)."""
        return await ml_integration.predict_best_value(request)
    
    @app.get("/api/ml/status")
    async def ml_status():
        """Check ML predictor status."""
        if ml_integration.predictors:
            model_type = "xgboost (position-specific)"
        elif ml_integration.predictor:
            model_type = ml_integration.predictor.model_type
        else:
            model_type = None
            
        return {
            "predictor_loaded": ml_integration.predictor_loaded,
            "model_type": model_type,
            "endpoints": [
                "/api/ml/predict/player",
                "/api/ml/predict/top-performers",
                "/api/ml/predict/best-value"
            ]
        }
    
    logger.info("✅ ML prediction routes registered")
