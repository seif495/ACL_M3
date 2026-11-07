import { createApiClient } from './http';

// Same base URL rule as services/api.ts: production uses relative URLs (the site and API share an origin).
const api = createApiClient();

export interface DailyQuestion {
  question_id: string;
  question: string;
  options: string[];
  category: string;
  difficulty: string;
}

export interface DailyChallengeData {
  date: string;
  questions: DailyQuestion[];
}

export interface DailyResult {
  question_id: string;
  correct: boolean;
  correct_answer: string;
  explanation: string;
}

export interface DailyCheck {
  date: string;
  score: number;
  total: number;
  results: DailyResult[];
}

export async function getDailyChallenge(): Promise<DailyChallengeData> {
  const { data } = await api.get<DailyChallengeData>('/api/trivia/daily');
  return data;
}

export async function checkDailyChallenge(
  date: string,
  answers: { question_id: string; answer: string }[],
): Promise<DailyCheck> {
  const { data } = await api.post<DailyCheck>('/api/trivia/daily/check', { date, answers });
  return data;
}
