import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowDownRight, ArrowUpRight, ExternalLink, Newspaper } from 'lucide-react';
import { apiService, type FplNewsUpdate, type GuardianArticle } from '../../services/api';
import { usePlayerDialog } from '../../hooks/usePlayerDialog';
import { cn } from '../../utils/cn';
import { Card, ClubCrest, PlayerAvatar, SectionHeading, Skeleton, StatusBadge } from '../ui';

function ago(iso: string | null): string {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return '';
  const m = Math.round(ms / 60000);
  if (m < 60) return `${Math.max(1, m)}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return d < 14 ? `${d}d ago` : new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function ArticleCard({ article, lead = false }: { article: GuardianArticle; lead?: boolean }) {
  const [imgOk, setImgOk] = useState(true);
  return (
    <a
      href={article.url}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        'group flex flex-col overflow-hidden rounded-lg border border-line bg-surface transition hover:-translate-y-0.5 hover:border-line-strong hover:shadow-pop focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
        lead && 'sm:col-span-2 sm:row-span-2',
      )}
    >
      <div className={cn('relative overflow-hidden bg-surface-2', lead ? 'aspect-[16/9]' : 'aspect-[16/10]')}>
        {article.thumbnail && imgOk ? (
          <img
            src={article.thumbnail}
            alt=""
            loading="lazy"
            onError={() => setImgOk(false)}
            className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.04]"
          />
        ) : (
          <span className="flex h-full items-center justify-center text-fg-subtle">
            <Newspaper className="h-8 w-8" aria-hidden="true" />
          </span>
        )}
        <span aria-hidden="true" className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-black/50 to-transparent" />
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3 sm:p-4">
        <h3 className={cn('font-display font-bold leading-snug text-fg group-hover:text-accent', lead ? 'text-h3 sm:text-h2' : 'text-body')}>
          {article.title}
        </h3>
        {article.summary && (
          <p className={cn('text-data text-fg-muted', lead ? 'line-clamp-3' : 'line-clamp-2')}>{article.summary}</p>
        )}
        <p className="mt-auto flex items-center gap-1.5 pt-1 text-micro text-fg-subtle">
          <span className="font-semibold text-fg-muted">The Guardian</span>
          {article.byline && <span className="truncate">· {article.byline}</span>}
          {article.published && <span className="shrink-0">· {ago(article.published)}</span>}
          <ExternalLink className="ml-auto h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="sr-only">(opens theguardian.com)</span>
        </p>
      </div>
    </a>
  );
}

const KIND_LABEL: Record<FplNewsUpdate['kind'], string> = {
  injury: 'Injury',
  doubt: 'Doubt',
  suspension: 'Suspension',
  unavailable: 'Unavailable',
  return: 'Back',
};

function UpdateRow({ u }: { u: FplNewsUpdate }) {
  const dialog = usePlayerDialog();
  const body = (
    <>
      <PlayerAvatar name={u.name} src={u.photo} size={40} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-fg">{u.web_name ?? u.name}</span>
          {u.team && <ClubCrest team={u.team} size={14} />}
          {u.kind === 'return' ? (
            <span className="rounded-sm bg-accent/15 px-1.5 py-0.5 text-micro font-bold text-accent">{KIND_LABEL.return}</span>
          ) : (
            <StatusBadge status={u.status} chance={u.chance} />
          )}
        </span>
        <span className="mt-0.5 block text-data text-fg-muted">{u.text}</span>
      </span>
      <span className="shrink-0 text-micro text-fg-subtle">{ago(u.time)}</span>
    </>
  );
  return (
    <li className="border-b border-line last:border-0">
      {dialog ? (
        <button
          type="button"
          onClick={() => dialog.openPlayer({ name: u.name, team: u.team, photo: u.photo, status: u.status, news: u.kind === 'return' ? null : u.text, chance: u.chance })}
          className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
        >
          {body}
        </button>
      ) : (
        <div className="flex items-start gap-3 px-4 py-3">{body}</div>
      )}
    </li>
  );
}

/**
 * News: Guardian headlines (when the server has a key) as photo cards, and our own FPL news
 * (availability updates and price changes) beside them. `team` narrows both to one club.
 */
export function NewsSection({
  team,
  title = 'Latest news',
  articles = 5,
  showUpdates = true,
}: {
  team?: string;
  title?: string;
  articles?: number;
  /** The FPL availability list (Home has its own team-news panel). */
  showUpdates?: boolean;
}) {
  const dialog = usePlayerDialog();
  const { data, isLoading } = useQuery({
    queryKey: ['news', team ?? 'all'],
    queryFn: () => apiService.getNews(team),
    staleTime: 5 * 60_000,
  });
  const guardian = (data?.guardian.articles ?? []).slice(0, articles);
  const updates = showUpdates ? (data?.fpl.updates ?? []).slice(0, 8) : [];
  const prices = (data?.fpl.prices ?? []).slice(0, 6);

  if (isLoading) {
    return (
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Skeleton className="h-80 rounded-lg" />
        <Skeleton className="h-80 rounded-lg" />
      </div>
    );
  }
  if (!guardian.length && !updates.length && !prices.length) return null;

  return (
    <section aria-labelledby={`news-${team ?? 'all'}`}>
      <SectionHeading id={`news-${team ?? 'all'}`} title={title} />
      <div className={cn('grid gap-4', guardian.length > 0 && 'lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]')}>
        {guardian.length > 0 && (
          <div>
            <div className="grid auto-rows-auto gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {guardian.map((a, i) => (
                <ArticleCard key={a.id} article={a} lead={i === 0} />
              ))}
            </div>
            <p className="mt-2 text-micro text-fg-subtle">Headlines from The Guardian, linking to theguardian.com.</p>
          </div>
        )}
        <div className="space-y-4">
          {updates.length > 0 && (
            <Card padded={false}>
              <h3 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">FPL team news</h3>
              <ul>
                {updates.map((u) => (
                  <UpdateRow key={`${u.name}-${u.time}`} u={u} />
                ))}
              </ul>
            </Card>
          )}
          {prices.length > 0 && (
            <Card padded={false}>
              <h3 className="border-b border-line px-4 py-3 font-display text-h3 font-bold text-fg">Price changes this gameweek</h3>
              <ul>
                {prices.map((p) => {
                  const up = p.change > 0;
                  const inner = (
                    <>
                      <PlayerAvatar name={p.name} src={p.photo} size={32} />
                      <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{p.web_name ?? p.name}</span>
                      {p.team && <ClubCrest team={p.team} size={14} />}
                      <span className="w-14 text-right text-data tabular-nums text-fg-muted">{p.price != null ? `£${p.price.toFixed(1)}m` : ''}</span>
                      <span className={cn('inline-flex w-20 items-center justify-end gap-0.5 whitespace-nowrap text-data font-bold tabular-nums', up ? 'text-accent' : 'text-danger')}>
                        {up ? <ArrowUpRight className="h-4 w-4" aria-hidden="true" /> : <ArrowDownRight className="h-4 w-4" aria-hidden="true" />}
                        {up ? '+' : '−'}£{Math.abs(p.change).toFixed(1)}m
                      </span>
                    </>
                  );
                  return (
                    <li key={p.name} className="border-b border-line last:border-0">
                      {dialog ? (
                        <button
                          type="button"
                          onClick={() => dialog.openPlayer({ name: p.name, team: p.team, photo: p.photo })}
                          className="flex w-full items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-surface-2"
                        >
                          {inner}
                        </button>
                      ) : (
                        <div className="flex items-center gap-3 px-4 py-2">{inner}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </section>
  );
}
