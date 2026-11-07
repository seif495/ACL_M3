/** A chance (0..1) as a whole percent. Never shows a rounded 0% or 100% for a result that can
 * still go either way: "<1%" and ">99%". Exactly 0 and 1 stay "0%" and "100%". */
export function formatChance(p: number): string {
  if (p <= 0) return '0%';
  if (p >= 1) return '100%';
  if (p < 0.01) return '<1%';
  if (p > 0.99) return '>99%';
  return `${Math.round(p * 100)}%`;
}

/** The same for a spoken label: "less than 1 percent". */
export function spokenChance(p: number): string {
  if (p <= 0) return '0 percent';
  if (p >= 1) return '100 percent';
  if (p < 0.01) return 'less than 1 percent';
  if (p > 0.99) return 'more than 99 percent';
  return `${Math.round(p * 100)} percent`;
}

const ORDINAL_RULES = new Intl.PluralRules('en-GB', { type: 'ordinal' });
const SUFFIX: Record<string, string> = { one: 'st', two: 'nd', few: 'rd', other: 'th' };

/** 1 -> "1st", 22 -> "22nd". */
export function ordinal(n: number): string {
  return `${n}${SUFFIX[ORDINAL_RULES.select(n)]}`;
}
