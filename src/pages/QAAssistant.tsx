import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircle, PanelRightClose, PanelRightOpen, Trash2 } from 'lucide-react';
import { useAppStore } from '../store/appStore';
import { apiService } from '../services/api';
import type { QueryResponse } from '../services/api';
import { formatWait, type RateInfo } from '../services/assistantApi';
import { readSavedTeamId } from '../components/myteam/format';
import { Button } from '../components/ui';
import { ChatMessage } from '../components/assistant/ChatMessage';
import { Composer } from '../components/assistant/Composer';
import { QueryDetails } from '../components/assistant/QueryDetails';
import { cardLabel } from '../components/assistant/cards/AnswerCards';
import { abortAnswer, useAssistantAsk } from '../hooks/useAssistantAsk';
import { cn } from '../utils/cn';
import { usePageMeta } from '../hooks/usePageMeta';

interface Example {
  /** Short text on the chip (readable on phones). */
  label: string;
  /** What is sent. */
  question: string;
}

/** Example questions for the coming gameweek; the transfer one only when a team ID is saved. */
function examplesFor(gameweek: number | null, hasTeam: boolean): Example[] {
  const gw = gameweek ? `GW${gameweek}` : 'this gameweek';
  const out: Example[] = [
    { label: `Captain for ${gw}?`, question: `Who should I captain in ${gw}?` },
    { label: 'Best defenders, next 4 GWs', question: 'Best defenders for the next 4 gameweeks' },
    { label: 'Easiest fixtures', question: 'Which teams have the easiest fixtures?' },
  ];
  if (hasTeam) out.push({ label: 'My transfers', question: 'What transfers should I make?' });
  else out.push({ label: 'Top scorers 2025-26', question: 'Who scored the most goals in 2025-26?' });
  return out;
}

/** How a rate-limit window reads: "this minute", "this hour". */
function windowLabel(seconds: number) {
  if (seconds <= 60) return 'this minute';
  if (seconds <= 3600) return 'this hour';
  return 'today';
}

const DETAILS_ID = 'assistant-query-details';
// Keep following a streaming answer only while the reader is this close to the bottom
const STICK_TO_BOTTOM_PX = 160;

/** Markdown answer as plain text for the screen-reader announcement. */
function plainText(markdown: string) {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[*_`#>|]+/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function announcementFor(response: QueryResponse) {
  const cards = response.cards ?? [];
  const summary = cards.length
    ? ` ${cards.length === 1 ? 'One card' : `${cards.length} cards`} with the answer: ${cards.map(cardLabel).join('; ')}.`
    : '';
  return `Answer: ${plainText(response.answer)}${summary}`;
}

export function QAAssistant() {
  usePageMeta({ title: "Assistant", description: "Ask questions about FPL players, teams and seasons. Answers come from a knowledge graph of every season since 2020-21." });
  const [input, setInput] = useState('');
  const [showDetails, setShowDetails] = useState(false);
  const [lastQueryInfo, setLastQueryInfo] = useState<QueryResponse | null>(null);
  // The conversation isn't a live region (cards would be read out in full); this is
  const [announcement, setAnnouncement] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const chatHistory = useAppStore((s) => s.chatHistory);
  const clearChat = useAppStore((s) => s.clearChat);
  const neo4jConnected = useAppStore((s) => s.neo4jConnected);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const retrievalMethod = useAppStore((s) => s.retrievalMethod);
  const pending = chatHistory.some((m) => m.streaming);

  // Shared health query (polled by the app shell): tells "still connecting" apart from "offline".
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: () => apiService.getHealth(),
    staleTime: 30_000,
  });

  // The coming gameweek for the example questions (the shell already fetches it)
  const { data: season } = useQuery({
    queryKey: ['season-current'],
    queryFn: () => apiService.getCurrentSeason(),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const [hasTeam] = useState(() => readSavedTeamId() != null);
  const examples = examplesFor(season?.next_gameweek ?? null, hasTeam);

  // Questions left (shown only when few), and the wait after the rate limit was hit
  const [rate, setRate] = useState<RateInfo | null>(null);
  const [limitedUntil, setLimitedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (limitedUntil == null) return;
    const timer = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= limitedUntil) setLimitedUntil(null);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [limitedUntil]);
  const waitSeconds = limitedUntil != null ? Math.max(0, Math.ceil((limitedUntil - now) / 1000)) : 0;

  const ask = useAssistantAsk({
    onDone: useCallback((response: QueryResponse) => {
      setLastQueryInfo(response);
      setAnnouncement(announcementFor(response));
    }, []),
    onError: useCallback((message: string) => setAnnouncement(`The assistant couldn't answer: ${message}`), []),
    onRate: useCallback((info: RateInfo) => {
      setRate(info);
      setLimitedUntil(null);
    }, []),
    onRateLimited: useCallback((retryAfter: number | null) => {
      const t = Date.now();
      setNow(t);
      setLimitedUntil(t + (retryAfter ?? 60) * 1000);
    }, []),
  });

  // Follow the conversation while the reader is at the bottom; scrolling up stops it
  const onScroll = () => {
    const box = scrollRef.current;
    if (box) stickRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < STICK_TO_BOTTOM_PX;
  };
  useEffect(() => {
    const box = scrollRef.current;
    if (box && stickRef.current) box.scrollTop = box.scrollHeight;
  }, [chatHistory]);

  const send = (question: string) => {
    if (!question.trim() || !neo4jConnected || pending) return;
    stickRef.current = true;
    setAnnouncement('Looking that up…');
    void ask(question.trim());
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !neo4jConnected || pending) return;
    send(input);
    setInput('');
  };

  const handleClear = () => {
    abortAnswer();
    clearChat();
    setLastQueryInfo(null);
    setAnnouncement('Conversation cleared.');
  };

  // Other pages can open the assistant with a question ready to send: navigate('/assistant', { state: { question } }).
  const location = useLocation();
  const navigate = useNavigate();
  const pendingQuestion = (location.state as { question?: string } | null)?.question;
  const sentRef = useRef<string | null>(null);
  useEffect(() => {
    if (!pendingQuestion || !neo4jConnected || sentRef.current === pendingQuestion) return;
    sentRef.current = pendingQuestion;
    send(pendingQuestion);
    navigate(location.pathname, { replace: true, state: null });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuestion, neo4jConnected]);

  const connecting = !neo4jConnected && !health;
  const isEmpty = chatHistory.length === 0;
  // Follow-up suggestions belong to the latest answer only; older ones would be stale.
  const lastIndex = chatHistory.length - 1;

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col">
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      {/* Page header */}
      <div className="shrink-0 border-b border-line bg-bg">
        <div className="mx-auto flex w-full max-w-page items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="min-w-0">
            <h1 className="font-display text-h2 font-extrabold text-fg">Assistant</h1>
            <p className="hidden truncate text-data text-fg-subtle sm:block">
              Ask about players, teams and seasons. Answers come from the FPL knowledge graph.
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowDetails((v) => !v)}
              aria-expanded={showDetails}
              aria-controls={DETAILS_ID}
              icon={
                showDetails ? (
                  <PanelRightClose className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <PanelRightOpen className="h-4 w-4" aria-hidden="true" />
                )
              }
            >
              {showDetails ? 'Hide details' : 'Show details'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClear}
              disabled={chatHistory.length === 0}
              icon={<Trash2 className="h-4 w-4" aria-hidden="true" />}
            >
              <span className="hidden sm:inline">Clear chat</span>
              <span className="sr-only sm:hidden">Clear chat</span>
            </Button>
          </div>
        </div>
      </div>

      {/* Chat (messages + composer) and the details panel. Below lg the panel sits between
          the messages and the composer; from lg it is a column on the right. */}
      <div
        className={cn(
          'grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_auto_auto]',
          showDetails && 'lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)] lg:grid-rows-[minmax(0,1fr)_auto] xl:grid-cols-[minmax(0,1fr)_minmax(0,30rem)]',
        )}
      >
        {/* Messages */}
        <div ref={scrollRef} onScroll={onScroll} className="row-start-1 min-h-0 overflow-y-auto lg:col-start-1">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-6 sm:px-6">
            {isEmpty ? (
              <div className="my-auto flex flex-col items-center py-8 text-center">
                <span className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full bg-brand text-accent ring-1 ring-brand-2">
                  <MessageCircle className="h-6 w-6" aria-hidden="true" />
                </span>
                <h2 className="font-display text-h2 font-bold text-fg">What do you want to know?</h2>
                <p className="mt-2 max-w-md text-body text-fg-muted">
                  Stats, comparisons and predictions for every Premier League season since 2020-21, plus the current one.
                </p>
                <ul className="mt-6 flex max-w-xl flex-wrap justify-center gap-2" aria-label="Example questions">
                  {examples.map((example) => (
                    <li key={example.question}>
                      <button
                        type="button"
                        onClick={() => send(example.question)}
                        title={example.question}
                        disabled={!neo4jConnected || pending}
                        className={cn(
                          'rounded-full border border-line-strong bg-surface px-3.5 py-1.5 text-data text-fg-muted transition-colors',
                          'hover:border-accent hover:text-fg disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line-strong disabled:hover:text-fg-muted',
                        )}
                      >
                        {example.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <ol className="mt-auto flex flex-col gap-5" aria-label="Conversation">
                {chatHistory.map((message, index) => (
                  <ChatMessage
                    key={message.id ?? index}
                    message={message}
                    onAsk={send}
                    askDisabled={!neo4jConnected || pending}
                    showFollowUps={index === lastIndex}
                  />
                ))}
              </ol>
            )}
          </div>
        </div>

        {/* Composer */}
        <div className="row-start-3 shrink-0 border-t border-line bg-bg lg:col-start-1 lg:row-start-2">
          <div className="mx-auto w-full max-w-3xl px-4 py-3 sm:px-6">
            {!neo4jConnected && (
              <div
                role="status"
                className={cn(
                  'mb-2 flex items-start gap-2.5 rounded-md border px-3 py-2 text-data',
                  connecting ? 'border-line bg-surface text-fg-muted' : 'border-warning/40 bg-warning/10 text-fg',
                )}
              >
                {connecting ? (
                  <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-accent-2" aria-hidden="true" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden="true" />
                )}
                <p>
                  {connecting ? (
                    'Connecting to the knowledge graph…'
                  ) : (
                    <>
                      <span className="font-semibold">Knowledge graph offline.</span> Questions are paused until the
                      database is reachable again. This page checks automatically.
                    </>
                  )}
                </p>
              </div>
            )}
            {limitedUntil != null ? (
              <p role="status" className="mb-1.5 px-1 text-micro text-warning">
                Question limit reached. You can ask again in {formatWait(waitSeconds)}.
              </p>
            ) : (
              rate &&
              rate.remaining <= 5 && (
                <p className="mb-1.5 px-1 text-micro text-fg-subtle">
                  {rate.remaining === 0
                    ? `No questions left ${windowLabel(rate.windowSeconds)}.`
                    : `${rate.remaining} ${rate.remaining === 1 ? 'question' : 'questions'} left ${windowLabel(rate.windowSeconds)}.`}
                </p>
              )
            )}
            <Composer
              value={input}
              onChange={setInput}
              onSubmit={handleSubmit}
              disabled={!neo4jConnected}
              busy={pending}
              placeholder={neo4jConnected ? 'Ask a question about FPL…' : 'Waiting for the knowledge graph…'}
              footer={
                <>
                  <span className="hidden sm:inline">Enter to send, Shift+Enter for a new line</span>
                  <span>
                    {selectedModel} · {retrievalMethod} retrieval ·{' '}
                    <Link to="/settings" className="font-semibold text-accent hover:underline">
                      Change
                    </Link>
                  </span>
                </>
              }
            />
          </div>
        </div>

        {/* Details panel */}
        {showDetails && (
          <QueryDetails
            id={DETAILS_ID}
            info={lastQueryInfo}
            onClose={() => setShowDetails(false)}
            className={cn(
              'row-start-2 max-h-[45dvh] border-t border-line',
              'lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:max-h-none lg:border-l lg:border-t-0',
            )}
          />
        )}
      </div>
    </div>
  );
}
