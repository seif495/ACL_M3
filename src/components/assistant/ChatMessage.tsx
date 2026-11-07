import { motion, useReducedMotion } from 'framer-motion';
import { AlertTriangle, Loader2 } from 'lucide-react';
import type { Message } from '../../store/appStore';
import type { AssistantMessage } from '../../services/assistantApi';
import { Markdown } from './Markdown';
import { AnswerCards, AnswerCardSkeleton, FollowUps } from './cards/AnswerCards';
import { Logo } from '../ui';
import { cn } from '../../utils/cn';

function AssistantMark({ tone = 'accent' }: { tone?: 'accent' | 'danger' }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
        tone === 'danger' ? 'bg-danger/15 text-danger' : 'bg-brand ring-1 ring-accent/40',
      )}
    >
      {tone === 'danger' ? <AlertTriangle className="h-3.5 w-3.5" /> : <Logo iconOnly size={12} />}
    </span>
  );
}

function timeLabel(ts: number) {
  return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function Working({ label }: { label: string }) {
  return (
    <div className="inline-flex items-center gap-2 rounded-lg rounded-tl-sm border border-line bg-surface px-4 py-3 text-data text-fg-muted">
      <Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden="true" />
      {label}
    </div>
  );
}

interface ChatMessageProps {
  message: Message;
  /** Asks a question: follow-up chips and the players on the cards use it. */
  onAsk?: (question: string) => void;
  /** Disables asking (a question is in flight or the graph is offline). */
  askDisabled?: boolean;
  /** Show the follow-up chips (the latest answer only). */
  showFollowUps?: boolean;
}

export function ChatMessage({ message, onAsk, askDisabled, showFollowUps = false }: ChatMessageProps) {
  const reduceMotion = useReducedMotion();
  const isUser = message.role === 'user';
  // The page stores failed requests as assistant messages prefixed with "Error:".
  const isError = !isUser && message.content.startsWith('Error:');
  const cards = (!isError && message.cards) || [];
  const streaming = !!message.streaming;
  const sources = (!isError && !streaming && (message as AssistantMessage).sources) || [];

  return (
    <motion.li
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={cn('flex gap-3', isUser ? 'justify-end' : 'justify-start')}
    >
      {!isUser && <AssistantMark tone={isError ? 'danger' : 'accent'} />}
      <div className={cn('min-w-0', isUser ? 'max-w-[85%] sm:max-w-[75%]' : 'flex-1')}>
        <p className="sr-only">
          {isUser ? 'You' : 'Assistant'} at {timeLabel(message.timestamp)}:
        </p>
        {isUser ? (
          <div className="whitespace-pre-wrap break-words rounded-lg rounded-br-sm bg-brand-2 px-4 py-2.5 text-body text-fg">
            {message.content}
          </div>
        ) : (
          <>
            {/* Cards come first: they usually arrive before the text, which then streams in
                below them without pushing them around */}
            {streaming && !message.content && cards.length === 0 ? (
              <>
                <Working label="Searching the knowledge graph…" />
                <AnswerCardSkeleton className="mt-3 max-w-xl" />
              </>
            ) : (
              <>
                {cards.length > 0 && (
                  <AnswerCards cards={cards} onAsk={onAsk} askDisabled={askDisabled} className="mb-3 mt-0" />
                )}
                {message.content ? (
                  <div
                    className={cn(
                      'rounded-lg rounded-tl-sm border px-4 py-3',
                      isError ? 'border-danger/40 bg-danger/10' : 'border-line bg-surface',
                    )}
                    aria-busy={streaming}
                  >
                    <Markdown>{message.content}</Markdown>
                    {streaming && (
                      <span aria-hidden="true" className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-accent" />
                    )}
                  </div>
                ) : (
                  streaming && <Working label="Writing the answer…" />
                )}
                {message.content && sources.length > 0 && (
                  <p className="mt-1.5 px-1 text-micro text-fg-subtle">
                    <span className="font-semibold">Based on:</span> {sources.join(' · ')}
                  </p>
                )}
              </>
            )}
            {!isError && !streaming && showFollowUps && onAsk && message.followUps && message.followUps.length > 0 && (
              <FollowUps questions={message.followUps} onAsk={onAsk} disabled={askDisabled} />
            )}
          </>
        )}
      </div>
    </motion.li>
  );
}
