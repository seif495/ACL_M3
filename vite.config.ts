import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// Public origin of the site, without a trailing slash. index.html's `%SITE_URL%` (canonical,
// Open Graph tags) and the generated robots.txt / sitemap.xml all use it. Override it with
// SITE_URL in the environment or a .env file; deploy/web.Dockerfile takes it as a build arg.
const DEFAULT_SITE_URL = 'https://20-215-33-13.sslip.io';

/**
 * Accepts a full URL or a Caddy site address, which may list several addresses separated by
 * commas or spaces ("pitchme.example.cloudapp.azure.com, 1-2-3-4.sslip.io") and may omit the
 * scheme. Uses the first entry as the canonical origin. Anything unusable (e.g. ":80") falls back
 * to the default with a warning: SEO metadata must never break a deploy.
 */
export function toSiteUrl(raw: string | undefined): string {
  const first = (raw ?? '').split(/[,\s]+/).find(Boolean);
  if (!first) return DEFAULT_SITE_URL;
  const withScheme = /^https?:\/\//i.test(first) ? first : `https://${first}`;
  const url = withScheme.replace(/\/+$/, '');
  if (/^https?:\/\/[a-z0-9.-]+(:\d+)?$/i.test(url)) return url.toLowerCase();
  console.warn(`[site] Ignoring unusable SITE_URL "${raw}"; using ${DEFAULT_SITE_URL}`);
  return DEFAULT_SITE_URL;
}

// Indexable fixed routes for sitemap-pages.xml. Keep in sync with the routes in src/App.tsx and
// the page_routes snippet in deploy/Caddyfile. Left out on purpose: /settings (per-browser
// preferences), /design (internal reference page), /stats (private), and the /qa and /search
// redirects. Club and match pages are in /sitemap-data.xml, which Caddy serves from the backend
// (services/seo.py) because they change with the season's data.
const SITEMAP_ROUTES: { path: string; changefreq: 'daily' | 'weekly' | 'monthly'; priority: number }[] = [
  { path: '/', changefreq: 'daily', priority: 1.0 },
  { path: '/fixtures', changefreq: 'daily', priority: 0.9 },
  { path: '/table', changefreq: 'daily', priority: 0.9 },
  { path: '/teams', changefreq: 'daily', priority: 0.8 },
  { path: '/predictions', changefreq: 'daily', priority: 0.9 },
  { path: '/team-of-the-week', changefreq: 'daily', priority: 0.8 },
  { path: '/players', changefreq: 'daily', priority: 0.8 },
  { path: '/assistant', changefreq: 'weekly', priority: 0.7 },
  { path: '/compare', changefreq: 'weekly', priority: 0.6 },
  { path: '/model', changefreq: 'daily', priority: 0.6 },
  { path: '/trivia', changefreq: 'weekly', priority: 0.5 },
  { path: '/contact', changefreq: 'monthly', priority: 0.3 },
  { path: '/privacy', changefreq: 'monthly', priority: 0.2 },
  { path: '/terms', changefreq: 'monthly', priority: 0.2 },
];

function robotsTxt(siteUrl: string): string {
  return [
    'User-agent: *',
    'Allow: /',
    'Disallow: /api/',
    'Disallow: /docs',
    'Disallow: /openapi.json',
    'Disallow: /design',
    '',
    `Sitemap: ${siteUrl}/sitemap.xml`,
    '',
  ].join('\n');
}

// sitemap.xml is an index of these (robots.txt points at it)
const SITEMAPS = ['/sitemap-pages.xml', '/sitemap-data.xml'];

function sitemapIndexXml(siteUrl: string): string {
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...SITEMAPS.map((path) => `  <sitemap>\n    <loc>${siteUrl}${path}</loc>\n  </sitemap>`),
    '</sitemapindex>',
    '',
  ].join('\n');
}

function sitemapPagesXml(siteUrl: string): string {
  const urls = SITEMAP_ROUTES.map(
    ({ path, changefreq, priority }) =>
      `  <url>\n    <loc>${siteUrl}${path}</loc>\n    <changefreq>${changefreq}</changefreq>\n    <priority>${priority.toFixed(1)}</priority>\n  </url>`,
  );
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls,
    '</urlset>',
    '',
  ].join('\n');
}

/**
 * Fills `%SITE_URL%` in index.html and emits robots.txt, sitemap.xml (an index) and
 * sitemap-pages.xml into the build.
 */
function siteEssentials(siteUrl: string): Plugin {
  return {
    name: 'pitchme-site-essentials',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => html.replace(/%SITE_URL%/g, () => siteUrl),
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: robotsTxt(siteUrl) });
      this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: sitemapIndexXml(siteUrl) });
      this.emitFile({ type: 'asset', fileName: 'sitemap-pages.xml', source: sitemapPagesXml(siteUrl) });
    },
  };
}

// The dev server proxies the API so the app is same-origin, like production.
// API_PROXY_TARGET picks the backend: a local uvicorn by default, or the deployed site
// with `npm run dev:live` (mode "live" reads .env.live), so frontend work needs no
// Python, Neo4j or API keys.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.API_PROXY_TARGET || 'http://localhost:8000';
  const siteUrl = toSiteUrl(env.SITE_URL);
  return {
    plugins: [react(), siteEssentials(siteUrl)],
    server: {
      port: 3000,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/health': { target, changeOrigin: true },
        '/ws': { target: target.replace(/^http/, 'ws'), ws: true, changeOrigin: true },
      },
    },
  };
});
