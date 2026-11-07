import { useCallback } from 'react';
import { handleApiError, StreamUnavailableError } from '../services/api';
import {
  queryAssistant,
  RateLimitedError,
  streamAssistant,
  type AssistantMessage,
  type AssistantResponse,
  type RateInfo,
} from '../services/assistantApi';
import { readSavedTeamId } from '../components/myteam/format';
import { useAppStore, type Message } from '../store/appStore';

// One answer streams at a time. Kept outside React so Clear chat can stop it even after the
// assistant page was left and opened again (the answer keeps arriving into the store meanwhile).
let inflight: AbortController | null = null;
let counter = 0;

/** Stops the answer that is streaming, if any. */
export function abortAnswer() {
  inflight?.abort();
  inflight = null;
}

interface AskOptions {
  onDone?: (response: AssistantResponse) => void;
  onError?: (message: string) => void;
  /** Questions left (from the rate-limit headers), after every answer that reports it. */
  onRate?: (rate: RateInfo) => void;
  /** A 429: how long to wait, in seconds (null when the server didn't say). */
  onRateLimited?: (retryAfter: number | null) => void;
}

/**
 * Sends a question and streams the answer into the chat: the cards first (as soon as the
 * backend has them), then the text piece by piece (one store update per animation frame), then
 * the final response. Falls back to POST /api/query when the backend has no stream endpoint.
 */
export function useAssistantAsk({ onDone, onError, onRate, onRateLimited }: AskOptions = {}) {
  return useCallback(
    async (question: string) => {
      const store = useAppStore.getState();
      if (store.chatHistory.some((m) => m.streaming)) return;
      const history = store.chatHistory.map((m) => ({ role: m.role, content: m.content }));
      const id = `answer-${Date.now().toString(36)}-${(counter++).toString(36)}`;
      store.addMessage({ role: 'user', content: question, timestamp: Date.now() });
      store.addMessage({ id, role: 'assistant', content: '', timestamp: Date.now(), streaming: true, cards: [], followUps: [] });

      // `sources` isn't in the store's Message type; the store merges the patch as given
      const update = (patch: Partial<AssistantMessage>) => useAppStore.getState().updateMessage(id, patch as Partial<Message>);
      const controller = new AbortController();
      inflight = controller;
      let text = '';
      let frame = 0;
      let finished = false;
      const finish = (response: AssistantResponse) => {
        finished = true;
        cancelAnimationFrame(frame);
        update({
          content: response.answer,
          cards: response.cards ?? [],
          followUps: response.follow_ups ?? [],
          sources: response.sources ?? [],
          streaming: false,
        });
        onDone?.(response);
      };

      try {
        const teamId = readSavedTeamId();
        const body = {
          question,
          model: store.selectedModel,
          retrieval_method: store.retrievalMethod,
          embedding_model: store.embeddingModel,
          is_first_message: history.length === 0,
          chat_history: history,
          ...(teamId ? { fpl_team_id: teamId } : {}),
        };
        let rate: RateInfo | null = null;
        try {
          rate = await streamAssistant(
            body,
            (event) => {
              switch (event.event) {
                case 'cards':
                  update({ cards: event.data.cards, followUps: event.data.follow_ups, sources: event.data.sources ?? [] });
                  break;
                case 'delta':
                  text += event.data.text;
                  if (!frame) {
                    frame = requestAnimationFrame(() => {
                      frame = 0;
                      update({ content: text });
                    });
                  }
                  break;
                case 'answer':
                  text = event.data.text;
                  break;
                case 'done':
                  finish(event.data);
                  break;
                case 'error':
                  throw new Error(event.data.detail);
              }
            },
            controller.signal,
          );
        } catch (err) {
          if (!(err instanceof StreamUnavailableError)) throw err;
          const fallback = await queryAssistant(body, controller.signal);
          rate = fallback.rate;
          finish(fallback.response);
        }
        if (rate) onRate?.(rate);
        if (!finished) throw new Error('The answer was interrupted. Please try again.');
      } catch (err) {
        if (controller.signal.aborted) return; // Clear chat: the message is gone anyway
        cancelAnimationFrame(frame);
        if (err instanceof RateLimitedError) onRateLimited?.(err.retryAfter);
        const message = handleApiError(err);
        update({ content: `Error: ${message}`, streaming: false, cards: [], followUps: [] });
        onError?.(message);
      } finally {
        if (inflight === controller) inflight = null;
      }
    },
    [onDone, onError, onRate, onRateLimited],
  );
}
