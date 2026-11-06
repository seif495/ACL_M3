import { handleApiError, httpStatusOf } from '../../services/api';

// Helpers for the My team page: the saved team ID and the wording of its errors.

export const TEAM_ID_KEY = 'pm-fpl-team-id';
export const MAX_TEAM_ID = 99_999_999;

/** A team ID from text: digits only, 1 to 99,999,999. */
export function parseTeamId(text: string | null | undefined): number | null {
  const t = (text ?? '').trim();
  if (!/^\d{1,8}$/.test(t)) return null;
  const n = Number(t);
  return n >= 1 && n <= MAX_TEAM_ID ? n : null;
}

// Storage can throw (private windows, blocked site data): every access is wrapped.
export function readSavedTeamId(): number | null {
  try {
    return parseTeamId(window.localStorage.getItem(TEAM_ID_KEY));
  } catch {
    return null;
  }
}

export function saveTeamId(id: number): void {
  try {
    window.localStorage.setItem(TEAM_ID_KEY, String(id));
  } catch {
    /* the ID just isn't remembered */
  }
}

export function forgetTeamId(): void {
  try {
    window.localStorage.removeItem(TEAM_ID_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** A friendly title and sentence for a failed request. */
export function errorCopy(error: unknown): { title: string; description: string } {
  switch (httpStatusOf(error)) {
    case 404:
      return {
        title: 'We couldn’t find that team',
        description: 'Check the number: your team ID is the number after /entry/ in the address of your Points page on the FPL website.',
      };
    case 409:
      return {
        title: 'That team has no squad yet',
        description: 'FPL has no picks for this team yet. Once the squad is picked, come back and load it again.',
      };
    case 502:
      // The server says when FPL is updating the game after a deadline, which lasts a while
      return {
        title: 'FPL isn’t answering',
        description: handleApiError(error) || 'The FPL site is busy or being updated. Wait a minute and try again.',
      };
    case 429:
      return {
        title: 'Too many requests',
        description: 'You’ve asked for a lot of teams in a short time. Wait a minute and try again.',
      };
    default:
      return { title: 'Couldn’t load the team', description: handleApiError(error) };
  }
}

/** "1,656,876" */
export const bigNumber = (n: number | null | undefined): string => (n == null ? '–' : n.toLocaleString());
