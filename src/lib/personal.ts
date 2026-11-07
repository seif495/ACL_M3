import { useCallback, useEffect, useState } from 'react';

/*
 * Things a visitor picks for themselves, kept only in this browser's local storage (no accounts):
 * a watchlist of players (full names, at most 30) and a favourite club (graph name). Every storage
 * access is wrapped, since it can throw in private windows or with site data blocked; other tabs
 * are kept in sync through the `storage` event, this tab through a custom event.
 */

export const WATCHLIST_KEY = 'pm-watchlist';
export const FAVOURITE_CLUB_KEY = 'pm-favourite-club';
export const MAX_WATCHLIST = 30;
const CHANGED = 'pm-personal-changed';

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* not remembered */
  }
  try {
    window.dispatchEvent(new Event(CHANGED));
  } catch {
    /* no window */
  }
}

export function readWatchlist(): string[] {
  try {
    const parsed: unknown = JSON.parse(read(WATCHLIST_KEY) ?? '[]');
    if (!Array.isArray(parsed)) return [];
    const names = parsed.filter((n): n is string => typeof n === 'string' && n.trim().length > 0 && n.length <= 120);
    return Array.from(new Set(names)).slice(0, MAX_WATCHLIST);
  } catch {
    return [];
  }
}

export function readFavouriteClub(): string | null {
  const v = read(FAVOURITE_CLUB_KEY);
  return v && v.length <= 60 ? v : null;
}

/** Re-reads `get` whenever this or another tab changes the stored values. */
function useStored<T>(get: () => T, same: (a: T, b: T) => boolean): T {
  const [value, setValue] = useState<T>(get);
  useEffect(() => {
    const refresh = () => setValue((prev) => {
      const next = get();
      return same(prev, next) ? prev : next;
    });
    window.addEventListener('storage', refresh);
    window.addEventListener(CHANGED, refresh);
    return () => {
      window.removeEventListener('storage', refresh);
      window.removeEventListener(CHANGED, refresh);
    };
  }, [get, same]);
  return value;
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((v, i) => v === b[i]);
const sameValue = (a: string | null, b: string | null) => a === b;

export function useWatchlist() {
  const names = useStored(readWatchlist, sameList);
  const has = useCallback((name: string) => names.includes(name), [names]);
  const add = useCallback((name: string) => {
    const list = readWatchlist();
    if (!list.includes(name) && list.length < MAX_WATCHLIST) write(WATCHLIST_KEY, JSON.stringify([...list, name]));
  }, []);
  const remove = useCallback((name: string) => {
    const list = readWatchlist().filter((n) => n !== name);
    write(WATCHLIST_KEY, list.length ? JSON.stringify(list) : null);
  }, []);
  const toggle = useCallback((name: string) => (readWatchlist().includes(name) ? remove(name) : add(name)), [add, remove]);
  return { names, has, add, remove, toggle, full: names.length >= MAX_WATCHLIST };
}

export function useFavouriteClub() {
  const club = useStored(readFavouriteClub, sameValue);
  const set = useCallback((name: string | null) => write(FAVOURITE_CLUB_KEY, name), []);
  const toggle = useCallback((name: string) => write(FAVOURITE_CLUB_KEY, readFavouriteClub() === name ? null : name), []);
  return { club, set, toggle };
}
