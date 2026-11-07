import axios, { type AxiosInstance } from 'axios';

// Production builds use relative URLs: the site and the API share an origin
// (Caddy serves dist/ and proxies /api, /health and /ws — see deploy/Caddyfile).
export const API_BASE_URL = import.meta.env.MODE === 'production' ? '' : (import.meta.env.VITE_API_URL || '');

/** An axios client for the site's API; every client in src/services builds on this. */
export function createApiClient(timeout = 60000): AxiosInstance {
  return axios.create({ baseURL: API_BASE_URL, headers: { 'Content-Type': 'application/json' }, timeout });
}
