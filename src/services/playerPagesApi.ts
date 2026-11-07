import { createApiClient } from './http';

const http = createApiClient();

/** GET /api/players/by-slug/{slug}: the graph name behind a player page's URL (404 if unknown). */
export interface PlayerBySlug {
  name: string;
  slug: string;
}

export async function getPlayerBySlug(slug: string, signal?: AbortSignal): Promise<PlayerBySlug> {
  const { data } = await http.get<PlayerBySlug>(`/api/players/by-slug/${encodeURIComponent(slug)}`, { signal });
  return data;
}
