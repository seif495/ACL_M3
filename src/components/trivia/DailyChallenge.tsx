import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, Flame, Loader2, Play, Share2, XCircle } from 'lucide-react';
import { handleApiError } from '../../services/api';
import { checkDailyChallenge, getDailyChallenge, type DailyCheck } from '../../services/triviaDailyApi';
import { Button, Card, Chip, ErrorState, SectionHeading, Skeleton } from '../ui';
import { cn } from '../../utils/cn';
import { liveStreak, nextRecord, readDaily, saveDaily, shareText, type DailyRecord } from './dailyStorage';

const LETTERS = 'ABCDE';

function Marks({ marks }: { marks: boolean[] }) {
  return (
    <ul className="flex gap-1.5" aria-label={marks.map((m, i) => `Question ${i + 1}: ${m ? 'correct' : 'wrong'}`).join(', ')}>
      {marks.map((m, i) => (
        <li
          key={i}
          aria-hidden="true"
          className={cn('flex h-7 w-7 items-center justify-center rounded-sm', m ? 'bg-accent text-accent-on' : 'bg-danger text-accent-on')}
        >
          {m ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
        </li>
      ))}
    </ul>
  );
}

function ShareButton({ date, record }: { date: string; record: DailyRecord }) {
  const [note, setNote] = useState('');
  const share = async () => {
    const text = shareText(date, record, `${window.location.origin}/trivia`);
    try {
      if (typeof navigator.share === 'function' && window.matchMedia?.('(pointer: coarse)').matches) {
        await navigator.share({ text });
        return;
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setNote('Copied to the clipboard');
    } catch {
      setNote('Could not copy. Select and copy your score by hand.');
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="secondary" onClick={share} icon={<Share2 className="h-4 w-4" aria-hidden="true" />}>
        Share result
      </Button>
      <span role="status" className="text-data text-fg-muted">{note}</span>
    </div>
  );
}

function Summary({ date, record, streak }: { date: string; record: DailyRecord; streak: number }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-display text-h2 font-bold text-fg">
            {record.score}/{record.total} <span className="text-data font-normal text-fg-muted">today</span>
          </p>
          <p className="text-data text-fg-muted">Come back tomorrow (UTC) for a new set.</p>
        </div>
        <Marks marks={record.marks} />
      </div>
      <dl className="flex gap-6 text-data">
        <div>
          <dt className="text-fg-subtle">Streak</dt>
          <dd className="font-semibold text-fg">{streak} {streak === 1 ? 'day' : 'days'}</dd>
        </div>
        <div>
          <dt className="text-fg-subtle">Best</dt>
          <dd className="font-semibold text-fg">{record.best} {record.best === 1 ? 'day' : 'days'}</dd>
        </div>
      </dl>
      <ShareButton date={date} record={record} />
    </div>
  );
}

export function DailyChallenge() {
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [record, setRecord] = useState<DailyRecord | null>(() => readDaily());
  const playedToday = record?.last === today;
  const [playing, setPlaying] = useState(false);
  const [index, setIndex] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const [result, setResult] = useState<DailyCheck | null>(null);
  const [submitError, setSubmitError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['trivia-daily', today],
    queryFn: getDailyChallenge,
    enabled: playing && !playedToday,
    staleTime: Infinity,
    retry: 1,
  });
  const questions = data?.questions ?? [];
  const question = questions[index];

  // Once every question is answered, send the answers for checking.
  useEffect(() => {
    if (!playing || !data || picked.length < data.questions.length || result || submitting) return;
    const send = async () => {
      setSubmitting(true);
      setSubmitError('');
      try {
        const res = await checkDailyChallenge(
          data.date,
          data.questions.map((q, i) => ({ question_id: q.question_id, answer: picked[i] })),
        );
        const next = nextRecord(readDaily(), data.date, res.results.map((r) => r.correct));
        saveDaily(next);
        setRecord(next);
        setResult(res);
        setPlaying(false);
      } catch (e) {
        setSubmitError(handleApiError(e));
      } finally {
        setSubmitting(false);
      }
    };
    void send();
  }, [playing, data, picked, result, submitting]);

  const pick = (option: string) => {
    if (!question || picked.length !== index) return;
    setPicked((p) => [...p, option]);
    setIndex((i) => i + 1);
  };

  const streak = liveStreak(record, today);

  return (
    <Card tone="brand" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SectionHeading
          as="h2"
          size="md"
          title="Daily challenge"
          subtitle="The same 5 questions for everyone each UTC day. One attempt."
          className="mb-0"
        />
        {streak > 0 && (
          <Chip tone="accent">
            <Flame className="h-3.5 w-3.5" aria-hidden="true" />
            {streak} day streak
          </Chip>
        )}
      </div>

      {playedToday && record ? (
        <Summary date={record.last} record={record} streak={streak} />
      ) : !playing ? (
        <div className="space-y-3">
          {record && <p className="text-data text-fg-muted">Best streak: {record.best} {record.best === 1 ? 'day' : 'days'}.</p>}
          <Button size="lg" className="w-full sm:w-auto" onClick={() => setPlaying(true)} icon={<Play className="h-4 w-4" aria-hidden="true" />}>
            Play today&apos;s challenge
          </Button>
        </div>
      ) : isError ? (
        <ErrorState title="Today's questions did not load" description={handleApiError(error)} onRetry={() => refetch()} className="py-4" />
      ) : submitError ? (
        <ErrorState
          title="Could not check your answers"
          description={submitError}
          onRetry={() => setPicked((p) => [...p])}
          className="py-4"
        />
      ) : isLoading || !question ? (
        <div role="status" className="space-y-3">
          <span className="sr-only">{data ? 'Checking your answers' : 'Loading questions'}</span>
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
          {submitting && <Loader2 className="h-4 w-4 animate-spin text-fg-muted" aria-hidden="true" />}
        </div>
      ) : (
        <section aria-labelledby="daily-question" className="space-y-3">
          <p className="text-data text-fg-muted">
            Question {index + 1} of {questions.length}
          </p>
          <h3 id="daily-question" className="font-display text-h3 font-bold leading-snug text-fg">{question.question}</h3>
          <ul className="grid gap-2" aria-label="Answers">
            {question.options.map((option, i) => (
              <li key={`${i}-${option}`}>
                <button
                  type="button"
                  onClick={() => pick(option)}
                  className="flex min-h-[3.5rem] w-full items-center gap-3 rounded-md border-2 border-line-strong bg-surface px-3 py-2.5 text-left text-body font-medium text-fg transition-colors hover:border-accent hover:bg-surface-2 sm:px-4"
                >
                  <span aria-hidden="true" className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-micro font-bold text-fg-muted">
                    {LETTERS[i] ?? i + 1}
                  </span>
                  <span className="min-w-0 flex-1">{option}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {result && playedToday && data && (
        <div className="border-t border-line pt-4">
          <h3 className="mb-2 font-display text-h3 font-bold text-fg">Answers</h3>
          <ol className="space-y-2">
            {data.questions.map((q, i) => {
              const r = result.results[i];
              return (
                <li key={q.question_id} className="flex items-start gap-3 text-data">
                  {r.correct ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-label="Correct" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-label="Wrong" />
                  )}
                  <div className="min-w-0">
                    <p className="font-medium text-fg">{q.question}</p>
                    <p className="text-fg-muted">
                      {r.correct ? 'Correct: ' : `You said ${picked[i]}. Answer: `}
                      <span className="font-semibold text-fg">{r.correct ? picked[i] : r.correct_answer}</span>
                    </p>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </Card>
  );
}
