/**
 * Club colours (primary, secondary) as "R G B" triplets for CSS variables, keyed by the graph's
 * club names. Used as glows and accents on club and match pages over the dark theme, never as
 * text backgrounds, so contrast stays the design system's. Colours are facts about the clubs,
 * not their marks.
 */
const COLORS: Record<string, [string, string]> = {
  Arsenal: ['239 1 7', '255 255 255'],
  'Aston Villa': ['103 14 54', '149 191 229'],
  Bournemouth: ['218 41 28', '20 20 20'],
  Brentford: ['227 6 19', '255 255 255'],
  Brighton: ['0 87 184', '255 255 255'],
  Burnley: ['108 29 69', '153 214 234'],
  Chelsea: ['3 70 148', '255 255 255'],
  'Coventry City': ['89 178 230', '255 255 255'],
  'Crystal Palace': ['27 69 143', '196 18 46'],
  Everton: ['0 51 153', '255 255 255'],
  Fulham: ['230 230 230', '20 20 20'],
  'Hull City': ['245 160 0', '20 20 20'],
  Ipswich: ['0 51 160', '255 255 255'],
  Leeds: ['29 66 138', '255 205 0'],
  Leicester: ['0 83 160', '253 190 17'],
  Liverpool: ['200 16 46', '0 178 169'],
  Luton: ['247 143 30', '0 34 82'],
  'Man City': ['108 171 221', '28 44 91'],
  'Man Utd': ['218 41 28', '251 225 34'],
  Newcastle: ['90 90 90', '255 255 255'],
  Norwich: ['0 166 80', '255 242 0'],
  "Nott'm Forest": ['221 0 0', '255 255 255'],
  'Sheffield Utd': ['238 39 55', '20 20 20'],
  Southampton: ['215 25 32', '255 255 255'],
  Spurs: ['19 34 87', '255 255 255'],
  Sunderland: ['235 23 47', '255 255 255'],
  Watford: ['251 238 35', '237 33 39'],
  'West Brom': ['18 47 103', '255 255 255'],
  'West Ham': ['122 38 58', '27 177 231'],
  Wolves: ['253 185 19', '35 31 32'],
};

const FALLBACK: [string, string] = ['0 230 140', '0 200 230'];

export function clubColors(team: string | null | undefined): { primary: string; secondary: string } {
  const [primary, secondary] = (team && COLORS[team]) || FALLBACK;
  return { primary, secondary };
}

/** "Man City" -> "man-city", "Nott'm Forest" -> "nottm-forest" (services/teams.slugify). */
export function teamSlug(team: string): string {
  return team
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export const teamPath = (team: string) => `/teams/${teamSlug(team)}`;
export const matchPath = (fixtureId: string) => `/matches/${encodeURIComponent(fixtureId)}`;
