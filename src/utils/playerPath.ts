// A player's page URL: /players/<slug>. The slug is the backend's (services/player_pages.py
// slugify_player = utils/text.normalize_name joined with "-"): lowercase, accents and letters
// such as ø, æ and ł folded, everything that isn't a letter or digit becomes "-".
// tests/test_player_pages.py has the same expected strings (SLUG_CASES); keep them in step:
//   "Erling Haaland"            -> erling-haaland
//   "Martin Ødegaard"           -> martin-odegaard
//   "N'Golo Kanté"              -> n-golo-kante
//   "Łukasz Fabiański"          -> lukasz-fabianski
//   "Weiß Straße"               -> weiss-strasse
//   "Æthelwulf Þór"             -> aethelwulf-thor
//   "Trent Alexander-Arnold"    -> trent-alexander-arnold
//   "João Pedro Junqueira de Jesus" -> joao-pedro-junqueira-de-jesus

// Letters that Unicode doesn't split into a base letter and an accent (utils/text._SPECIAL_LETTERS)
const UNSPLIT: Record<string, string> = { ø: 'o', æ: 'ae', œ: 'oe', ł: 'l', đ: 'd', ð: 'd', þ: 'th', ß: 'ss', ı: 'i' };

export function playerSlug(name: string): string {
  return (name ?? '')
    .toLowerCase()
    .replace(/[øæœłđðþßı]/g, (c) => UNSPLIT[c])
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** The player's page, or the players list for a name with no usable letters. */
export function playerPath(name: string): string {
  const slug = playerSlug(name);
  return slug ? `/players/${slug}` : '/players';
}
