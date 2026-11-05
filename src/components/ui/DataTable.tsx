import { useMemo, useState, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Skeleton } from './Feedback';

export type SortDirection = 'asc' | 'desc';
export interface SortState {
  key: string;
  direction: SortDirection;
}

type Breakpoint = 'sm' | 'md' | 'lg';

// Literal class names so Tailwind's scanner keeps them.
const HIDE_BELOW: Record<Breakpoint, string> = {
  sm: 'hidden sm:table-cell',
  md: 'hidden md:table-cell',
  lg: 'hidden lg:table-cell',
};

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Full name for abbreviated headers ("Pts" → "Total points"); used as title + sr text. */
  description?: string;
  align?: 'left' | 'right' | 'center';
  /** Cell content; defaults to row[key]. */
  render?: (row: T, index: number) => ReactNode;
  /** Enables sorting on this column. */
  sortValue?: (row: T) => number | string | null | undefined;
  /** Pin to the left edge while scrolling horizontally (use for the first column). */
  sticky?: boolean;
  /** Hide on narrow screens. */
  hideBelow?: Breakpoint;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  /** Accessible table caption (can be visually hidden). */
  caption: string;
  showCaption?: boolean;
  initialSort?: SortState;
  /** Controlled sorting; when set, the table does not sort rows itself. */
  sort?: SortState;
  onSortChange?: (sort: SortState) => void;
  loading?: boolean;
  skeletonRows?: number;
  empty?: ReactNode;
  /** Mouse convenience only: also render a real link/button in a cell for keyboard users. */
  onRowClick?: (row: T) => void;
  /** Extra classes per row, e.g. to highlight a selected player. */
  rowClassName?: (row: T, index: number) => string | undefined;
  dense?: boolean;
  className?: string;
}

const ALIGN = { left: 'text-left', right: 'text-right', center: 'text-center' } as const;

function compare(a: number | string | null | undefined, b: number | string | null | undefined): number {
  // Missing values always sort last, regardless of direction (handled by caller).
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

/**
 * Dense, sortable data table in the FPL statistics style: sticky header row,
 * optional sticky first column, horizontal scroll on small screens.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  showCaption = false,
  initialSort,
  sort: controlledSort,
  onSortChange,
  loading = false,
  skeletonRows = 8,
  empty = 'No data',
  onRowClick,
  rowClassName,
  dense = false,
  className,
}: DataTableProps<T>) {
  const [internalSort, setInternalSort] = useState<SortState | undefined>(initialSort);
  const sort = controlledSort ?? internalSort;

  const sortedRows = useMemo(() => {
    if (controlledSort || !sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const get = col.sortValue;
    const dir = sort.direction === 'asc' ? 1 : -1;
    return [...rows].sort((ra, rb) => {
      const a = get(ra);
      const b = get(rb);
      const aMissing = a == null || a === '';
      const bMissing = b == null || b === '';
      if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
      return compare(a, b) * dir;
    });
  }, [rows, columns, sort, controlledSort]);

  const toggleSort = (col: Column<T>) => {
    // Numbers are usually wanted biggest-first, names A→Z.
    const sample = rows.length ? col.sortValue?.(rows[0]) : undefined;
    const firstDir: SortDirection = typeof sample === 'string' ? 'asc' : 'desc';
    const next: SortState =
      sort?.key === col.key
        ? { key: col.key, direction: sort.direction === 'asc' ? 'desc' : 'asc' }
        : { key: col.key, direction: firstDir };
    if (!controlledSort) setInternalSort(next);
    onSortChange?.(next);
  };

  const cellPad = dense ? 'px-2 py-1.5' : 'px-3 py-2.5';
  const stickyCell = 'sticky left-0 z-[1]';

  return (
    <div className={cn('relative overflow-x-auto rounded-lg border border-line bg-surface', className)}>
      <table className="w-full border-collapse text-data">
        <caption className={cn(showCaption ? 'px-3 py-2 text-left text-data text-fg-subtle' : 'sr-only')}>{caption}</caption>
        <thead>
          <tr className="border-b border-line bg-surface-2">
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const ariaSort = active ? (sort!.direction === 'asc' ? 'ascending' : 'descending') : col.sortValue ? 'none' : undefined;
              const align = col.align ?? 'left';
              return (
                <th
                  key={col.key}
                  scope="col"
                  aria-sort={ariaSort}
                  title={col.description}
                  className={cn(
                    cellPad,
                    'whitespace-nowrap text-micro font-semibold uppercase text-fg-subtle',
                    ALIGN[align],
                    col.sticky && cn(stickyCell, 'bg-surface-2'),
                    col.hideBelow && HIDE_BELOW[col.hideBelow],
                    col.className,
                  )}
                >
                  {col.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(col)}
                      className={cn(
                        '-my-1.5 inline-flex items-center gap-1 py-1.5 uppercase hover:text-fg',
                        align === 'right' && 'flex-row-reverse',
                        active && 'text-accent',
                      )}
                    >
                      {col.header}
                      {col.description && <span className="sr-only"> ({col.description})</span>}
                      {active ? (
                        sort!.direction === 'asc' ? (
                          <ArrowUp className="h-3 w-3" aria-hidden="true" />
                        ) : (
                          <ArrowDown className="h-3 w-3" aria-hidden="true" />
                        )
                      ) : (
                        <ChevronsUpDown className="h-3 w-3 opacity-50" aria-hidden="true" />
                      )}
                    </button>
                  ) : (
                    <>
                      {col.header}
                      {col.description && <span className="sr-only"> ({col.description})</span>}
                    </>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            Array.from({ length: skeletonRows }, (_, i) => (
              <tr key={`sk-${i}`} className="border-b border-line last:border-0">
                {columns.map((col) => (
                  <td key={col.key} className={cn(cellPad, col.hideBelow && HIDE_BELOW[col.hideBelow])}>
                    <Skeleton className="h-4 w-full max-w-[8rem]" />
                  </td>
                ))}
              </tr>
            ))
          ) : sortedRows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-3 py-10 text-center text-fg-subtle">
                {empty}
              </td>
            </tr>
          ) : (
            sortedRows.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  'group border-b border-line last:border-0',
                  onRowClick && 'cursor-pointer',
                  rowClassName?.(row, i),
                )}
              >
                {columns.map((col) => {
                  const content = col.render
                    ? col.render(row, i)
                    : ((row as Record<string, unknown>)[col.key] as ReactNode);
                  return (
                    <td
                      key={col.key}
                      className={cn(
                        cellPad,
                        // Every cell lights up with its row in the same frame (no per-cell transition,
                        // so the sticky first cell doesn't change before the rest).
                        'whitespace-nowrap text-fg group-hover:bg-surface-2',
                        ALIGN[col.align ?? 'left'],
                        col.align === 'right' && 'tabular-nums',
                        col.sticky && cn(stickyCell, 'bg-surface group-hover:bg-surface-2'),
                        col.hideBelow && HIDE_BELOW[col.hideBelow],
                        col.className,
                      )}
                    >
                      {content}
                    </td>
                  );
                })}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
