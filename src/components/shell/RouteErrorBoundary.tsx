import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ErrorState } from '../ui';

interface State {
  error: Error | null;
}

const isChunkError = (error: Error) =>
  /dynamically imported module|Importing a module script failed|Loading chunk|error loading dynamically/i.test(error.message);

/**
 * Keeps the header and footer on screen when a page fails to render: most often its code chunk
 * couldn't be fetched (offline, or a deploy replaced the build the tab started with). App.tsx
 * keys it by path, so moving to another page starts fresh.
 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page failed to render', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const chunk = isChunkError(error);
    return (
      <div className="mx-auto w-full max-w-page px-4 py-10 sm:px-6">
        <ErrorState
          title={chunk ? "This page couldn't load" : 'Something went wrong on this page'}
          description={
            chunk
              ? navigator.onLine === false
                ? "You're offline and this page hasn't been opened on this device yet. Try again when you're connected."
                : 'The site may have just been updated. Reloading fetches the latest version.'
              : 'Reloading usually fixes it. If it keeps happening, let us know on the Contact page.'
          }
          onRetry={() => window.location.reload()}
        />
      </div>
    );
  }
}
