// How a player's name is shown. FPL's data has the full legal name ("Francisco Evanilson de
// Lima Barbosa"), which every lookup keys on (photos, profiles, predictions, links), and a short
// name ("Evanilson") that the game itself displays. These helpers only pick the text to show:
// keep passing the full name to anything that looks a player up.

// Letters that Unicode doesn't split into a base letter and an accent (like utils/text.normalize_name)
const UNSPLIT: Record<string, string> = { ø: 'o', æ: 'ae', œ: 'oe', ł: 'l', đ: 'd', ð: 'd', þ: 'th', ß: 'ss', ı: 'i' };

const fold = (s: string) =>
  s
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/[øæœłđðþßı]/g, (c) => UNSPLIT[c])
    .trim();

/**
 * The name for lists and tables. A "First Last" name stays as it is ("Erling Haaland",
 * "Kevin De Bruyne", "Jacob Ramsey" for FPL's "J.Ramsey"); anything else, such as a longer legal
 * name or a player known by another name, shows FPL's short name ("Evanilson", "Gabriel",
 * "Rodri", "Van Hecke"). Without a short name, the full name.
 */
export function displayName(name: string, webName?: string | null): string {
  const full = (name ?? '').trim();
  const web = (webName ?? '').trim();
  if (!web) return full;
  // "J.Ramsey" -> "Ramsey": FPL adds an initial to tell players apart
  const surname = fold(web.replace(/^\p{L}\.\s*/u, ''));
  const folded = fold(full);
  if (surname && folded.endsWith(` ${surname}`)) {
    const first = folded.slice(0, folded.length - surname.length).trim();
    if (first && !/\s/.test(first)) return full;
  }
  return web;
}

/** The shortest form, for one-line summaries ("Next best: Tavernier 4.6"): FPL's short name,
 *  else the last word of the full name. */
export function shortName(name: string, webName?: string | null): string {
  const web = (webName ?? '').trim();
  if (web) return web;
  const full = (name ?? '').trim();
  return full.split(/\s+/).slice(-1)[0] || full;
}
