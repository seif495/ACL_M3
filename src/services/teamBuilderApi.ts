import axios from 'axios';
import { createApiClient } from './http';
import type { TeamOfWeekPlayer, TeamOfWeekResponse } from './api';

const http = createApiClient();

/** The formations a request can fix (DEF-MID-FWD); null leaves the choice to the optimiser. */
export const BUILDER_FORMATIONS = ['3-4-3', '3-5-2', '4-4-2', '4-3-3', '4-5-1', '5-3-2', '5-4-1'] as const;

export const BUILDER_LIMITS = {
  minBudget: 80,
  maxBudget: 110,
  defaultBudget: 100,
  maxLocked: 15,
  maxExcluded: 200,
  maxPerClub: 3,
} as const;

/** Body of POST /api/team-of-the-week/build. Budget in millions. */
export interface BuildRequest {
  budget: number;
  locked: string[];
  excluded: string[];
  formation: string | null;
  max_per_club: number;
}

/** A player of the built team: the predicted team's fields plus FPL's ownership when known. */
export interface BuilderPlayer extends TeamOfWeekPlayer {
  /** Percentage of FPL managers who own him; null/absent when the status file has no figure. */
  ownership?: number | null;
}

export interface BuilderTemplatePlayer {
  name: string;
  team: string;
  position: string;
  points: number;
  cost: number | null;
  ownership: number | null;
  starts: boolean;
}

/** The most-owned squad (most-owned players per position, 3 a club) arranged by the same optimiser. */
export interface BuilderTemplate {
  total_points: number;
  total_cost: number | null;
  formation: string;
  players: BuilderTemplatePlayer[];
}

export interface BuildResponse extends Omit<TeamOfWeekResponse, 'starters' | 'bench'> {
  starters: BuilderPlayer[];
  bench: BuilderPlayer[];
  /** Squad cost in millions (same as total_cost). */
  budget_used: number | null;
  /** False when the solver ran out of time: the best squad found, not proven best. */
  optimal: boolean;
  max_per_club: number;
  /** The locked and excluded names as the server resolved them. */
  locked: string[];
  excluded: string[];
  template: BuilderTemplate | null;
}

/** POST /api/team-of-the-week/build: the predicted squad under the visitor's constraints. */
export async function buildTeam(request: BuildRequest, signal?: AbortSignal): Promise<BuildResponse> {
  const { data } = await http.post<BuildResponse>('/api/team-of-the-week/build', request, { signal });
  return data;
}

/** A short message for a failed build: the server's reason for a 422, plain words for the rest. */
export function builderErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return error instanceof Error ? error.message : 'Something went wrong.';
  const status = error.response?.status;
  const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;
  if (status === 429) return 'You are rebuilding too quickly. Wait a minute and try again.';
  if (status === 503) return typeof detail === 'string' ? detail : 'The builder is busy. Try again in a moment.';
  if (status === 404) return 'There are no predictions to build from yet.';
  if (status === 422) return typeof detail === 'string' ? detail : 'Those limits can’t be combined. Check the values and try again.';
  if (!error.response) return 'Couldn’t reach the server. Check your connection and try again.';
  return typeof detail === 'string' ? detail : 'Something went wrong. Try again.';
}
