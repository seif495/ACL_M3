import { useExport } from './exportContext';

/** Other hosts' photos via our image proxy, so the exported canvas isn't tainted by them. */
export function proxyPhoto(url: string | null | undefined): string | null | undefined {
  if (!url || url.startsWith('/') || url.startsWith('data:')) return url;
  try {
    if (new URL(url).origin === window.location.origin) return url;
  } catch {
    return url;
  }
  return `/api/images/proxy?url=${encodeURIComponent(url)}`;
}

/** For content that is drawn both on the page and in the image: photos go through the proxy in the image. */
export function useExportPhoto(): (url: string | null | undefined) => string | null | undefined {
  const { exporting } = useExport();
  return (url) => (exporting ? proxyPhoto(url) : url);
}
