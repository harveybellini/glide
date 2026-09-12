import type {
  ActivityResponse,
  AuthStatus,
  CalendarEvent,
  DayResponse,
  DemoSessionResponse,
  PlaceRef,
  DisconnectResponse,
  ResolveDecisionResponse,
  RunQueuedResponse,
  RunResultResponse,
  UserSettings,
} from "./types";
import { readStored, removeStored, writeStored } from "./storage";

const SESSION_KEY = "glide-sample-session";
let liveMode = false;

// Only these verbs may be retried: repeating a POST would create a second run.
const RETRYABLE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function setLiveMode(enabled: boolean): void {
  liveMode = enabled;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const attempt = async (): Promise<Response> =>
    fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
  let response: Response;
  try {
    response = await attempt();
  } catch (first) {
    // One retry absorbs a transient proxy/keep-alive failure for read-only
    // requests; a failed POST is surfaced instead of duplicating server work.
    if (!RETRYABLE_METHODS.has((init.method ?? "GET").toUpperCase())) {
      throw first;
    }
    response = await attempt().catch(() => {
      throw first;
    });
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail ?? `Request failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

function sessionHeaders(): Record<string, string> {
  if (liveMode) {
    return {};
  }
  const sessionId = readStored(SESSION_KEY);
  return sessionId ? { "X-Glide-Session": sessionId } : {};
}

export function storedSessionId(): string | null {
  return readStored(SESSION_KEY);
}

export async function createSampleSession(): Promise<DemoSessionResponse> {
  const session = await request<DemoSessionResponse>("/api/demo/session", {
    method: "POST",
    body: JSON.stringify({}),
  });
  writeStored(SESSION_KEY, session.session.session_id);
  return session;
}

export async function fetchDay(): Promise<DayResponse> {
  return request<DayResponse>("/api/day", { headers: sessionHeaders() });
}

export async function fetchSettings(): Promise<UserSettings> {
  return request<UserSettings>("/api/me", { headers: sessionHeaders() });
}

export async function patchSettings(updates: {
  padding_minutes?: number;
  earliest_departure?: string | null;
  start_place?: PlaceRef | null;
  time_zone?: string;
  notification_email?: string;
  notify_on_decisions?: boolean;
}): Promise<UserSettings> {
  return request<UserSettings>("/api/settings", {
    method: "PATCH",
    body: JSON.stringify(updates),
    headers: sessionHeaders(),
  });
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return request<AuthStatus>("/api/auth/status");
}

export async function signOut(): Promise<DisconnectResponse> {
  return request<DisconnectResponse>("/api/auth/logout", {
    method: "POST",
  });
}

export async function runCheck(): Promise<RunQueuedResponse> {
  return request<RunQueuedResponse>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ trigger: liveMode ? "live" : "sample" }),
    headers: sessionHeaders(),
  });
}

export async function fetchRun(runId: string): Promise<RunResultResponse> {
  return request<RunResultResponse>(`/api/runs/${runId}`, {
    headers: sessionHeaders(),
  });
}

const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "needs_input",
  "failed",
  "superseded",
  "paused",
]);

export async function waitForRun(
  runId: string,
  timeoutMs = 120000,
): Promise<RunResultResponse> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const result = await fetchRun(runId);
    if (TERMINAL_RUN_STATUSES.has(result.run.status)) {
      return result;
    }
    if (Date.now() >= deadline) {
      throw new Error("Check timed out. Refresh to see the latest state.");
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

export async function moveEvent(
  occurrenceId: string,
  start: string,
  end: string,
  location?: string | null,
): Promise<CalendarEvent> {
  return request<CalendarEvent>(`/api/demo/events/${occurrenceId}`, {
    method: "PATCH",
    body: JSON.stringify({ start, end, location: location ?? null }),
    headers: sessionHeaders(),
  });
}

export async function fetchActivity(): Promise<ActivityResponse> {
  return request<ActivityResponse>("/api/activity", { headers: sessionHeaders() });
}

export async function resolveDecision(
  decisionId: string,
  action = "skip_journey",
  place?: PlaceRef,
): Promise<ResolveDecisionResponse> {
  return request<ResolveDecisionResponse>(`/api/decisions/${decisionId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action, place }),
    headers: sessionHeaders(),
  });
}

export async function pauseAutomation(): Promise<UserSettings> {
  return request<UserSettings>("/api/pause", {
    method: "POST",
    headers: sessionHeaders(),
  });
}

export async function resumeAutomation(): Promise<UserSettings> {
  return request<UserSettings>("/api/resume", {
    method: "POST",
    headers: sessionHeaders(),
  });
}

export async function resetSample(): Promise<DemoSessionResponse> {
  const session = await request<DemoSessionResponse>("/api/demo/reset", {
    method: "POST",
    headers: sessionHeaders(),
  });
  writeStored(SESSION_KEY, session.session.session_id);
  return session;
}

export function clearSession(): void {
  removeStored(SESSION_KEY);
}

export async function searchPlaces(query: string): Promise<PlaceRef[]> {
  return request<PlaceRef[]>(
    `/api/places/search?query=${encodeURIComponent(query)}`,
    { headers: sessionHeaders() },
  );
}
