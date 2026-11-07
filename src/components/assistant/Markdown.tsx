import ReactMarkdown, { type Components } from 'react-markdown';
import { Link } from 'react-router-dom';
import { cn } from '../../utils/cn';

// Assistant answers are LLM markdown. Each element is mapped to token-styled markup so the
// output matches the dark design system without the typography plugin.
const COMPONENTS: Components = {
  p: ({ node: _n, className, ...rest }) => <p className={cn('my-2 first:mt-0 last:mb-0', className)} {...rest} />,
  h1: ({ node: _n, className, ...rest }) => (
    <h3 className={cn('mb-2 mt-4 font-display text-h3 font-bold text-fg first:mt-0', className)} {...rest} />
  ),
  h2: ({ node: _n, className, ...rest }) => (
    <h3 className={cn('mb-2 mt-4 font-display text-h3 font-bold text-fg first:mt-0', className)} {...rest} />
  ),
  h3: ({ node: _n, className, ...rest }) => (
    <h4 className={cn('mb-1.5 mt-3 font-display text-body font-bold text-fg first:mt-0', className)} {...rest} />
  ),
  h4: ({ node: _n, className, ...rest }) => (
    <h5 className={cn('mb-1 mt-3 text-body font-semibold text-fg first:mt-0', className)} {...rest} />
  ),
  ul: ({ node: _n, className, ...rest }) => (
    <ul className={cn('my-2 list-disc space-y-1 pl-5 marker:text-accent', className)} {...rest} />
  ),
  ol: ({ node: _n, className, ...rest }) => (
    <ol className={cn('my-2 list-decimal space-y-1 pl-5 marker:font-semibold marker:text-fg-subtle', className)} {...rest} />
  ),
  li: ({ node: _n, className, ...rest }) => <li className={cn('pl-1', className)} {...rest} />,
  strong: ({ node: _n, className, ...rest }) => <strong className={cn('font-semibold text-fg', className)} {...rest} />,
  a: ({ node: _n, className, href, ...rest }) => {
    const external = !!href && /^https?:\/\//i.test(href);
    // Site pages (e.g. the My team hint) stay in the app instead of reloading it
    if (href && /^\/(?!\/)/.test(href)) {
      return (
        <Link
          to={href}
          className={cn('font-medium text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent', className)}
        >
          {rest.children}
        </Link>
      );
    }
    return (
      <a
        href={href}
        className={cn('font-medium text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent', className)}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        {...rest}
      />
    );
  },
  blockquote: ({ node: _n, className, ...rest }) => (
    <blockquote className={cn('my-2 border-l-2 border-accent-2 pl-3 text-fg-muted', className)} {...rest} />
  ),
  hr: ({ node: _n, className, ...rest }) => <hr className={cn('my-3 border-line', className)} {...rest} />,
  code: ({ node: _n, className, ...rest }) => (
    <code className={cn('rounded-sm bg-surface-2 px-1 py-0.5 font-mono text-[0.85em] text-accent-2', className)} {...rest} />
  ),
  pre: ({ node: _n, className, ...rest }) => (
    <pre
      className={cn(
        'my-2 overflow-x-auto rounded-md border border-line bg-bg p-3 text-data leading-relaxed text-fg-muted',
        '[&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit',
        className,
      )}
      {...rest}
    />
  ),
  table: ({ node: _n, className, ...rest }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-line">
      <table className={cn('w-full border-collapse text-data', className)} {...rest} />
    </div>
  ),
  thead: ({ node: _n, className, ...rest }) => <thead className={cn('bg-surface-2', className)} {...rest} />,
  th: ({ node: _n, className, ...rest }) => (
    <th
      scope="col"
      className={cn('whitespace-nowrap px-3 py-2 text-left text-micro font-semibold uppercase text-fg-muted', className)}
      {...rest}
    />
  ),
  td: ({ node: _n, className, ...rest }) => (
    <td className={cn('border-t border-line px-3 py-2 align-top', className)} {...rest} />
  ),
};

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn('min-w-0 break-words text-body text-fg', className)}>
      {/* skipHtml: raw HTML in an answer is dropped, never rendered (react-markdown escapes it
          without rehype-raw anyway; this keeps it that way if plugins are added later) */}
      <ReactMarkdown components={COMPONENTS} skipHtml>
        {children}
      </ReactMarkdown>
    </div>
  );
}
