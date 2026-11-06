import { useEffect } from 'react';

const SITE_NAME = 'Pitch Me';

interface PageMeta {
  /** Page name, e.g. "Fixtures". Omit on the home page to use the site name alone. */
  title?: string;
  /** One or two sentences, ~150 characters: shown in search results and link previews. */
  description: string;
  /** Keep search engines away (private pages such as /stats). */
  noindex?: boolean;
}

function setMeta(selector: string, attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(selector);
  if (!el) {
    el = document.createElement('meta');
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute('content', content);
}

/**
 * Per-page <title>, meta description, Open Graph title/description/url and canonical link.
 *
 * Search engines render the SPA and read these. Social apps' preview crawlers don't run JS, so
 * Caddy sends them to the backend, which serves the same title and description per page
 * (services/seo.py's PAGES; tests/test_seo.py fails if the two drift apart).
 */
export function usePageMeta({ title, description, noindex = false }: PageMeta) {
  useEffect(() => {
    const fullTitle = title ? `${title} · ${SITE_NAME}` : `${SITE_NAME} · FPL stats, predictions & AI assistant`;
    const url = `${window.location.origin}${window.location.pathname}`;
    document.title = fullTitle;
    setMeta('meta[name="description"]', 'name', 'description', description);
    setMeta('meta[property="og:title"]', 'property', 'og:title', fullTitle);
    setMeta('meta[property="og:description"]', 'property', 'og:description', description);
    setMeta('meta[property="og:url"]', 'property', 'og:url', url);
    setMeta('meta[name="twitter:title"]', 'name', 'twitter:title', fullTitle);
    setMeta('meta[name="twitter:description"]', 'name', 'twitter:description', description);

    let canonical = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    if (!canonical) {
      canonical = document.createElement('link');
      canonical.rel = 'canonical';
      document.head.appendChild(canonical);
    }
    canonical.href = url;

    const robots = document.head.querySelector<HTMLMetaElement>('meta[name="robots"]');
    if (noindex) setMeta('meta[name="robots"]', 'name', 'robots', 'noindex, nofollow');
    else robots?.remove();
  }, [title, description, noindex]);
}
