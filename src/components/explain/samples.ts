import type {
  ExplainBeeswarm,
  ExplainContribution,
  ExplainDependence,
  ExplainFeature,
  ExplainFixture,
  ExplainGroup,
  ExplainPlayerHit,
  ExplainSummary,
  LimeWeight,
  PlayerExplanation,
} from '../../services/api';
import type { TrackRecord } from '../../services/trackRecordApi';

/*
 * SAMPLE DATA for the hidden /design page only: made-up players and numbers in the exact shape of
 * the /api/ml/explain/* endpoints, so the model-insights components can be checked without the
 * backend. The feature list mirrors the v3 model's (ml/points_model.V3_FEATURES); importances,
 * SHAP and LIME values are plausible but invented. Nothing in the app reads these.
 */

/** Small seeded PRNG (mulberry32), so the samples are identical on every load. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const round = (v: number, digits = 3) => Number(v.toFixed(digits));

// --- Features ---------------------------------------------------------------------------------

const FORM_STATS: Array<[string, string, string]> = [
  ['total_points', 'Points', 'FPL points per match'],
  ['minutes', 'Minutes', 'Minutes played per match'],
  ['started', 'Starts', 'Share of matches started'],
  ['played', 'Appearances', 'Share of matches with any minutes'],
  ['goals_scored', 'Goals', 'Goals per match'],
  ['assists', 'Assists', 'Assists per match'],
  ['bonus', 'Bonus', 'Bonus points per match'],
  ['bps', 'BPS', 'Bonus points system score per match'],
  ['ict_index', 'ICT index', 'FPL ICT index per match'],
  ['influence', 'Influence', 'FPL influence score per match'],
  ['creativity', 'Creativity', 'FPL creativity score per match'],
  ['threat', 'Threat', 'FPL threat score per match'],
  ['clean_sheets', 'Clean sheets', 'Clean sheets per match'],
  ['goals_conceded', 'Goals conceded', 'Goals conceded per match while on the pitch'],
  ['saves', 'Saves', 'Saves per match'],
  ['expected_goals', 'xG', 'Expected goals per match'],
  ['expected_assists', 'xA', 'Expected assists per match'],
  ['expected_goal_involvements', 'xGI', 'Expected goal involvements per match'],
  ['expected_goals_conceded', 'xGC', 'Expected goals conceded per match'],
  ['defensive_contribution', 'Defensive contributions', 'Defensive contributions per match'],
];

const TEAM_STATS: Array<[string, string]> = [
  ['gf', 'goals scored'],
  ['ga', 'goals conceded'],
  ['xgf', 'xG for'],
  ['xga', 'xG against'],
];

type FeatureMeta = Omit<ExplainFeature, 'importance'>;

function buildFeatureMeta(): FeatureMeta[] {
  const out: FeatureMeta[] = [];
  for (const w of [3, 6]) {
    for (const [key, label, desc] of FORM_STATS) {
      out.push({
        feature: `${key}_r${w}`,
        label: `${label}, last ${w} matches`,
        group: 'Recent form',
        description: `${desc} over the player's last ${w} matches before this gameweek.`,
      });
    }
  }
  out.push(
    { feature: 'season_matches_before', label: 'Matches this season', group: 'Season & career', description: 'Premier League matches the player has been in the squad for this season.' },
    { feature: 'season_ppg', label: 'Points per match this season', group: 'Season & career', description: 'FPL points per match so far this season (missing before his first match).' },
    { feature: 'season_minutes_share', label: 'Share of minutes this season', group: 'Season & career', description: 'Minutes played as a share of every minute available this season.' },
    { feature: 'career_matches_before', label: 'Career matches', group: 'Season & career', description: 'Premier League matches in the data before this one, across seasons.' },
    { feature: 'days_rest', label: 'Days since last match', group: 'Fixture', description: 'Days between his previous match and this one (capped at 30).' },
  );
  for (const [prefix, group, whose] of [
    ['team', 'Team strength', "His team's"],
    ['opp', 'Opponent strength', "The opponent's"],
  ] as const) {
    for (const [stat, words] of TEAM_STATS) {
      out.push({
        feature: `${prefix}_${stat}`,
        label: `${prefix === 'team' ? 'Team' : 'Opponent'} ${words}, last 6`,
        group,
        description: `${whose} ${words} per match over their last 6 matches.`,
      });
    }
  }
  out.push(
    { feature: 'fixtures_in_gw', label: 'Fixtures this gameweek', group: 'Fixture', description: 'Matches his team plays in the gameweek (2 in a double gameweek).' },
    { feature: 'was_home', label: 'Home match', group: 'Fixture', description: 'Whether his team plays this fixture at home.' },
    { feature: 'price', label: 'Price', group: 'Price & ownership', description: 'FPL price in £m at the deadline.' },
    { feature: 'selected_rank', label: 'Ownership rank', group: 'Price & ownership', description: 'Percentile of FPL ownership among all players (1 = most selected).' },
  );
  for (const [pos, name] of [
    ['GK', 'Goalkeeper'],
    ['DEF', 'Defender'],
    ['MID', 'Midfielder'],
    ['FWD', 'Forward'],
  ]) {
    out.push({ feature: `pos_${pos}`, label: name, group: 'Position', description: `Whether the player is listed as a ${name.toLowerCase()} in FPL.` });
  }
  out.push(
    { feature: 'minutes_r1', label: 'Minutes, last match', group: 'Last match', description: 'Minutes played in his previous match.' },
    { feature: 'started_r1', label: 'Started last match', group: 'Last match', description: 'Whether he started his previous match.' },
    { feature: 'total_points_r1', label: 'Points, last match', group: 'Last match', description: 'FPL points in his previous match.' },
  );
  for (const [prefix, group, whose] of [
    ['team', 'Team strength', "His team's"],
    ['opp', 'Opponent strength', "The opponent's"],
  ] as const) {
    for (const [stat, words] of TEAM_STATS) {
      out.push({
        feature: `${prefix}_${stat}20`,
        label: `${prefix === 'team' ? 'Team' : 'Opponent'} ${words}, last 20`,
        group,
        description: `${whose} ${words} per match over their last 20 matches.`,
      });
    }
  }
  return out;
}

const META = buildFeatureMeta();
const META_BY_KEY = new Map(META.map((m) => [m.feature, m]));

/** Mean |SHAP| (points) for the features that matter; the rest get small values below. */
const KEY_IMPORTANCE: Record<string, number> = {
  minutes_r3: 0.552,
  minutes_r1: 0.418,
  season_minutes_share: 0.297,
  started_r6: 0.268,
  price: 0.231,
  selected_rank: 0.204,
  total_points_r6: 0.163,
  expected_goal_involvements_r6: 0.128,
  started_r1: 0.117,
  bps_r6: 0.096,
  team_xgf20: 0.091,
  opp_xgf20: 0.083,
  season_ppg: 0.079,
  played_r6: 0.071,
  was_home: 0.064,
  opp_ga20: 0.058,
  threat_r6: 0.052,
  pos_GK: 0.047,
  team_ga20: 0.044,
  expected_goals_r3: 0.041,
  total_points_r1: 0.038,
  clean_sheets_r6: 0.033,
  career_matches_before: 0.031,
  fixtures_in_gw: 0.029,
};

function buildFeatures(): ExplainFeature[] {
  const rand = rng(7);
  return META.map((m) => ({ ...m, importance: KEY_IMPORTANCE[m.feature] ?? round(0.003 + rand() * 0.024) })).sort(
    (a, b) => b.importance - a.importance,
  );
}

const FEATURES = buildFeatures();

function buildGroups(features: ExplainFeature[]): ExplainGroup[] {
  const sums = new Map<string, number>();
  for (const f of features) sums.set(f.group, (sums.get(f.group) ?? 0) + f.importance);
  return [...sums.entries()].map(([group, importance]) => ({ group, importance: round(importance) })).sort((a, b) => b.importance - a.importance);
}

// --- Beeswarm ----------------------------------------------------------------------------------

/** SHAP as a function of the value percentile, per feature: [slope, offset, noise, missing share, curve]. */
const SHAPES: Record<string, [number, number, number, number, number]> = {
  minutes_r3: [1.9, -0.62, 0.12, 0, 2.2],
  minutes_r1: [1.4, -0.55, 0.1, 0.02, 1.6],
  season_minutes_share: [1.0, -0.45, 0.09, 0.03, 1.8],
  started_r6: [0.95, -0.4, 0.08, 0, 1.5],
  price: [0.75, -0.18, 0.08, 0, 2.6],
  selected_rank: [0.62, -0.2, 0.09, 0, 2.4],
  total_points_r6: [0.55, -0.16, 0.08, 0, 2.2],
  expected_goal_involvements_r6: [0.48, -0.1, 0.06, 0, 3],
  started_r1: [0.42, -0.2, 0.05, 0.02, 1],
  bps_r6: [0.34, -0.12, 0.05, 0, 1.8],
};

function buildBeeswarm(): ExplainBeeswarm[] {
  const top = FEATURES.slice(0, 10);
  const n = 320;
  return top.map((f, fi) => {
    const rand = rng(100 + fi);
    const [slope, offset, noise, missing, curve] = SHAPES[f.feature] ?? [0.3, -0.1, 0.05, 0, 1.5];
    const points: Array<[number | null, number]> = [];
    for (let i = 0; i < n; i += 1) {
      if (rand() < missing) {
        points.push([null, round(-0.05 - rand() * 0.25)]);
        continue;
      }
      const p = rand();
      const shap = offset + slope * p ** curve + (rand() - 0.5) * 2 * noise;
      points.push([round(p, 3), round(shap)]);
    }
    return { feature: f.feature, label: f.label, points };
  });
}

// --- Players -----------------------------------------------------------------------------------

export const SAMPLE_TOP_PLAYERS: ExplainPlayerHit[] = [
  { name: 'Sample Striker', element: 9001, team: 'Arsenal', position: 'FWD', predicted_points: 6.2 },
  { name: 'Mock Midfielder', element: 9002, team: 'Newcastle', position: 'MID', predicted_points: 6.0 },
  { name: 'Example Winger', element: 9003, team: 'Liverpool', position: 'MID', predicted_points: 5.9 },
  { name: 'Second Striker', element: 9004, team: 'Chelsea', position: 'FWD', predicted_points: 5.8 },
  { name: 'Demo Defender', element: 9005, team: 'Chelsea', position: 'DEF', predicted_points: 5.6 },
  { name: 'Sample Keeper', element: 9006, team: 'Arsenal', position: 'GK', predicted_points: 5.2 },
  { name: 'Placeholder Playmaker', element: 9007, team: 'Man City', position: 'MID', predicted_points: 5.1 },
  { name: 'Sample Full-back', element: 9008, team: 'Spurs', position: 'DEF', predicted_points: 4.9 },
  { name: 'Test Target Man', element: 9009, team: 'Brentford', position: 'FWD', predicted_points: 4.8 },
  { name: 'Mock Wing-back', element: 9010, team: 'Aston Villa', position: 'DEF', predicted_points: 4.7 },
  { name: 'Demo Number Ten', element: 9011, team: 'Brighton', position: 'MID', predicted_points: 4.6 },
  { name: 'Sample Centre-back', element: 9012, team: 'Crystal Palace', position: 'DEF', predicted_points: 4.5 },
];

export const SAMPLE_SUMMARY: ExplainSummary = {
  season: '2026-27',
  gameweek: 9,
  generated_at: '2026-10-22T05:12:00+00:00',
  base_value: 1.384,
  rows: 642,
  players: 611,
  model: {
    version: 'v3',
    trained_at: '2026-10-19T04:31:10+00:00',
    n_trees: 174,
    seasons: ['2020-21', '2021-22', '2022-23', '2023-24', '2024-25', '2025-26', '2026-27'],
    rows: 166965,
    source: 'live',
    test_metrics: {
      v3: { rows: 33640, mae: 1.007, rmse: 1.946, spearman: 0.718, top20_points: 4.7, captain_points: 6.614 },
      last3_avg: { rows: 33198, mae: 1.088, rmse: 2.202, spearman: 0.71, top20_points: 3.777, captain_points: 4.455 },
      last6_avg: { rows: 33198, mae: 1.091, rmse: 2.134, spearman: 0.689, top20_points: 3.898, captain_points: 4.75 },
      season_ppg: { rows: 32129, mae: 1.084, rmse: 2.104, spearman: 0.691, top20_points: 4.205, captain_points: 5.81 },
    },
  },
  groups: buildGroups(FEATURES),
  features: FEATURES,
  beeswarm: buildBeeswarm(),
  top_players: SAMPLE_TOP_PLAYERS,
};

// --- One player's explanation -----------------------------------------------------------------

/** Every feature's contribution: the listed ones as given, the rest small, summing to `total`. */
function contributions(
  seed: number,
  total: number,
  listed: Array<[string, number | null, number]>,
  values: Record<string, number | null>,
): ExplainContribution[] {
  const rand = rng(seed);
  const listedKeys = new Set(listed.map(([k]) => k));
  const rest = META.filter((m) => !listedKeys.has(m.feature));
  const listedSum = listed.reduce((s, [, , shap]) => s + shap, 0);
  const raw = rest.map(() => (rand() - 0.45) * 0.03);
  const drift = total - listedSum - raw.reduce((s, v) => s + v, 0);
  const restShap = raw.map((v) => v + drift / raw.length);
  const rows: ExplainContribution[] = [
    ...listed.map(([feature, value, shap]) => {
      const m = META_BY_KEY.get(feature)!;
      return { feature, label: m.label, group: m.group, value, shap: round(shap, 4) };
    }),
    ...rest.map((m, i) => ({
      feature: m.feature,
      label: m.label,
      group: m.group,
      value: values[m.feature] ?? round(rand() * 3, 2),
      shap: round(restShap[i], 4),
    })),
  ];
  return rows.sort((a, b) => Math.abs(b.shap) - Math.abs(a.shap));
}

function lime(weights: Array<[string, string, number, number | null]>, extra: Omit<ExplainFixture['lime'], 'weights'>): ExplainFixture['lime'] {
  const out: LimeWeight[] = weights.map(([feature, condition, weight, value]) => ({
    feature,
    label: META_BY_KEY.get(feature)?.label ?? feature,
    // The backend writes rules with the feature's label, as here.
    condition: condition.replace(feature, META_BY_KEY.get(feature)?.label ?? feature),
    weight,
    value,
  }));
  return { ...extra, weights: out };
}

const BASE = 1.384;

/** Raw values for the striker's features that aren't listed with their own contribution. */
const STRIKER_VALUES: Record<string, number | null> = {
  pos_GK: 0,
  pos_DEF: 0,
  pos_MID: 0,
  pos_FWD: 1,
  started_r1: 1,
  fixtures_in_gw: 2,
  season_matches_before: 8,
  season_ppg: 6.38,
  career_matches_before: 187,
  total_points_r1: 9,
  started_r3: 1,
  started_r6: 1,
  played_r3: 1,
  played_r6: 1,
};

const fixtureA: ExplainFixture = {
  fixture: 81,
  opponent: 'Brighton',
  is_home: true,
  kickoff_time: '2026-10-24T14:00:00+00:00',
  base_value: BASE,
  model_points: 4.862,
  contributions: contributions(
    11,
    4.862 - BASE,
    [
      ['minutes_r3', 84.33, 0.912],
      ['price', 9.6, 0.604],
      ['selected_rank', 0.97, 0.488],
      ['expected_goal_involvements_r6', 0.81, 0.452],
      ['minutes_r1', 90, 0.401],
      ['season_minutes_share', 0.88, 0.315],
      ['total_points_r6', 6.17, 0.276],
      ['opp_xga20', 1.62, 0.198],
      ['was_home', 1, 0.142],
      ['days_rest', 3, -0.168],
      ['team_xgf20', 1.94, 0.121],
      ['clean_sheets_r6', 0.33, -0.052],
      ['bps_r6', 24.5, 0.073],
    ],
    STRIKER_VALUES,
  ),
  lime: lime(
    [
      ['minutes_r3', 'minutes_r3 > 78.33', 1.024, 84.33],
      ['minutes_r1', 'minutes_r1 > 85.00', 0.611, 90],
      ['price', 'price > 7.50', 0.548, 9.6],
      ['selected_rank', 'selected_rank > 0.91', 0.432, 0.97],
      ['expected_goal_involvements_r6', 'expected_goal_involvements_r6 > 0.42', 0.389, 0.81],
      ['days_rest', 'days_rest <= 4.00', -0.214, 3],
      ['season_minutes_share', '0.71 < season_minutes_share <= 0.93', 0.198, 0.88],
      ['opp_xga20', 'opp_xga20 > 1.48', 0.161, 1.62],
      ['was_home', 'was_home > 0.50', 0.122, 1],
      ['pos_GK', 'pos_GK <= 0.00', 0.071, 0],
    ],
    { intercept: 1.296, local_prediction: 4.638, model_prediction: 4.862, score: 0.61, num_samples: 5000, kernel_width: 6.36 },
  ),
};

const fixtureB: ExplainFixture = {
  fixture: 88,
  opponent: 'Everton',
  is_home: false,
  kickoff_time: '2026-10-27T19:45:00+00:00',
  base_value: BASE,
  model_points: 3.418,
  contributions: contributions(
    12,
    3.418 - BASE,
    [
      ['minutes_r3', 84.33, 0.884],
      ['price', 9.6, 0.592],
      ['selected_rank', 0.97, 0.471],
      ['expected_goal_involvements_r6', 0.81, 0.437],
      ['minutes_r1', 90, 0.388],
      ['opp_xga20', 1.08, -0.236],
      ['was_home', 0, -0.181],
      ['season_minutes_share', 0.88, 0.302],
      ['days_rest', 3, -0.213],
      ['total_points_r6', 6.17, 0.264],
      ['fixtures_in_gw', 2, -0.094],
    ],
    STRIKER_VALUES,
  ),
  lime: lime(
    [
      ['minutes_r3', 'minutes_r3 > 78.33', 0.987, 84.33],
      ['minutes_r1', 'minutes_r1 > 85.00', 0.594, 90],
      ['price', 'price > 7.50', 0.531, 9.6],
      ['selected_rank', 'selected_rank > 0.91', 0.418, 0.97],
      ['opp_xga20', '0.98 < opp_xga20 <= 1.21', -0.276, 1.08],
      ['was_home', 'was_home <= 0.50', -0.198, 0],
      ['expected_goal_involvements_r6', 'expected_goal_involvements_r6 > 0.42', 0.371, 0.81],
      ['days_rest', 'days_rest <= 4.00', -0.231, 3],
      ['fixtures_in_gw', 'fixtures_in_gw > 1.00', -0.087, 2],
    ],
    { intercept: 1.412, local_prediction: 3.105, model_prediction: 3.418, score: 0.47, num_samples: 5000, kernel_width: 6.36 },
  ),
};

/** A double gameweek for a doubtful player, so every adjustment step shows. */
export const SAMPLE_PLAYER_EXPLANATION: PlayerExplanation = {
  player: {
    name: 'Sample Striker',
    element: 9001,
    team: 'Arsenal',
    position: 'FWD',
    status: 'd',
    chance_of_playing_next_round: 75,
    news: 'Sample news: knock, 75% chance of playing',
  },
  season: '2026-27',
  gameweek: 9,
  fixtures: [fixtureA, fixtureB],
  model_points: round(fixtureA.model_points + fixtureB.model_points),
  availability_factor: 0.91,
  predicted_points: round((fixtureA.model_points + fixtureB.model_points) * 0.91, 2),
};

/** Shaped like an axios 404, to show the "not in this gameweek" state. */
export const SAMPLE_NOT_FOUND_ERROR = { isAxiosError: true, response: { status: 404 } };

// --- Dependence --------------------------------------------------------------------------------

const POSITIONS = ['GK', 'DEF', 'DEF', 'DEF', 'MID', 'MID', 'MID', 'FWD'];

export const SAMPLE_DEPENDENCE: ExplainDependence = (() => {
  const rand = rng(42);
  const meta = META_BY_KEY.get('minutes_r3')!;
  const points = Array.from({ length: 260 }, (_, i) => {
    const position = POSITIONS[Math.floor(rand() * POSITIONS.length)];
    if (rand() < 0.03) return { name: `Sample Player ${i + 1}`, element: 9100 + i, position, value: null, shap: round(-0.1 - rand() * 0.2) };
    // Many players sit at 0 (not playing) or near 90 (nailed starters).
    const r = rand();
    const value = r < 0.25 ? rand() * 8 : r < 0.6 ? 60 + rand() * 30 : rand() * 90;
    const base = -0.95 + 1.45 / (1 + Math.exp(-(value - 55) / 9));
    const posShift = position === 'FWD' || position === 'MID' ? 0.08 : position === 'GK' ? -0.05 : 0;
    const shap = base + posShift * (value / 90) + (rand() - 0.5) * 0.22;
    return { name: `Sample Player ${i + 1}`, element: 9100 + i, position, value: round(value, 2), shap: round(shap) };
  });
  return { feature: meta.feature, label: meta.label, group: meta.group, description: meta.description, points };
})();

// --- Track record ------------------------------------------------------------------------------

/** Eight made-up finished gameweeks in the shape of /api/ml/track-record. */
export const SAMPLE_TRACK_RECORD: TrackRecord = (() => {
  const rand = rng(7);
  const gameweeks = Array.from({ length: 8 }, (_, i) => {
    const gw = 9 + i;
    const best = 70 + Math.round(rand() * 25);
    const fpl = 34 + Math.round(rand() * 26);
    const model = Math.max(20, fpl + Math.round((rand() - 0.42) * 22));
    const maeFpl = round(1.15 + rand() * 0.25, 3);
    const maeModel = round(maeFpl + (rand() - 0.6) * 0.12, 3);
    const captainPoints = [2, 5, 6, 8, 9, 12, 3, 7][i];
    const average = 44 + Math.round(rand() * 20);
    return {
      gameweek: gw,
      deadline: `2026-11-${String(7 + i * 7).padStart(2, '0')}T17:30:00+00:00`,
      archived_at: `2026-11-${String(7 + i * 7).padStart(2, '0')}T12:00:00+00:00`,
      model: 'v4',
      fpl_source: 'element_snapshots' as const,
      mae: { model: maeModel, fpl: maeFpl, n: 330 },
      top10: { model, fpl, best, n: 10 },
      captain: { element: 100 + i, name: `Sample Player ${i + 1}`, predicted: round(6 + rand() * 2, 2), points: captainPoints, hit: captainPoints >= 6 },
      players: 640,
      squad: { points: average + Math.round((rand() - 0.4) * 30), bench_points: Math.round(rand() * 10), predicted: round(60 + rand() * 12, 2), average, highest: average + 60 + Math.round(rand() * 40), formation: '3-4-3' },
    };
  });
  const avg = (f: (g: (typeof gameweeks)[number]) => number) => gameweeks.reduce((a, g) => a + f(g), 0) / gameweeks.length;
  const hits = gameweeks.filter((g) => g.captain.hit).length;
  const squadBeat = gameweeks.filter((g) => g.squad.points > g.squad.average).length;
  return {
    season: '2026-27',
    gameweeks,
    missing: [
      { gameweek: 1, reason: 'No prediction was saved before this deadline' },
      { gameweek: 2, reason: 'No prediction was saved before this deadline' },
      { gameweek: 3, reason: 'No prediction was saved before this deadline' },
      { gameweek: 7, reason: 'Squad recorded by hand; no predictions were saved' },
    ],
    totals: {
      gameweeks: gameweeks.length,
      compared_gameweeks: gameweeks.length,
      mae: { model: round(avg((g) => g.mae.model), 3), fpl: round(avg((g) => g.mae.fpl), 3), n: 330 * gameweeks.length, gameweeks: gameweeks.length },
      top10: { model: round(avg((g) => g.top10.model), 2), fpl: round(avg((g) => g.top10.fpl), 2), best: round(avg((g) => g.top10.best), 2), gameweeks: gameweeks.length },
      captain: { hits, of: gameweeks.length, rate: round(hits / gameweeks.length), mean_points: round(avg((g) => g.captain.points), 2), threshold: 6 },
      squad: { mean_points: round(avg((g) => g.squad.points), 2), mean_average: round(avg((g) => g.squad.average), 2), beat_average: squadBeat, of: gameweeks.length, gameweeks: gameweeks.length },
      beating_fpl: {
        mae: { wins: gameweeks.filter((g) => g.mae.model < g.mae.fpl).length, of: gameweeks.length },
        top10: { wins: gameweeks.filter((g) => g.top10.model > g.top10.fpl).length, of: gameweeks.length },
      },
    },
    calibration: [
      { label: '0-2', low: 0, high: 2, n: 4100, predicted: 0.62, actual: 0.71 },
      { label: '2-4', low: 2, high: 4, n: 1150, predicted: 2.85, actual: 2.62 },
      { label: '4-6', low: 4, high: 6, n: 260, predicted: 4.7, actual: 4.31 },
      { label: '6+', low: 6, high: null, n: 38, predicted: 6.9, actual: 5.84 },
    ],
    note: null,
  };
})();
