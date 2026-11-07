import { StreamUnavailableError, type AnswerCard, type QueryRequestBody, type QueryResponse } from './api';
import type { Message } from '../store/appStore';

// The assistant's own client: the same endpoints as apiService.queryFPL/streamQuery, plus the
// visitor's FPL team ID in the body, the sources line in the answers, and the rate-limit headers
// (questions left, and the wait after a 429), which axios' and streamQuery's callers can't see.

const API_BASE_URL = import.meta.env.MODE === 'production' ? '' : (import.meta.env.VITE_API_URL || '');

export interface AssistantRequestBody extends QueryRequestBody {
  /** The team ID saved by the My team page; "my team" questions answer for that squad. */
  fpl_team_id?: number;
}

/** A QueryResponse from a backend that also says what the answer is based on. */
export interface AssistantResponse extends QueryResponse {
  sources?: string[];
}

/** A chat message that may carry its "Based on: ..." line. */
export type AssistantMessage = Message & { sources?: string[] };

export type AssistantStreamEvent =
  | { event: 'cards'; data: { cards: AnswerCard[]; follow_ups: string[]; sources?: string[] } }
  | { event: 'delta'; data: { text: string } }
  | { event: 'answer'; data: { text: string } }
  | { event: 'done'; data: AssistantResponse }
  | { event: 'error'; data: { detail: string } };

/** Questions left in the tightest rate-limit window (X-RateLimit-Remaining / -Window). */
export interface RateInfo {
  remaining: number;
  windowSeconds: number;
}

/** A 429: the question wasn't sent; `retryAfter` is the server's wait in seconds. */
export class RateLimitedError extends Error {
  constructor(public retryAfter: number | null) {
    super(retryAfter ? `You've asked a lot of questions. Try again in ${formatWait(retryAfter)}.` : "You've asked a lot of questions. Try again shortly.");
  }
}

export function formatWait(seconds: number): string {
  if (seconds < 60) {
    const n = Math.max(1, Math.round(seconds));
    return `${n} second${n === 1 ? '' : 's'}`;
  }
  const minutes = Math.ceil(seconds / 60);
  return minutes === 1 ? 'a minute' : `${minutes} minutes`;
}

export function rateInfoFrom(headers: Headers): RateInfo | null {
  const remaining = Number(headers.get('X-RateLimit-Remaining'));
  const windowSeconds = Number(headers.get('X-RateLimit-Window'));
  if (headers.get('X-RateLimit-Remaining') == null || !Number.isFinite(remaining)) return null;
  return { remaining, windowSeconds: Number.isFinite(windowSeconds) && windowSeconds > 0 ? windowSeconds : 3600 };
}

async function failure(response: Response): Promise<Error> {
  if (response.status === 429) {
    const wait = Number(response.headers.get('Retry-After'));
    return new RateLimitedError(Number.isFinite(wait) && wait > 0 ? wait : null);
  }
  let detail = `Request failed with status ${response.status}`;
  try {
    const json = await response.json();
    if (typeof json?.detail === 'string') detail = json.detail;
  } catch {
    // not JSON: keep the status message
  }
  return new Error(detail);
}

function post(path: string, body: AssistantRequestBody, accept: string, signal?: AbortSignal) {
  return fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: accept },
    body: JSON.stringify(body),
    signal,
  });
}

/** POST /api/query (the fallback when the stream endpoint is missing). */
export async function queryAssistant(body: AssistantRequestBody, signal?: AbortSignal): Promise<{ response: AssistantResponse; rate: RateInfo | null }> {
  const response = await post('/api/query', body, 'application/json', signal);
  if (!response.ok) throw await failure(response);
  return { response: (await response.json()) as AssistantResponse, rate: rateInfoFrom(response.headers) };
}

/**
 * Streams an answer from POST /api/query/stream, calling onEvent for each server-sent event.
 * Returns the rate-limit headers. Throws StreamUnavailableError when the endpoint is missing,
 * RateLimitedError on a 429, and an Error with the server's detail for other failures.
 */
export async function streamAssistant(
  body: AssistantRequestBody,
  onEvent: (event: AssistantStreamEvent) => void,
  signal?: AbortSignal,
): Promise<RateInfo | null> {
  const response = await post('/api/query/stream', body, 'text/event-stream', signal);
  if (response.status === 404 || response.status === 405) throw new StreamUnavailableError();
  if (!response.ok) throw await failure(response);
  if (!response.body) throw new StreamUnavailableError();
  const rate = rateInfoFrom(response.headers);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent({ event, data: JSON.parse(data.join('\n')) } as AssistantStreamEvent);
    }
  }
  return rate;
}
