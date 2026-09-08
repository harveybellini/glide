export type TravelMode = "driving";

export interface SessionHandle {
  session_id: string;
}

export interface PlaceRef {
  id: string;
  provider_id?: string | null;
  label: string;
  longitude?: number | null;
  latitude?: number | null;
  provenance: string;
  confirmed: boolean;
  storage_policy_status: string;
}

export interface UserSettings {
  user_id: string;
  time_zone: string;
  source_calendar_id: string;
  glide_calendar_id: string;
  start_place?: PlaceRef | null;
  earliest_departure?: string | null;
  mode: TravelMode;
  padding_minutes: number;
  enabled: boolean;
  revision: number;
}

export interface CalendarEvent {
  provider_event_id: string;
  occurrence_id: string;
  calendar_id: string;
  etag: string;
  start: string;
  end: string;
  original_time_zone: string;
  title: string;
  location?: string | null;
  place_id?: string | null;
  status: string;
  transparency: string;
  attendance: string;
  kind: string;
}

export interface ManagedBlock {
  journey_key: string;
  user_id?: string;
  origin_occurrence_id?: string;
  destination_occurrence_id?: string;
  provider_event_id: string;
  start: string;
  end: string;
  last_applied_hash: string;
  etag: string;
  source_revision: string;
  policy_revision: number;
  padding_minutes?: number;
  manual_override: boolean;
  skipped: boolean;
}

export interface Decision {
  id: string;
  user_id: string;
  occurrence_id: string;
  journey_key: string;
  source_revision: string;
  reason: string;
  calculated_facts: Record<string, number | string | boolean>;
  allowed_actions: string[];
  status: string;
  version: number;
}

export interface Run {
  id: string;
  user_id: string;
  trigger: string;
  status: string;
  lease_revision: number;
  source_fingerprint: string;
  started_at: string;
  ended_at?: string | null;
  counts: Record<string, number>;
  safe_failure_code?: string | null;
}

export interface MutationReceipt {
  id: string;
  run_id: string;
  journey_key: string;
  operation: string;
  provider_event_id?: string | null;
  before_hash?: string | null;
  after_hash?: string | null;
  outcome: string;
  timestamp: string;
}

export interface DemoSessionResponse {
  session: SessionHandle;
  settings: UserSettings;
  source_events: CalendarEvent[];
  sample_date: string;
  label: string;
}

export interface DayResponse {
  date: string;
  source_events: CalendarEvent[];
  travel_blocks: ManagedBlock[];
  decisions: Decision[];
  last_run?: Run | null;
  label: string;
}

export interface RunQueuedResponse {
  run_id: string;
  status: string;
}

export interface RunResultResponse {
  run: Run;
  plans: Record<string, unknown>[];
  decisions: Decision[];
  travel_blocks: ManagedBlock[];
  receipts: MutationReceipt[];
}

export interface ActivityResponse {
  receipts: MutationReceipt[];
}

export interface ResolveDecisionResponse {
  decision: Decision;
  run_id?: string | null;
}

export interface AuthStatus {
  connected: boolean;
  email?: string | null;
  provider_available: boolean;
}
