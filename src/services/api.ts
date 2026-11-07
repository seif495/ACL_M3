import axios, { AxiosError } from 'axios';
import { API_BASE_URL, createApiClient } from './http';

const api = createApiClient();

const EMBEDDINGS_BUILD_TIMEOUT_MS = 15 * 60 * 1000; // 15 minutes

// Response types

/**
 * One row of player data from the search, stats and compare endpoints. Its keys depend on the
 * Cypher query that produced it, so the pages read the fields they expect and fall back when absent.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- keys and value types vary by query
export type PlayerRow = Record<string, any>;

export interface ConnectionResponse {
  success: boolean;
  message: string;
  stats?: {
    total_nodes: number;
    total_relationships: number;
  };
}

export interface QueryResponse {
  answer: string;
  intent: string;
  entities: Record<string, unknown>;
  cypher_query: string;
  kg_context: string;
  embedding_context?: string;
  embedding_used: boolean;
  results: Array<Record<string, unknown>>;
  graph_data?: {
    nodes: Array<{
      id: string;
      label: string;
      type: string;
      data?: Record<string, unknown>;
    }>;
    edges: Array<{
      from: string;
      to: string;
      label: string;
    }>;
  };
  /** Visual blocks drawn with the answer (services/answer_cards.py). Absent from older backends. */
  cards?: AnswerCard[];
  /** Suggested next questions, each routed by the backend's intent classifier. */
  follow_ups?: string[];
}

export interface QueryRequestBody {
  question: string;
  model: string;
  retrieval_method: string;
  embedding_model: string;
  is_first_message: boolean;
  chat_history: Array<{ role: string; content: string }>;
}

/** Events of POST /api/query/stream, in order: cards (as soon as they're built), the answer
 * in delta pieces, the final answer text, then the full response. */
export type AnswerStreamEvent =
  | { event: 'cards'; data: { cards: AnswerCard[]; follow_ups: string[] } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'answer'; data: { text: string } }
  | { event: 'done'; data: QueryResponse }
  | { event: 'error'; data: { detail: string } };

/** The backend has no stream endpoint (an older deploy): use queryFPL instead. */
export class StreamUnavailableError extends Error {}

// --- Assistant answer cards -------------------------------------------------
// Built from structured data (query rows, the graph, predictions), never from the
// answer text. The backend may add card types later: unknown ones are skipped.

export type CardTone = 'accent' | 'info' | 'warning' | 'danger' | 'neutral';
export type CardIcon =
  | 'trophy'
  | 'flame'
  | 'medal'
  | 'star'
  | 'zap'
  | 'clock'
  | 'home'
  | 'trend-up'
  | 'trend-down'
  | 'shield'
  | 'target'
  | 'calendar'
  | 'alert';
/** int, dec1/dec2 (decimals), money (£m), compact (1.2M). */
export type CardValueFormat = 'int' | 'dec1' | 'dec2' | 'money' | 'compact';
export type MatchResult = 'W' | 'D' | 'L';

/** A "notable stat" computed from the data (streaks, ranks, records). */
export interface CardHighlight {
  label: string;
  value: string;
  detail: string | null;
  tone: CardTone;
  icon: CardIcon;
}

export interface CardPlayer {
  /** Exact player name in the graph (used for links). */
  name: string;
  team: string | null;
  position: PositionCode | null;
  photo: string | null;
  /** FPL availability (a/d/i/s/u/n); null where it doesn't apply (past seasons). */
  status: string | null;
  chance: number | null;
  news: string | null;
}

export interface CardStat {
  key: string;
  label: string;
  value: number | null;
  format: CardValueFormat;
}

export interface CardOpponent {
  team: string;
  home: boolean;
  /** FPL fixture difficulty 1–5 for the player's/team's side, when known. */
  difficulty: number | null;
  kickoff_time: string | null;
  gameweek: number | null;
}

export interface CardPrediction {
  gameweek: number;
  points: number;
  /** Several for a double gameweek, none for a blank one. */
  opponents: CardOpponent[];
  rank: number;
  pool: number;
  position_rank: number;
  position_pool: number;
}

export interface CardMatch {
  season: string;
  gameweek: number | null;
  points: number;
  minutes: number;
  goals: number;
  assists: number;
  opponent: string | null;
  home: boolean | null;
  /** From the player's side, e.g. "4-1". */
  score: string | null;
  result: MatchResult | null;
}

/** Price (£m) and ownership (managers) by gameweek over one season. */
export interface CardTrend {
  season: string;
  points: Array<{ gameweek: number; price: number; selected: number | null }>;
  price_change: number;
  selected_change: number | null;
}

/** Expected goals/assists over one season (FPL publishes them for recent seasons only). */
export interface CardUnderlying {
  season: string;
  minutes: number;
  xg: number;
  xa: number;
  xgi: number;
  goals: number;
  assists: number;
  xg_per90: number | null;
  xa_per90: number | null;
  xgi_per90: number | null;
  /** Expected goals conceded while on the pitch, next to the goals actually conceded. */
  xgc: number;
  goals_conceded: number;
}

export interface PlayerAnswerCard {
  type: 'player';
  id: string;
  emphasis: 'stats' | 'prediction';
  player: CardPlayer;
  /** null = all seasons. */
  season: string | null;
  season_label: string;
  stats: CardStat[];
  /** Points per season, oldest first (all-seasons cards only). */
  seasons: Array<{ season: string; team: string | null; points: number; goals: number; assists: number; games: number }>;
  /** Most recent matches in scope, oldest first. */
  form: CardMatch[];
  /** The gameweek the question asked about. */
  match: CardMatch | null;
  prediction: CardPrediction | null;
  /** The club's next matches with FPL difficulty (current players). */
  next_fixtures?: CardOpponent[];
  /** For the season asked about, else the player's latest season. */
  trend?: CardTrend | null;
  underlying?: CardUnderlying | null;
  highlights: CardHighlight[];
}

export interface ComparisonMetric {
  key: string;
  label: string;
  format: CardValueFormat;
  values: [number | null, number | null];
  /** Index of the higher value; null for ties, missing values and unscored rows. */
  better: 0 | 1 | null;
  scored: boolean;
}

export interface ComparisonAnswerCard {
  type: 'comparison';
  id: string;
  emphasis: 'stats' | 'prediction';
  season: string | null;
  season_label: string;
  players: [CardPlayer, CardPlayer];
  metrics: ComparisonMetric[];
  /** How many scored metrics each player leads. */
  leads: [number, number];
  predictions: [CardPrediction | null, CardPrediction | null];
  /** Each player's next three matches (current players). */
  next_fixtures?: [CardOpponent[], CardOpponent[]];
  form: [CardMatch[], CardMatch[]];
  highlights: CardHighlight[];
}

export interface LeaderboardRow {
  /** Shared by ties; null in grouped (per-position) lists. */
  rank: number | null;
  player: CardPlayer;
  value: number | null;
  values: Record<string, number | null>;
  detail: string | null;
  group: PositionCode | null;
}

export interface LeaderboardAnswerCard {
  type: 'leaderboard';
  id: string;
  title: string;
  subtitle: string;
  icon: CardIcon;
  metric: { key: string; label: string; format: CardValueFormat; unit: string };
  columns: Array<{ key: string; label: string; format: CardValueFormat }>;
  /** Rows grouped by position (the top players of each). */
  grouped: boolean;
  season: string | null;
  rows: LeaderboardRow[];
  highlights: CardHighlight[];
}

export interface PredictionRow {
  rank: number;
  player: CardPlayer;
  points: number;
  opponents: CardOpponent[];
  price: number | null;
}

export interface PredictionAnswerCard {
  type: 'prediction';
  id: string;
  title: string;
  gameweek: number;
  deadline: string | null;
  position: PositionCode | null;
  captain: boolean;
  model: string | null;
  rows: PredictionRow[];
  highlights: CardHighlight[];
}

export interface TeamAnswerCard {
  type: 'team';
  id: string;
  team: string;
  season: string;
  standing: {
    position: number;
    played: number;
    won: number;
    drawn: number;
    lost: number;
    goals_for: number;
    goals_against: number;
    goal_difference: number;
    points: number;
  } | null;
  teams_in_table: number;
  /** Last five results, oldest first. */
  form: Array<{ opponent: string; home: boolean; score: string; result: MatchResult; gameweek: number | null }>;
  next_fixtures: CardOpponent[];
  top_players: Array<{ player: CardPlayer; points: number; goals: number; assists: number; games: number }>;
  /** Injured, doubtful and suspended players (first four). */
  flagged: CardPlayer[];
  flagged_total: number;
  highlights: CardHighlight[];
}

export interface FixturesAnswerCard {
  type: 'fixtures';
  id: string;
  title: string;
  subtitle: string;
  /** One team (its results) or two (head to head); results are from teams[0]'s side. */
  teams: string[];
  record: { team: string; played: number; won: number; drawn: number; lost: number; goals_for: number; goals_against: number };
  /** Newest first. */
  matches: Array<{
    season: string | null;
    gameweek: number | null;
    home_team: string;
    away_team: string;
    home_score: number;
    away_score: number;
    kickoff_time: string | null;
    result: MatchResult | null;
  }>;
  total: number;
  highlights: CardHighlight[];
}

export interface SeasonAnswerCard {
  type: 'season';
  id: string;
  season: string;
  season_label: string;
  in_progress: boolean;
  stats: CardStat[];
  leaders: Array<{ player: CardPlayer; points: number; goals: number; assists: number }>;
  highlights: CardHighlight[];
}

export interface PitchSlot {
  player: CardPlayer;
  points: number | null;
  cost: number | null;
  captain: boolean;
  vice_captain: boolean;
  opponents: Array<{ team: string; home: boolean }>;
}

/** Best XI: the optimiser's predicted squad, or a finished gameweek's dream team. */
export interface PitchAnswerCard {
  type: 'pitch';
  id: string;
  mode: TeamOfWeekMode;
  gameweek: number;
  formation: string;
  /** Predicted mode counts the captain twice. */
  total_points: number;
  total_cost: number | null;
  budget: number | null;
  starters: PitchSlot[];
  /** Predicted mode only. */
  bench: PitchSlot[];
  highlights: CardHighlight[];
}

/** The top predicted players of each next-gameweek match (both sides together). */
export interface FixturePicksAnswerCard {
  type: 'fixture_picks';
  id: string;
  title: string;
  gameweek: number | null;
  deadline: string | null;
  per_fixture: number;
  fixtures: {
    home: string;
    away: string;
    kickoff_time: string | null;
    players: { player: CardPlayer; points: number; /** Two matches this gameweek, points summed. */ double: boolean }[];
  }[];
}

export type AnswerCard =
  | FixturePicksAnswerCard
  | PlayerAnswerCard
  | ComparisonAnswerCard
  | LeaderboardAnswerCard
  | PredictionAnswerCard
  | TeamAnswerCard
  | FixturesAnswerCard
  | SeasonAnswerCard
  | PitchAnswerCard;

export interface TriviaQuestion {
  question: string;
  options: string[];
  category: string;
  difficulty: string;
  question_id: string;
}

export interface TriviaAnswerResponse {
  correct: boolean;
  feedback: string;
  correct_answer?: string;
}

export interface HealthStatus {
  status: string;
  neo4j: string;
  neo4j_stats?: {
    total_nodes: number;
    total_relationships: number;
  };
  llm_available: boolean;
  embeddings_built: boolean;
  embedding_count: number;
  embeddings_building?: boolean;
  embedding_build_error?: string | null;
  ml_available?: boolean;
  ml_model_type?: string | null;
}

export interface MLPrediction {
  player_name: string;
  /** FPL's short name ("Evanilson"), for display only; lookups use player_name. */
  web_name?: string | null;
  predicted_points: number;
  confidence_interval?: [number, number];
  features_used?: Record<string, unknown>;
  model_version: string;
  team_name?: string | null;
  fixture_context?: {
    opponent_team: string;
    home_away: "home" | "away";
    match_date: string;  // ISO datetime
    matchday: number;
    fixture_id: string;
  } | null;
  /** FPL availability: a available, d doubtful, i injured, s suspended, u unavailable, n not in squad */
  status?: 'a' | 'd' | 'i' | 's' | 'u' | 'n';
  /** 0-100, or null when FPL hasn't set one */
  chance_of_playing_next_round?: number | null;
  /** Official FPL team news, e.g. "Hamstring injury - Expected back 24 Oct" */
  news?: string;
  /** Next gameweeks' projected points (only with `horizon` > 1 and projections available). */
  projected?: { gameweek: number; points: number }[];
  projected_total?: number;
}

export interface TopPerformersResponse {
  predictions: MLPrediction[];
  metadata: Record<string, unknown> & {
    /** Gameweeks actually used: 1 when projections weren't available. */
    horizon?: number;
    horizon_gameweeks?: number[];
  };
}

export interface BestValueResponse {
  predictions: (MLPrediction & { value: number; cost: number })[];
  metadata: Record<string, unknown>;
}

export interface EmbeddingBuildResponse {
  success: boolean;
  count: number;
  message: string;
  started?: boolean;
  building?: boolean;
  model?: string | null;
}

export interface ImageSearchResponse {
  query: string;
  image_url?: string | null;
  source?: string | null;
}

// --- Season dashboard (Pitch Me redesign) ---------------------------------
export type FixtureStatusCode = 'scheduled' | 'in_progress' | 'finished';
export type PositionCode = 'GK' | 'DEF' | 'MID' | 'FWD';

export interface SeasonInfo {
  season: string;
  current_gameweek: number | null;
  next_gameweek: number | null;
  next_deadline: string | null;
  finished_gameweeks: number[];
  updated_at: string | null;
}

export interface SeasonFixture {
  id: string;
  gameweek: number;
  kickoff_time: string | null;
  status: FixtureStatusCode;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  home_difficulty: number | null;
  away_difficulty: number | null;
}

export interface FixturesResponse {
  season: string;
  gameweek: number;
  gameweeks: number[];
  fixtures: SeasonFixture[];
}

export interface TickerFixture {
  fixture_id: string;
  opponent: string;
  home: boolean;
  /** FPL's 1 (easy) to 5 (hard) from this club's side. */
  difficulty: number | null;
  kickoff_time: string | null;
}

export interface TickerClub {
  team: string;
  fixtures: number;
  total_difficulty: number;
  average_difficulty: number | null;
  /** One entry per gameweek; empty `fixtures` is a blank, two is a double. */
  gameweeks: { gameweek: number; fixtures: TickerFixture[] }[];
}

export interface FixtureTickerResponse {
  season: string;
  from_gameweek: number;
  gameweeks: number[];
  /** Easiest run first. */
  clubs: TickerClub[];
}

export interface TableRow {
  position: number;
  team: string;
  played: number;
  won: number;
  drawn: number;
  lost: number;
  goals_for: number;
  goals_against: number;
  goal_difference: number;
  points: number;
  form: Array<'W' | 'D' | 'L'>;
  /** The form's matches, for links to their match pages (older backends omit it). */
  recent?: { fixture_id: string; result: 'W' | 'D' | 'L'; home: boolean; score: string; opponent: string }[];
  next_fixture: { opponent: string; home: boolean; kickoff_time: string | null } | null;
}

/** One match from a club's side (team pages, match-page form). */
export interface TeamMatch {
  fixture_id: string;
  gameweek: number | null;
  kickoff_time: string | null;
  status: string;
  opponent: string;
  home: boolean;
  goals_for: number | null;
  goals_against: number | null;
  score: string | null;
  result: 'W' | 'D' | 'L' | null;
  difficulty: number | null;
  xg_for: number | null;
  xg_against: number | null;
}

export interface TeamSquadPlayer {
  name: string;
  web_name: string | null;
  position: PositionCode | null;
  status: string | null;
  news: string | null;
  chance_of_playing_next_round: number | null;
  total_points: number;
  goals: number;
  assists: number;
  minutes: number;
  appearances: number;
  clean_sheets: number;
  bonus: number;
  price: number | null;
  predicted_points: number | null;
  photo: string | null;
}

export interface TeamLeader {
  name: string;
  web_name: string | null;
  position: PositionCode | null;
  value: number;
  photo: string | null;
}

export interface TeamPageResponse {
  season: string;
  team: string;
  slug: string;
  standing: TableRow | null;
  teams_in_table: number;
  form: TeamMatch[];
  results: TeamMatch[];
  fixtures: TeamMatch[];
  position_history: { gameweek: number; position: number; points: number }[];
  stats: {
    played: number;
    goals_for: number;
    goals_against: number;
    clean_sheets: number;
    failed_to_score: number;
    xg_for: number | null;
    xg_against: number | null;
    xg_matches: number;
    home: { won: number; drawn: number; lost: number };
    away: { won: number; drawn: number; lost: number };
    biggest_win: TeamMatch | null;
  };
  squad: TeamSquadPlayer[];
  top_scorers: TeamLeader[];
  top_assists: TeamLeader[];
}

export interface TeamsIndexResponse {
  season: string;
  teams: (TableRow & { slug: string })[];
}

export interface MatchPlayer {
  name: string;
  team: string | null;
  position: PositionCode | null;
  minutes: number;
  started: boolean;
  points: number;
  goals: number;
  assists: number;
  yellow_cards: number;
  red_cards: number;
  own_goals: number;
  penalties_missed: number;
  penalties_saved: number;
  saves: number;
  bonus: number;
  bps: number;
  xg: number | null;
  xa: number | null;
  defensive_contribution: number | null;
  photo: string | null;
}

export interface MatchEvent {
  name: string;
  count: number;
}

export interface MatchSide {
  scorers: MatchEvent[];
  assists: MatchEvent[];
  /** Own goals by the other side, which count for this one. */
  own_goals_for: MatchEvent[];
  yellow_cards: MatchEvent[];
  red_cards: MatchEvent[];
  penalties_missed: MatchEvent[];
  penalties_saved: MatchEvent[];
  xg: number | null;
  points: number;
  starters: MatchPlayer[];
  substitutes: MatchPlayer[];
  unused: MatchPlayer[];
}

export interface MatchGoal {
  minute: number | null;
  extra: number | null;
  kind: 'goal' | 'penalty' | 'own_goal' | 'missed_penalty';
  /** Our player's name when matched, else ESPN's. */
  player: string | null;
  assist: string | null;
}

export interface MatchPageResponse {
  id: string;
  season: string;
  gameweek: number | null;
  kickoff_time: string | null;
  status: 'scheduled' | 'in_progress' | 'finished' | string;
  home_team: string;
  away_team: string;
  home_score: number | null;
  away_score: number | null;
  home_difficulty: number | null;
  away_difficulty: number | null;
  live: { fetched_at: string | null; minutes: number | null } | null;
  summary: {
    home: MatchSide;
    away: MatchSide;
    bonus: { name: string; team: string | null; bonus: number; bps: number }[];
    top_scorer: { name: string; team: string | null; points: number } | null;
  } | null;
  /** Goal minutes and penalties (ESPN), by the side each counts for; null until fetched. */
  goals?: { home: MatchGoal[]; away: MatchGoal[] } | null;
  form: { home: TeamMatch[]; away: TeamMatch[] };
  meetings: {
    fixture_id: string;
    season: string | null;
    gameweek: number | null;
    kickoff_time: string | null;
    home_team: string;
    away_team: string;
    home_score: number;
    away_score: number;
  }[];
  predicted: { home: { name: string; position: PositionCode | null; predicted_points: number }[]; away: { name: string; position: PositionCode | null; predicted_points: number }[] };
}

export interface GuardianArticle {
  id: string;
  title: string;
  url: string;
  summary: string | null;
  byline: string | null;
  thumbnail: string | null;
  published: string | null;
  source: string;
}

export interface FplNewsUpdate {
  kind: 'injury' | 'doubt' | 'suspension' | 'unavailable' | 'return';
  name: string;
  web_name: string | null;
  team: string | null;
  position: string | null;
  status: string;
  chance: number | null;
  text: string;
  time: string | null;
  photo: string | null;
}

export interface FplPriceChange {
  name: string;
  web_name: string | null;
  team: string | null;
  position: string | null;
  /** £m, signed. */
  change: number;
  price: number | null;
  selected: string | number | null;
  photo: string | null;
}

export interface NewsResponse {
  team: string | null;
  guardian: { enabled: boolean; articles: GuardianArticle[]; stale: boolean; fetched_at: string | null };
  fpl: { updates: FplNewsUpdate[]; prices: FplPriceChange[] };
}

export interface LeagueTableResponse {
  season: string;
  updated_at: string | null;
  rows: TableRow[];
}

export interface InjuredPlayer {
  name: string;
  web_name: string | null;
  team: string;
  position: PositionCode;
  status: 'd' | 'i' | 's' | 'u' | 'n';
  chance_of_playing_next_round: number | null;
  news: string | null;
  news_added: string | null;
  photo: string | null;
}

export interface InjuriesResponse {
  updated_at: string | null;
  players: InjuredPlayer[];
}

export interface TeamOfWeekPlayer {
  name: string;
  team: string;
  position: PositionCode;
  points: number;
  cost: number | null;
  captain: boolean;
  vice_captain: boolean;
  opponents: Array<{ team: string; home: boolean }>;
  status: string | null;
  chance_of_playing_next_round: number | null;
  news: string | null;
  photo: string | null;
  /** FPL's short name for the name plate; missing from older backends. */
  web_name?: string | null;
}

export type TeamOfWeekMode = 'predicted' | 'actual';

export interface TeamOfWeekResponse {
  mode: TeamOfWeekMode;
  gameweek: number;
  formation: string;
  total_points: number;
  total_cost: number | null;
  budget: number | null;
  starters: TeamOfWeekPlayer[];
  bench: TeamOfWeekPlayer[];
}

/** One player of a saved model pick, scored with that gameweek's FPL points. */
export interface ModelReviewPlayer {
  name: string;
  /** FPL's short display name ("Raya"), when known. */
  web_name: string | null;
  team: string;
  position: PositionCode;
  /** £m. */
  cost: number | null;
  /** The model's prediction for this gameweek. */
  predicted_points: number | null;
  /** Raw FPL points this gameweek; null until one of his matches has finished. */
  points: number | null;
  minutes: number | null;
  /** 0 = on the bench after auto-subs, 1, 2 = captain whose points count double (the vice if the captain didn't play). */
  multiplier: number;
  /** As picked. */
  captain: boolean;
  /** As picked. */
  vice_captain: boolean;
  /** null while his matches are still to finish. */
  played: boolean | null;
  /** Came off the bench via an auto-sub. */
  subbed_in: boolean;
  /** Picked to start, didn't play, replaced. */
  subbed_out: boolean;
  opponents: Array<{ team: string; home: boolean }>;
  photo: string | null;
}

/** The squad the model picked before a past deadline, and how it actually scored. */
export interface ModelReviewResponse {
  season: string;
  gameweek: number;
  /** Every gameweek with a saved pick, ascending. */
  gameweeks: number[];
  /** finished = FPL has marked the gameweek finished. */
  status: 'live' | 'finished';
  /** Set when FPL's live points (fetched at this time) were merged in, during the gameweek. */
  live?: { fetched_at: string } | null;
  deadline: string | null;
  /** When the pick was saved (ISO). */
  archived_at: string | null;
  /** manual = recorded by hand from the FPL app. */
  source: 'pipeline' | 'manual';
  note: string | null;
  /** Final XI after auto-subs, e.g. "4-4-2". */
  formation: string;
  /** The model's XI before auto-subs. */
  picked_formation: string;
  /** Model's XI + captain again, as predicted. */
  predicted_points: number;
  /** FPL score: final XI × multipliers. */
  points: number;
  /** Points left on the bench after auto-subs. */
  bench_points: number;
  fpl_average: number | null;
  fpl_highest: number | null;
  /** £m. */
  total_cost: number | null;
  /** £m. */
  budget: number | null;
  /** Final XI after auto-subs, GK, DEF, MID, FWD order. */
  starters: ModelReviewPlayer[];
  /** Final bench in priority order, goalkeeper first. */
  bench: ModelReviewPlayer[];
  auto_subs: Array<{ out: string; in: string }>;
}

// --- ML manager: a season-long team the points model runs under the game's rules, from GW9 on.
// Prices, bank and team value are in tenths of £1m (1000 = £100.0m), as the game stores them.

export type MlManagerChip = 'wildcard' | 'freehit' | 'bboost' | '3xc';

/** planned = saved before the deadline and replanned every run; locked = the deadline passed; scored = the gameweek finished. */
export type MlManagerStatus = 'planned' | 'locked' | 'scored';

export interface MlManagerPlayerRef {
  /** FPL element id. */
  element: number;
  name: string;
  web_name: string | null;
  position: PositionCode;
  team: string;
  /** Photo URL, when the backend knows one without a network call. */
  photo?: string | null;
}

export interface MlManagerTransfer {
  /** Sold at `selling_price` (tenths). */
  out: MlManagerPlayerRef & { selling_price: number };
  /** Bought at `price` (tenths). */
  in: MlManagerPlayerRef & { price: number };
}

export interface MlManagerSquadPlayer extends MlManagerPlayerRef {
  /** Tenths. */
  purchase_price: number;
  /** Tenths: what selling him would raise (half of any rise, rounded down). */
  selling_price: number;
  /** Tenths. */
  now_cost: number;
  /** The model's projection for this gameweek (summed over a double gameweek), before the captain's multiplier. */
  predicted_points: number | null;
  role: 'starter' | 'bench';
  /** Bench only: 1 = the substitute goalkeeper, 2..4 = outfield subs in priority order. */
  bench_order: number | null;
  captain: boolean;
  vice_captain: boolean;
  /** This gameweek's opponents, when the backend sends them (several in a double gameweek). */
  opponents?: Array<{ team: string; home: boolean }>;
}

export interface MlManagerResultPlayer {
  element: number;
  name?: string;
  /** Raw points this gameweek; null if unknown (while live: his matches haven't started). */
  points: number | null;
  /** Minutes played so far; null while his matches are still to start. */
  minutes: number | null;
  /** null while his matches are still to finish. */
  played?: boolean | null;
  /** 0 for an uncounted bench player, 2 for the captain (3 with Triple Captain). */
  multiplier: number;
  counted: boolean;
  /** Came off the bench via an auto-sub. */
  subbed_in?: boolean;
  /** Picked to start, didn't play, replaced. */
  subbed_out?: boolean;
}

/** Rank in the game's league of managers who started in the same gameweek (GW9). */
export interface MlManagerLeague {
  id?: number | null;
  /** The league's name in the game, e.g. "Gameweek 9". */
  name: string | null;
  /** The gameweek the rank is after (totals only). */
  gameweek?: number;
  rank: number;
  size: number;
  /** Share of the league with a lower total, 0–100 (95.9 = top 4.1%). */
  percentile: number | null;
}

export interface MlManagerResult {
  /** Net points: gross minus hits. */
  points: number;
  gross: number;
  hit_cost: number;
  /** Display name of the captain as picked. */
  captain: string | null;
  /** Whose points were multiplied: the captain, or the vice when the captain didn't play. */
  armband?: string | null;
  vice_captain_used?: boolean;
  /** [out, in] full player names. */
  auto_subs: Array<[string, string]>;
  players: MlManagerResultPlayer[];
  gw_average: number | null;
  gw_highest: number | null;
  /** Running season total after this gameweek. */
  total: number;
  league: MlManagerLeague | null;
  /** Points left on the bench. */
  bench_points?: number;
  /** Set on a provisional result from FPL's live points while the gameweek is played. */
  live?: { fetched_at: string } | null;
  /** A locked gameweek the pipeline hasn't scored yet: live points, or the finished matches so far. */
  provisional?: boolean;
  /** FPL has marked the gameweek finished (bonus may still be added until it checks the data). */
  finished?: boolean;
}

/** One gameweek of the manager (a saved gwNN.json). */
export interface MlManagerGameweek {
  season: string;
  gameweek: number;
  deadline: string | null;
  status: MlManagerStatus;
  /** When the plan was last saved (ISO UTC). */
  planned_at: string | null;
  model: string;
  chip: MlManagerChip | null;
  transfers: MlManagerTransfer[];
  /** Free transfers available before this gameweek's transfers (0 for the free starting squad). */
  free_transfers: number;
  transfers_made: number;
  /** Transfers beyond the free ones. */
  hits: number;
  /** Points deducted for them. */
  hit_cost: number;
  /** Tenths. */
  bank_before: number;
  /** Tenths. */
  bank_after: number;
  /** All 15, starters first. */
  squad: MlManagerSquadPlayer[];
  /** This gameweek's projection with the captain/chip, minus hits. */
  projected_points: number | null;
  horizon: Array<{ gameweek: number; projected: number }>;
  /** One-line explanation of the decisions. */
  reason: string | null;
  /** Null until the gameweek is scored. */
  result: MlManagerResult | null;
  /** Tenths: the squad at current prices, without the bank (summed from `now_cost` when absent). */
  team_value?: number;
}

export interface MlManagerChipState {
  chip: MlManagerChip;
  /** e.g. "Wildcard". */
  label: string;
  /** First and last gameweek it can be played in. */
  window: [number, number];
  /** The gameweek it was played in, or is planned for (see `planned`). */
  used_gameweek: number | null;
  /** Not used and its window hasn't ended (it may not have started yet). */
  available: boolean;
  /** 1 or 2: the half of the season. */
  half?: number;
  /** Played in the planned gameweek, which can still change. */
  planned?: boolean;
  /** Playable in the gameweek being planned. */
  active?: boolean;
}

export interface MlManagerHistoryRow {
  gameweek: number;
  /** Net of hits. */
  points: number;
  /** Before hits. */
  gross?: number | null;
  hit_cost: number;
  chip: MlManagerChip | null;
  captain: string | null;
  transfers: number;
  /** Running total. */
  total: number;
  /** Tenths: the squad at current prices, without the bank. */
  team_value: number;
  /** Tenths. */
  bank: number;
  gw_average: number | null;
  gw_highest: number | null;
  league_id?: number | null;
  league_name?: string | null;
  league_rank: number | null;
  league_size: number | null;
  percentile: number | null;
}

export interface MlManagerTotals {
  points: number;
  /** Scored gameweeks. */
  gameweeks: number;
  /** Season points minus the sum of the gameweek averages; null before the first scored gameweek. */
  vs_average: number | null;
  league: MlManagerLeague | null;
}

/** One gameweek the manager has a team for (the page's picker). */
export interface MlManagerGameweekRef {
  gameweek: number;
  /** As shown: a plan past its deadline is locked. */
  status: MlManagerStatus;
  /** Being played: deadline passed, FPL hasn't finished it. */
  live: boolean;
  /** Net points once scored. */
  points: number | null;
}

export interface MlManagerView {
  season: string;
  start_gameweek: number;
  model: string;
  /** budget in tenths. */
  rules: { budget: number; max_free_transfers: number; hit_cost: number };
  /** The gameweek asked for; by default the one being played, else the plan for the next deadline or the last scored one. */
  current: MlManagerGameweek;
  /** Every gameweek with a team, oldest first (older backends leave it out). */
  gameweeks?: MlManagerGameweekRef[];
  /** The gameweek shown without one asked for. */
  default_gameweek?: number;
  chips: MlManagerChipState[];
  /** Scored gameweeks, oldest first. */
  history: MlManagerHistoryRow[];
  totals: MlManagerTotals;
  next_deadline: string | null;
  /** The replayed state after the last locked/scored gameweek (before any planned one). */
  state?: { gameweek: number | null; next_gameweek: number; bank: number; free_transfers: number; team_value: number };
}

// --- Model insights: SHAP and LIME explanations of the points model's (v4) next-gameweek predictions.

/** One model feature. `importance` = mean |SHAP| in points over this gameweek's player-fixture rows. */
export interface ExplainFeature {
  feature: string;
  label: string;
  group: string;
  description: string;
  importance: number;
}

/** Mean |SHAP| summed per feature family. */
export interface ExplainGroup {
  group: string;
  importance: number;
}

/** SHAP summary-plot points for one feature: [feature value percentile 0..1 (null = missing), shap]. */
export interface ExplainBeeswarm {
  feature: string;
  label: string;
  points: Array<[number | null, number]>;
}

export interface ExplainMetrics {
  /** Average actual points of the top 20 picks per gameweek. */
  top20_points: number;
  /** Average actual points of the top pick per gameweek. */
  captain_points: number;
  rmse: number;
  mae: number;
  spearman: number;
  rows?: number;
}

export interface ExplainModelInfo {
  version: string;
  trained_at: string | null;
  /** Trees per model; the served model averages `members` models (1 for older files). */
  n_trees: number | null;
  members?: number;
  seasons: string[];
  rows: number | null;
  /** live = the weekly retrained copy on the server; bundled = the model shipped with the code. */
  source: 'live' | 'bundled';
  /** The model's own metrics under its `version` ('v4'), then 'last3_avg', 'last6_avg', 'season_ppg'. */
  test_metrics: Record<string, ExplainMetrics>;
}

export interface ExplainPlayerHit {
  name: string;
  element: number;
  team: string;
  position: string;
  predicted_points: number;
}

export interface ExplainSummary {
  season: string;
  gameweek: number;
  generated_at: string | null;
  base_value: number;
  /** Player-fixture rows explained. */
  rows: number;
  players: number;
  model: ExplainModelInfo;
  /** Sorted by importance, descending. */
  groups: ExplainGroup[];
  /** Every model feature, sorted by importance, descending. */
  features: ExplainFeature[];
  /** The top 10 features. */
  beeswarm: ExplainBeeswarm[];
  /** The top 12 by predicted points. */
  top_players: ExplainPlayerHit[];
}

/** One feature's SHAP contribution to a prediction. Contributions arrive sorted by |shap|, descending. */
export interface ExplainContribution {
  feature: string;
  label: string;
  group: string;
  value: number | null;
  shap: number;
}

export interface LimeWeight {
  feature: string;
  label: string;
  /** Human-readable rule, e.g. "minutes_r3 > 78". */
  condition: string;
  /** Points. */
  weight: number;
  value: number | null;
}

export interface LimeExplanation {
  intercept: number;
  local_prediction: number;
  model_prediction: number;
  /** R² of the local linear fit. */
  score: number;
  num_samples: number;
  kernel_width: number;
  weights: LimeWeight[];
}

export interface ExplainFixture {
  fixture: number;
  opponent: string;
  is_home: boolean;
  kickoff_time: string | null;
  base_value: number;
  /** The raw model output for this fixture (can be negative). */
  model_points: number;
  contributions: ExplainContribution[];
  lime: LimeExplanation;
}

export interface PlayerExplanation {
  player: {
    name: string;
    element: number;
    team: string;
    position: string;
    status: string | null;
    chance_of_playing_next_round: number | null;
    news: string;
  };
  season: string;
  gameweek: number;
  /** Two in a double gameweek, none in a blank one. */
  fixtures: ExplainFixture[];
  /** Sum over fixtures of max(0, model_points). */
  model_points: number;
  /** 0..1, FPL's chance of playing. */
  availability_factor: number;
  /** The number the rest of the site shows. */
  predicted_points: number;
}

export interface ExplainDependencePoint {
  name: string;
  element: number;
  position: string;
  value: number | null;
  shap: number;
}

export interface ExplainDependence {
  feature: string;
  label: string;
  group: string;
  description: string;
  points: ExplainDependencePoint[];
}

export type PlayerLeaderSort =
  | 'total_points'
  | 'goals'
  | 'assists'
  | 'clean_sheets'
  | 'bonus'
  | 'points_per_game'
  | 'minutes';

export interface PlayerLeader {
  name: string;
  /** FPL's short name, for display only (current players); lookups use name. */
  web_name?: string | null;
  team: string | null;
  /** The player's position in that season. */
  position: PositionCode | null;
  games: number;
  minutes: number;
  goals: number;
  assists: number;
  clean_sheets: number;
  bonus: number;
  total_points: number;
  /** total_points / games, 2 dp. */
  points_per_game: number;
  /** Average price that season, £m (null when unknown). */
  price?: number | null;
  /** FPL availability; current season only (null for past seasons). */
  status: string | null;
  chance_of_playing_next_round: number | null;
  news: string | null;
  photo: string | null;
}

export interface PlayerLeadersResponse {
  season: string;
  sort: PlayerLeaderSort;
  position: PositionCode | null;
  players: PlayerLeader[];
}

export interface PlayerLeadersParams {
  season?: string;
  position?: PositionCode;
  sort?: PlayerLeaderSort;
  /** 1–100, default 50. */
  limit?: number;
  /** One club, as the graph names it (e.g. "Man City"). */
  team?: string;
  /** Highest average price, £m. */
  max_price?: number;
}

/** Filters shared by the prediction lists. */
export interface PredictionFilters {
  team?: string;
  max_price?: number;
  /** Part of the player's name. */
  q?: string;
}

export interface ProfileOpponent {
  team: string;
  home: boolean;
  /** The player's side first, e.g. "2-0"; null before the match is played. */
  score: string | null;
}

/** GET /api/players/profile: one season (or "All seasons") of a player's matches. */
export interface PlayerProfile {
  name: string;
  season: string;
  position: PositionCode | null;
  team: string | null;
  status: string | null;
  news: string | null;
  chance_of_playing_next_round: number | null;
  matches: number;
  appearances: number;
  price: number | null;
  selected: number | null;
  points_per_game: number | null;
  minutes_per_game: number | null;
  /** Sums over the matches; optional stats are null when FPL has no data for them. */
  totals: Record<string, number | null>;
  /** Per 90 minutes; null under 90 minutes played. */
  per90: Record<string, number | null>;
  breakdown: { key: string; label: string; points: number }[];
  /** One entry per gameweek (single season only). */
  gameweeks: { gameweek: number; points: number; minutes: number; goals: number; assists: number; opponents: ProfileOpponent[] }[];
  /** One entry per season (all seasons only). */
  seasons: { season: string; team: string | null; points: number; minutes: number; appearances: number; goals: number; assists: number }[];
  photo: string | null;
  predicted_points: number | null;
}

// API functions
export const apiService = {
  // Health & Connection
  async getHealth(): Promise<HealthStatus> {
    const { data } = await api.get<HealthStatus>('/health');
    return data;
  },

  async connectNeo4j(uri: string, username: string, password: string): Promise<ConnectionResponse> {
    const { data } = await api.post<ConnectionResponse>('/api/connection/connect', {
      uri,
      username,
      password,
    });
    return data;
  },

  async disconnectNeo4j(): Promise<{ success: boolean; message: string }> {
    const { data } = await api.post('/api/connection/disconnect');
    return data;
  },

  // ML Predictions
  async predictPlayer(playerName: string): Promise<MLPrediction> {
    const { data } = await api.post<MLPrediction>('/api/ml/predict/player', {
      player_name: playerName,
    });
    return data;
  },

  async predictTopPerformers(
    position?: string,
    topK: number = 10,
    filters: PredictionFilters = {},
    horizon: number = 1,
  ): Promise<TopPerformersResponse> {
    // Try GET endpoint first (faster, uses CSV), fallback to POST (uses Neo4j + ML)
    try {
      const { data } = await api.get<TopPerformersResponse>('/api/ml/top-performers', {
        params: { position, limit: topK, ...filters, horizon: horizon > 1 ? horizon : undefined },
        timeout: 5000,
      });
      return data;
    } catch (error) {
      // Fallback to POST endpoint if GET fails
      console.warn('CSV endpoint failed, trying Neo4j-based endpoint:', error);
      const { data } = await api.post<TopPerformersResponse>('/api/ml/predict/top-performers', {
        position,
        top_k: topK,
      });
      return data;
    }
  },

  async predictBestValue(
    position?: string,
    topK: number = 10,
    filters: PredictionFilters = {},
  ): Promise<BestValueResponse> {
    // Try GET endpoint first (faster, uses CSV), fallback to POST (uses Neo4j + ML)
    try {
      const { data } = await api.get<BestValueResponse>('/api/ml/best-value', {
        params: { position, limit: topK, ...filters },
        timeout: 5000,
      });
      return data;
    } catch (error) {
      // Fallback to POST endpoint if GET fails
      console.warn('CSV endpoint failed, trying Neo4j-based endpoint:', error);
      const { data } = await api.post<BestValueResponse>('/api/ml/predict/best-value', {
        position,
        top_k: topK,
      });
      return data;
    }
  },

  async getMLStatus(): Promise<unknown> {
    const { data } = await api.get('/api/ml/status');
    return data;
  },

  // Query
  async queryFPL(
    question: string,
    model: string = 'gpt-oss-120b',
    retrievalMethod: string = 'Hybrid',
    embeddingModel: string = 'minilm',
    isFirstMessage: boolean = false,
    chatHistory: Array<{ role: string; content: string }> = []
  ): Promise<QueryResponse> {
    const { data } = await api.post<QueryResponse>('/api/query', {
      question,
      model,
      retrieval_method: retrievalMethod,
      embedding_model: embeddingModel,
      is_first_message: isFirstMessage,
      chat_history: chatHistory,
    });
    return data;
  },

  /**
   * Streams an answer from POST /api/query/stream, calling onEvent for each server-sent
   * event. Uses fetch, since axios can't read a response body as it arrives. Throws
   * StreamUnavailableError when the endpoint is missing, and an Error with the server's
   * detail for other HTTP errors (e.g. the rate limit).
   */
  async streamQuery(body: QueryRequestBody, onEvent: (event: AnswerStreamEvent) => void, signal?: AbortSignal): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/api/query/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    });
    if (response.status === 404 || response.status === 405) throw new StreamUnavailableError();
    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      try {
        const json = await response.json();
        if (typeof json?.detail === 'string') detail = json.detail;
      } catch {
        // not JSON: keep the status message
      }
      throw new Error(detail);
    }
    if (!response.body) throw new StreamUnavailableError();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = 'message';
        const data: string[] = [];
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (data.length) onEvent({ event, data: JSON.parse(data.join('\n')) } as AnswerStreamEvent);
      }
    }
  },

  // Embeddings
  async buildEmbeddings(model: string = 'minilm'): Promise<EmbeddingBuildResponse> {
    const { data } = await api.post<EmbeddingBuildResponse>(
      '/api/embeddings/build',
      { model },
      { timeout: EMBEDDINGS_BUILD_TIMEOUT_MS }
    );
    return data;
  },

  // Trivia
  async getNewTriviaQuestion(difficulty?: 'easy' | 'medium' | 'hard'): Promise<TriviaQuestion> {
    const params = difficulty ? { difficulty } : {};
    const { data } = await api.get<TriviaQuestion>('/api/trivia/new', { params });
    return data;
  },

  async checkTriviaAnswer(questionId: string, answer: string): Promise<TriviaAnswerResponse> {
    const { data } = await api.post<TriviaAnswerResponse>('/api/trivia/answer', {
      question_id: questionId,
      answer,
    });
    return data;
  },

  // Players
  async searchPlayers(
    query: string,
    options?: { limit?: number; includeAvatars?: boolean; signal?: AbortSignal }
  ): Promise<{ players: PlayerRow[] }> {
    const { data } = await api.post(
      '/api/players/search',
      {
        query,
        limit: options?.limit ?? 20,
        include_avatars: options?.includeAvatars ?? false, // Default to false for fast search
      },
      { signal: options?.signal }
    );
    return data;
  },

  async getPlayerStats(
    playerName: string,
    season?: string,
    signal?: AbortSignal
  ): Promise<{ stats: PlayerRow }> {
    const { data } = await api.post('/api/players/stats', { player_name: playerName, season }, { signal });
    return data;
  },

  async comparePlayers(
    player1: string,
    player2: string,
    season?: string
  ): Promise<{ comparison: PlayerRow[] }> {
    const { data } = await api.post('/api/players/compare', {
      player1,
      player2,
      season,
    });
    return data;
  },

  async getClubs(season?: string): Promise<{ season: string; clubs: string[] }> {
    const { data } = await api.get<{ season: string; clubs: string[] }>('/api/clubs', { params: { season } });
    return data;
  },

  async getPlayerProfile(name: string, season?: string, signal?: AbortSignal): Promise<PlayerProfile> {
    const { data } = await api.get<PlayerProfile>('/api/players/profile', {
      params: { name, season: season && season !== 'All seasons' ? season : undefined },
      signal,
    });
    return data;
  },

  async getPlayerLeaders(params: PlayerLeadersParams = {}, signal?: AbortSignal): Promise<PlayerLeadersResponse> {
    const { data } = await api.get<PlayerLeadersResponse>('/api/players/leaders', { params, signal });
    return data;
  },

  async searchImage(query: string, signal?: AbortSignal): Promise<ImageSearchResponse> {
    const { data } = await api.get<ImageSearchResponse>('/api/images/search', {
      params: { query },
      signal,
    });
    return data;
  },

  // Season dashboard
  async getCurrentSeason(): Promise<SeasonInfo> {
    const { data } = await api.get<SeasonInfo>('/api/season/current');
    return data;
  },

  async getFixtures(gameweek?: number): Promise<FixturesResponse> {
    const { data } = await api.get<FixturesResponse>('/api/fixtures', { params: gameweek ? { gameweek } : undefined });
    return data;
  },

  async getFixtureTicker(gameweeks: number = 6, start?: number): Promise<FixtureTickerResponse> {
    const { data } = await api.get<FixtureTickerResponse>('/api/fixtures/ticker', { params: { gameweeks, start } });
    return data;
  },

  async getTeams(season?: string): Promise<TeamsIndexResponse> {
    const { data } = await api.get<TeamsIndexResponse>('/api/teams', { params: { season } });
    return data;
  },

  async getTeam(slug: string, season?: string): Promise<TeamPageResponse> {
    const { data } = await api.get<TeamPageResponse>(`/api/teams/${encodeURIComponent(slug)}`, { params: { season } });
    return data;
  },

  async getMatch(fixtureId: string): Promise<MatchPageResponse> {
    const { data } = await api.get<MatchPageResponse>(`/api/matches/${encodeURIComponent(fixtureId)}`);
    return data;
  },

  async getNews(team?: string): Promise<NewsResponse> {
    const { data } = await api.get<NewsResponse>('/api/news', { params: { team } });
    return data;
  },

  async getLeagueTable(): Promise<LeagueTableResponse> {
    const { data } = await api.get<LeagueTableResponse>('/api/table');
    return data;
  },

  async getInjuries(team?: string): Promise<InjuriesResponse> {
    const { data } = await api.get<InjuriesResponse>('/api/injuries', { params: team ? { team } : undefined });
    return data;
  },

  async getTeamOfTheWeek(mode: TeamOfWeekMode = 'predicted', gameweek?: number): Promise<TeamOfWeekResponse> {
    const { data } = await api.get<TeamOfWeekResponse>('/api/team-of-the-week', {
      params: { mode, ...(gameweek ? { gameweek } : {}) },
    });
    return data;
  },

  /** The model's saved pick for a past gameweek (default: the latest whose deadline passed). 404 when none. */
  async getModelReview(gameweek?: number): Promise<ModelReviewResponse> {
    const { data } = await api.get<ModelReviewResponse>('/api/model-review', {
      params: gameweek ? { gameweek } : undefined,
    });
    return data;
  },

  /**
   * The ML manager's season: one gameweek (by default the one being played, else the latest plan
   * or result), chips, history and totals. 404 until it has started, or for a gameweek it has no team for.
   */
  async getMlManager(gameweek?: number): Promise<MlManagerView> {
    const { data } = await api.get<MlManagerView>('/api/ml-manager', {
      params: gameweek ? { gameweek } : undefined,
    });
    return data;
  },

  /** A visitor's FPL team by its ID. 404 no such team, 409 no squad yet, 502 FPL not answering, 429 rate limit. */
  async getMyTeam(id: number, signal?: AbortSignal): Promise<MyTeamView> {
    const { data } = await api.get<MyTeamView>(`/api/my-team/${id}`, { signal });
    return data;
  },

  /** The planner's best transfers for that team (5–15 s). 503 no projections yet or busy, 422 no valid plan. */
  async planMyTeam(id: number, options: MyTeamPlanOptions): Promise<MyTeamPlan> {
    const { data } = await api.post<MyTeamPlan>(`/api/my-team/${id}/plan`, options, { timeout: 90_000 });
    return data;
  },

  // Model insights (SHAP / LIME). 503 until the first prediction run has written explanations.
  async getExplainSummary(signal?: AbortSignal): Promise<ExplainSummary> {
    const { data } = await api.get<ExplainSummary>('/api/ml/explain/summary', { signal });
    return data;
  },

  /** One player's explanation, by FPL element id or by name. 404 when he isn't in this gameweek's predictions. */
  async getPlayerExplanation(player: { element: number } | { name: string }, signal?: AbortSignal): Promise<PlayerExplanation> {
    const { data } = await api.get<PlayerExplanation>('/api/ml/explain/player', { params: player, signal });
    return data;
  },

  async getExplainDependence(feature: string, signal?: AbortSignal): Promise<ExplainDependence> {
    const { data } = await api.get<ExplainDependence>('/api/ml/explain/dependence', { params: { feature }, signal });
    return data;
  },

  async searchExplainPlayers(q: string, limit = 20, signal?: AbortSignal): Promise<{ players: ExplainPlayerHit[] }> {
    const { data } = await api.get<{ players: ExplainPlayerHit[] }>('/api/ml/explain/players', { params: { q, limit }, signal });
    return data;
  },

  // Data Management
  async loadFPLData(clearExisting: boolean = true): Promise<{ success: boolean; stats: unknown }> {
    const { data } = await api.post('/api/data/load', { clear_existing: clearExisting });
    return data;
  },

  async getDatabaseStats(): Promise<Record<string, unknown>> {
    const { data } = await api.get('/api/stats/database');
    return data;
  },

  // Cookie-free page-view counter (aggregate counts only). Every route change is counted; with
  // consent the view also carries the browser's timezone and visitor flags (lib/visitor.ts).
  // Fire-and-forget: analytics must never surface an error to the visitor.
  trackPageview(path: string, referrer: string | null, extras: PageviewExtras = {}): void {
    api.post('/api/analytics/pageview', { path, referrer, ...extras }, { timeout: 5000 }).catch(() => undefined);
  },

  /** The owner's site stats (`/stats`): 403 for a wrong token, 404 when the server has none set. */
  async getSiteStats(days: number, token: string): Promise<SiteStats> {
    const { data } = await api.get('/api/admin/analytics', { params: { days }, headers: { 'X-Admin-Token': token } });
    return data;
  },
};

// Page-view analytics (services/analytics.py)
export interface PageviewExtras {
  /** The visitor accepted the consent banner; the fields below are ignored without it. */
  consented?: boolean;
  timezone?: string | null;
  new_visitor?: boolean;
  first_today?: boolean;
  /** The owner's own browser: counted apart from everyone else. */
  owner?: boolean;
  /** False: only the visitor flags, for a page whose view was already counted. */
  view?: boolean;
}

export interface SiteStatsDay {
  date: string;
  views: number;
  visitors: number;
  owner_views: number;
}

export interface SiteStats {
  days: number;
  /** Other people's views (the owner's are in owner_views). */
  total_views: number;
  /** Views from visitors who accepted the consent banner: what visitors and countries cover. */
  consented_views: number;
  /** Visitors who accepted, counted once per UTC day. */
  visitors: number;
  new_visitors: number;
  owner_views: number;
  by_day: SiteStatsDay[];
  top_paths: { path: string; views: number }[];
  top_referrers: { host: string; views: number }[];
  top_countries: { code: string | null; country: string; visitors: number }[];
  top_zones: { zone: string; visitors: number }[];
}

// --- My team (a visitor's FPL team imported by its ID) -----------------------------------------
// Prices are tenths of £1m, as FPL stores them.

export interface MyTeamEntry {
  id: number;
  name: string;
  manager: string;
  overall_points: number | null;
  overall_rank: number | null;
  event_points: number | null;
  last_gameweek: number | null;
  started_gameweek: number | null;
}

export interface MyTeamChip {
  chip: MlManagerChip;
  label: string;
  half: number;
  window: [number, number];
  used_gameweek: number | null;
  planned?: boolean;
  available: boolean;
  active: boolean;
}

export interface MyTeamProjection {
  gameweek: number;
  points: number;
}

export interface MyTeamPlayer {
  element: number;
  name: string;
  web_name: string | null;
  team: string;
  position: PositionCode;
  now_cost: number;
  purchase_price: number | null;
  selling_price: number | null;
  purchase_source?: string | null;
  status: string | null;
  chance: number | null;
  news: string | null;
  predicted_points: number | null;
  opponents: { team: string; home: boolean }[];
  projected: MyTeamProjection[];
  projected_total: number | null;
  photo: string | null;
}

export interface MyTeamLineup {
  starters: number[];
  /** In substitution order. */
  bench: number[];
  captain: number | null;
  vice_captain: number | null;
  formation: string;
  projected_points: number;
}

export interface MyTeamHistoryRow {
  gameweek: number;
  /** Before the transfer cost. */
  points: number | null;
  transfer_cost: number;
  overall_rank: number | null;
  average: number | null;
  chip: MlManagerChip | null;
}

export interface MyTeamView {
  entry: MyTeamEntry;
  /** The next gameweek. */
  gameweek: number;
  deadline: string | null;
  predictions_gameweek: number | null;
  based_on_gameweek: number | null;
  pending_transfers: unknown[];
  bank: number;
  team_value: number;
  free_transfers: number;
  chips: MyTeamChip[];
  squad: MyTeamPlayer[];
  lineup: MyTeamLineup;
  history: MyTeamHistoryRow[];
}

export interface MyTeamPlanOptions {
  free_transfers?: number;
  max_hits: number;
  horizon: number;
  chips: boolean;
}

export interface MyTeamPlanSide {
  element: number;
  name: string;
  web_name: string | null;
  position: PositionCode;
  team: string;
}

export interface MyTeamPlanPlayer {
  element: number;
  name: string;
  web_name: string | null;
  position: PositionCode;
  team: string;
  purchase_price: number | null;
  selling_price: number | null;
  now_cost: number;
  predicted_points: number | null;
  role: 'starter' | 'bench';
  bench_order: number | null;
  captain: boolean;
  vice_captain: boolean;
  photo: string | null;
}

export interface MyTeamPlan {
  season?: string;
  gameweek: number;
  deadline: string | null;
  chip: MlManagerChip | null;
  transfers: {
    out: MyTeamPlanSide & { selling_price: number };
    in: MyTeamPlanSide & { price: number };
  }[];
  free_transfers: number;
  transfers_made: number;
  hits: number;
  hit_cost: number;
  bank_before: number;
  bank_after: number;
  squad: MyTeamPlanPlayer[];
  projected_points: number;
  horizon: { gameweek: number; projected: number; transfers: number; hits: number; free_transfers: number }[];
  reason: string | null;
  optimal: boolean;
  inputs: MyTeamPlanOptions;
  /** The current squad's best XI, for comparison. */
  current_projected: number;
}

/** The HTTP status of a failed request, if it had a response. */
export const httpStatusOf = (error: unknown): number | null =>
  axios.isAxiosError(error) ? (error.response?.status ?? null) : null;

// Error handling
export const handleApiError = (error: unknown): string => {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<{ detail: string }>;
    if (axiosError.response?.data?.detail) {
      return axiosError.response.data.detail;
    }
    if (axiosError.message) {
      return axiosError.message;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'An unknown error occurred';
};

/** True for an HTTP 404, e.g. a resource that doesn't exist yet (not a failure worth retrying). */
export const isNotFoundError = (error: unknown): boolean =>
  axios.isAxiosError(error) && error.response?.status === 404;

/** True for an HTTP 403, e.g. a wrong admin token. */
export const isForbiddenError = (error: unknown): boolean =>
  axios.isAxiosError(error) && error.response?.status === 403;

/** True for an HTTP 503: the service is up but its data isn't ready (e.g. no explanations yet). */
export const isServiceUnavailableError = (error: unknown): boolean =>
  axios.isAxiosError(error) && error.response?.status === 503;

export default apiService;