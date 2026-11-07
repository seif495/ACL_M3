import { useEffect, useState } from 'react';
import { WifiOff } from 'lucide-react';

/** A slim strip under the header while the browser is offline (the service worker still serves
    the pages already opened, but their data needs a connection). */
export function OfflineNotice() {
  const [offline, setOffline] = useState(() => typeof navigator !== 'undefined' && navigator.onLine === false);

  useEffect(() => {
    const update = () => setOffline(navigator.onLine === false);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => {
      window.removeEventListener('online', update);
      window.removeEventListener('offline', update);
    };
  }, []);

  if (!offline) return null;
  return (
    <div role="status" className="border-b border-line bg-surface">
      <p className="mx-auto flex w-full max-w-page items-center gap-2 px-4 py-2 text-data text-fg-muted sm:px-6">
        <WifiOff className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
        You're offline. Pages you've opened still load, but new data needs a connection.
      </p>
    </div>
  );
}
