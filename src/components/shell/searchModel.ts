import { CLUB_MAPPING, getClubAbbreviation } from '../../utils/clubLogos';
import type { SearchFixture } from '../../services/searchApi';

export interface SitePage {
  to: string;
  title: string;
  keywords: string;
}

export const SITE_PAGES: SitePage[] = [
  { to: '/', title: 'Home', keywords: 'dashboard start' },
  { to: '/fixtures', title: 'Fixtures', keywords: 'results scores gameweek schedule matches' },
  { to: '/fixtures?view=ticker', title: 'Fixture difficulty ticker', keywords: 'ticker difficulty fdr run schedule' },
  { to: '/table', title: 'Table', keywords: 'league standings positions' },
  { to: '/teams', title: 'Clubs', keywords: 'teams squads' },
  { to: '/my-team', title: 'My team', keywords: 'transfers squad import plan wildcard chips fpl id' },
  { to: '/team-of-the-week', title: 'Team of the Week', keywords: 'dream team best xi totw ml manager' },
  { to: '/players', title: 'Players', keywords: 'stats leaders search' },
  { to: '/predictions', title: 'Predictions', keywords: 'predicted points forecast next gameweek' },
  { to: '/insights', title: 'Insights', keywords: 'captain captains differentials picks ownership' },
  { to: '/model', title: 'Model insights', keywords: 'shap explain track record accuracy' },
  { to: '/assistant', title: 'Assistant', keywords: 'ask chat question ai' },
  { to: '/compare', title: 'Compare players', keywords: 'versus head to head' },
  { to: '/trivia', title: 'Trivia', keywords: 'quiz game' },
  { to: '/glossary', title: 'Glossary', keywords: 'terms meaning definitions xg bps ict' },
  { to: '/settings', title: 'Settings', keywords: 'model preferences connection' },
  { to: '/privacy', title: 'Privacy', keywords: 'cookies data policy' },
  { to: '/terms', title: 'Terms', keywords: 'conditions legal' },
  { to: '/contact', title: 'Contact', keywords: 'feedback email support' },
];

/** Lowercase, accents folded, anything but letters and digits is a space. */
export function norm(text: string): string {
  return (text ?? '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Every token starts some word of the text. */
function tokensMatch(tokens: string[], text: string): boolean {
  if (!tokens.length) return false;
  const words = text.split(' ');
  return tokens.every((t) => words.some((w) => w.startsWith(t)));
}

/** Names a club answers to: its graph name, its FPL code and the longer names for the same code. */
export function clubAliases(club: string): string[] {
  const code = getClubAbbreviation(club);
  const aliases = new Set<string>([norm(club), norm(code)]);
  for (const [name, info] of Object.entries(CLUB_MAPPING)) {
    if (info.abbreviation === code) aliases.add(norm(name));
  }
  return [...aliases].filter(Boolean);
}

const CONNECTORS = new Set(['v', 'vs', 'versus']);

function clubMatchesText(club: string, text: string): boolean {
  const tokens = norm(text).split(' ').filter(Boolean);
  return tokens.length > 0 && clubAliases(club).some((a) => tokensMatch(tokens, a));
}

export function searchClubs(clubs: string[], query: string, limit = 4): string[] {
  const q = norm(query);
  if (q.length < 2) return [];
  const hits = clubs.filter((c) => clubMatchesText(c, q));
  const rank = (c: string) => (clubAliases(c).some((a) => a.startsWith(q)) ? 0 : 1);
  return hits.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b)).slice(0, limit);
}

/**
 * "Arsenal v Hull", "ars hul", "arsenal hull": the fixtures between a club the first words name
 * and one the rest name. A "v"/"vs" says where to split; without one every split is tried.
 */
export function searchFixtures(clubs: string[], fixtures: SearchFixture[], query: string, limit = 5): SearchFixture[] {
  const tokens = norm(query).split(' ').filter(Boolean);
  const connector = tokens.findIndex((t, i) => i > 0 && CONNECTORS.has(t));
  const words = tokens.filter((t) => !CONNECTORS.has(t));
  if (words.length < 2) return [];
  const splits = connector > 0 ? [connector] : words.map((_, i) => i).filter((i) => i > 0);
  const found = new Map<string, { f: SearchFixture; order: number }>();
  for (const at of splits) {
    const left = words.slice(0, at).join(' ');
    const right = words.slice(at).join(' ');
    const as = clubs.filter((c) => clubMatchesText(c, left));
    const bs = clubs.filter((c) => clubMatchesText(c, right));
    if (!as.length || !bs.length) continue;
    for (const f of fixtures) {
      const forward = as.includes(f.home) && bs.includes(f.away);
      const backward = as.includes(f.away) && bs.includes(f.home);
      if ((forward || backward) && !found.has(f.id)) found.set(f.id, { f, order: forward ? 0 : 1 });
    }
  }
  return [...found.values()]
    .sort((a, b) => a.order - b.order || a.f.gameweek - b.f.gameweek)
    .map((x) => x.f)
    .slice(0, limit);
}

export function searchPages(query: string, limit = 5): SitePage[] {
  const tokens = norm(query).split(' ').filter(Boolean);
  if (!tokens.length || norm(query).length < 2) return [];
  const score = (p: SitePage) => {
    const title = norm(p.title);
    if (title === norm(query)) return 0;
    if (tokensMatch(tokens, title)) return 1;
    return tokensMatch(tokens, `${title} ${norm(p.keywords)}`) ? 2 : 3;
  };
  return SITE_PAGES.map((p) => ({ p, s: score(p) }))
    .filter((x) => x.s < 3)
    .sort((a, b) => a.s - b.s)
    .slice(0, limit)
    .map((x) => x.p);
}
