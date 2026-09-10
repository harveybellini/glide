import { useCallback, useEffect, useState } from "react";
import {
  clearSession,
  createSampleSession,
  fetchActivity,
  fetchAuthStatus,
  fetchDay,
  fetchSettings,
  pauseAutomation,
  resolveDecision,
  resetSample,
  resumeAutomation,
  runCheck,
  searchPlaces,
  setLiveMode,
  storedSessionId,
  waitForRun,
} from "./api";
import ConnectionStatus from "./components/ConnectionStatus";
import EventEditor from "./components/EventEditor";
import SettingsPanel from "./components/SettingsPanel";
import type {
  ActivityResponse,
  AuthStatus,
  CalendarEvent,
  DayResponse,
  ManagedBlock,
  PlaceRef,
  UserSettings,
} from "./types";
import { formatDate, formatTime } from "./time";

type Item =
  | { kind: "event"; data: CalendarEvent }
  | { kind: "travel"; data: ManagedBlock };

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(storedSessionId());
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [day, setDay] = useState<DayResponse | null>(null);
  const [activity, setActivity] = useState<ActivityResponse | null>(null);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [editingOccurrenceId, setEditingOccurrenceId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);

  const loadDay = useCallback(async () => {
    const [next, nextActivity, nextSettings] = await Promise.all([
      fetchDay(),
      fetchActivity(),
      fetchSettings(),
    ]);
    setDay(next);
    setActivity(nextActivity);
    setSettings(nextSettings);
  }, []);

  useEffect(() => {
    fetchAuthStatus()
      .then((status) => {
        setAuthStatus(status);
        if (status.connected) {
          setLiveMode(true);
          return loadDay();
        }
        setLiveMode(false);
        if (storedSessionId()) {
          return loadDay().catch(() => {
            clearSession();
            setSessionId(null);
          });
        }
        return undefined;
      })
      .catch(() => {
        setLiveMode(false);
        setAuthStatus({
          connected: false,
          provider_available: false,
          requires_reconnect: false,
        });
      });
  }, [loadDay]);

  const onDisconnected = useCallback(() => {
    setAuthStatus({
      connected: false,
      provider_available: true,
      requires_reconnect: false,
    });
    setLiveMode(false);
    setDay(null);
    setSettings(null);
    setActivity(null);
    if (storedSessionId()) {
      loadDay().catch(() => {
        clearSession();
        setSessionId(null);
      });
    }
  }, [loadDay]);

  const startSample = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await createSampleSession();
      setSessionId(next.session.session_id);
      await loadDay();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the sample.");
    } finally {
      setBusy(false);
    }
  };

  const recheck = async () => {
    if (!sessionId && !authStatus?.connected) {
      return;
    }
    setBusy(true);
    setError(null);
    setStatus("Planning travel…");
    try {
      const queued = await runCheck();
      const result = await waitForRun(queued.run_id);
      if (result.run.status === "failed") {
        throw new Error("The check could not be completed. Try again.");
      }
      await loadDay();
      setStatus(
        result.run.status === "needs_input"
          ? "A decision needs your input."
          : "Travel plan updated.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Check failed.");
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!sessionId) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await resetSample();
      setSessionId(next.session.session_id);
      setEditingOccurrenceId(null);
      setShowSettings(false);
      await loadDay();
      setStatus("Sample reset to its starting state.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reset failed.");
    } finally {
      setBusy(false);
    }
  };

  const resolveJourney = async (
    decisionId: string,
    action = "skip_journey",
    place?: PlaceRef,
  ) => {
    setBusy(true);
    setError(null);
    try {
      const resolved = await resolveDecision(decisionId, action, place);
      if (resolved.run_id) {
        const result = await waitForRun(resolved.run_id);
        if (result.run.status === "failed") {
          throw new Error("The check could not be completed. Try again.");
        }
      }
      await loadDay();
      setStatus(action === "correct_location" ? "Location corrected." : "Decision saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the decision.");
    } finally {
      setBusy(false);
    }
  };

  const toggleAutomation = async () => {
    if (!settings) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = settings.enabled ? await pauseAutomation() : await resumeAutomation();
      setSettings(updated);
      setStatus(updated.enabled ? "Automation resumed." : "Automation paused.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update automation.");
    } finally {
      setBusy(false);
    }
  };

  if ((!authStatus?.connected && !sessionId) || !day) {
    return (
      <main className="landing">
        <h1>Glide</h1>
        <p className="tagline">Your calendar, with time to get there.</p>
        <ConnectionStatus onDisconnected={onDisconnected} />
        <p>
          Glide preserves ordinary appointments and adds clearly marked travel
          events directly to your primary Google Calendar. Only Glide-owned
          travel events are reconciled or cleaned up.
        </p>
        <p>
          Try a fictional sample day with simulated routes and see how travel
          blocks are reserved between appointments.
        </p>
        <button type="button" className="primary" onClick={startSample} disabled={busy}>
          {busy ? "Creating sample…" : "Try a sample day"}
        </button>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </main>
    );
  }

  const items: Item[] = [
    ...day.source_events.map((data) => ({ kind: "event" as const, data })),
    ...day.travel_blocks.map((data) => ({ kind: "travel" as const, data })),
  ].sort((left, right) => left.data.start.localeCompare(right.data.start));

  return (
    <main className="app" aria-busy={busy}>
      <a className="skip-link" href="#timeline">
        Skip to timeline
      </a>

      <header>
        <div>
          <h1>Glide</h1>
          <p className="label">{day.label}</p>
        </div>
        <div className="header-actions">
          <button type="button" onClick={recheck} disabled={busy}>
            {busy ? "Checking…" : "Recheck now"}
          </button>
          <button
            type="button"
            onClick={() => setShowSettings((visible) => !visible)}
            disabled={busy}
            aria-expanded={showSettings}
          >
            Settings
          </button>
          {!authStatus?.connected && (
            <button type="button" onClick={reset} disabled={busy}>
              Reset sample
            </button>
          )}
          <button type="button" onClick={toggleAutomation} disabled={busy}>
            {settings?.enabled ? "Pause automation" : "Resume automation"}
          </button>
        </div>
      </header>

      <div className="account-bar">
        <ConnectionStatus compact onDisconnected={onDisconnected} />
      </div>

      {showSettings && settings && (
        <SettingsPanel
          settings={settings}
          live={Boolean(authStatus?.connected)}
          onSaved={(updated) => {
            setSettings(updated);
            setShowSettings(false);
            setStatus("Settings saved.");
          }}
          onClose={() => setShowSettings(false)}
        />
      )}

      <h2>{formatDate(day.date)}</h2>

      <section id="timeline" aria-label="Calendar timeline" className="timeline">
        {items.length === 0 && <p className="empty">No appointments on this sample day.</p>}
        {items.map((item, index) => {
          if (item.kind === "travel") {
            const origin = day.source_events.find(
              (event) => event.occurrence_id === item.data.origin_occurrence_id,
            );
            const destination = day.source_events.find(
              (event) =>
                event.occurrence_id === item.data.destination_occurrence_id,
            );
            const receipt = [...(activity?.receipts ?? [])]
              .reverse()
              .find((candidate) => candidate.journey_key === item.data.journey_key);
            return (
              <article key={`${item.data.journey_key}-${index}`} className="row travel">
                <span className="time">
                  {formatTime(item.data.start)}–{formatTime(item.data.end)}
                </span>
                <span className="content">
                  <strong>Travel · Glide</strong>
                  <span>
                    {origin?.title ?? "Start"} → {destination?.title ?? "Destination"}
                  </span>
                  <span>
                    Buffer {item.data.padding_minutes ?? "–"} min ·{" "}
                    {receipt?.outcome ?? "pending"}
                  </span>
                </span>
              </article>
            );
          }
          const event = item.data;
          return (
            <div key={`${event.occurrence_id}-${index}`}>
              <article className="row event">
                <span className="time">
                  {formatTime(event.start)}–{formatTime(event.end)}
                </span>
                <span className="content">
                  <strong>{event.title}</strong>
                  <span>{event.location || "No location"}</span>
                </span>
                {!authStatus?.connected && (
                  <button
                    type="button"
                    onClick={() =>
                      setEditingOccurrenceId((current) =>
                        current === event.occurrence_id ? null : event.occurrence_id,
                      )
                    }
                    disabled={busy}
                    aria-expanded={editingOccurrenceId === event.occurrence_id}
                  >
                    Edit
                  </button>
                )}
              </article>
              {editingOccurrenceId === event.occurrence_id && (
                <EventEditor
                  event={event}
                  dateIso={day.date}
                  onSaved={() => {
                    setEditingOccurrenceId(null);
                    setStatus("Appointment updated. Recheck to replan travel.");
                    void loadDay();
                  }}
                  onCancel={() => setEditingOccurrenceId(null)}
                />
              )}
            </div>
          );
        })}
      </section>

      {day.decisions.length > 0 && (
        <section aria-label="Needs your decision" className="decisions">
          <h2>Needs your decision</h2>
          {day.decisions.map((decision) => (
            <article key={decision.id} className="decision">
              <DecisionExplanation decision={decision} />
              <DecisionActions
                decision={decision}
                live={!sessionId}
                busy={busy}
                onResolve={resolveJourney}
              />
            </article>
          ))}
        </section>
      )}

      <section aria-label="Activity" className="activity">
        <h2>Activity</h2>
        {day.last_run && (
          <p className="last-run">
            Last check: {day.last_run.status}
            {day.last_run.ended_at ? ` at ${formatTime(day.last_run.ended_at)}` : ""}
            {day.last_run.safe_failure_code ? ` (${day.last_run.safe_failure_code})` : ""}
          </p>
        )}
        {activity && activity.receipts.length > 0 ? (
          <ul>
            {activity.receipts.map((receipt, index) => (
              <li key={`${receipt.id}-${index}`}>
                <span className="badge">{receipt.operation}</span>
                <span>{receipt.outcome}</span>
                <time>{formatTime(receipt.timestamp)}</time>
              </li>
            ))}
          </ul>
        ) : (
          <p>No updates yet. Run a check to see changes here.</p>
        )}
      </section>

      <p className="status" aria-live="polite">
        {status}
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </main>
  );
}

function DecisionActions({
  decision,
  live,
  busy,
  onResolve,
}: {
  decision: DayResponse["decisions"][number];
  live: boolean;
  busy: boolean;
  onResolve: (decisionId: string, action?: string, place?: PlaceRef) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<PlaceRef[]>([]);
  const [selected, setSelected] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const actions = new Set(decision.allowed_actions);

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setSearchError(null);
    try {
      const results = await searchPlaces(query);
      setCandidates(results);
      setSelected(results[0]?.id ?? "");
    } catch (reason) {
      setSearchError(reason instanceof Error ? reason.message : "Place search failed.");
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="decision-actions">
      {live && actions.has("correct_location") && (
        <div className="place-search">
          <label>
            Correct location
            <div className="search-row">
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search for the correct place"
              />
              <button type="button" onClick={search} disabled={busy || searching}>
                {searching ? "Searching…" : "Search"}
              </button>
            </div>
          </label>
          {candidates.length > 0 && (
            <div className="search-row">
              <select value={selected} onChange={(event) => setSelected(event.target.value)}>
                {candidates.map((place) => (
                  <option key={place.id} value={place.id}>
                    {place.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={busy || !selected}
                onClick={() => {
                  const place = candidates.find((candidate) => candidate.id === selected);
                  if (place) void onResolve(decision.id, "correct_location", place);
                }}
              >
                Use this place
              </button>
            </div>
          )}
          {searchError && <span className="error">{searchError}</span>}
        </div>
      )}
      {actions.has("edit_source_event") && (
        live ? (
          <a href="https://calendar.google.com/" target="_blank" rel="noreferrer">
            Edit in Google Calendar
          </a>
        ) : (
          <button
            type="button"
            onClick={() => document.getElementById("timeline")?.scrollIntoView()}
            disabled={busy}
          >
            Edit appointments
          </button>
        )
      )}
      {actions.has("keep_manual_edit") && (
        <button type="button" onClick={() => void onResolve(decision.id, "keep_manual_edit")} disabled={busy}>
          Keep my edit
        </button>
      )}
      {actions.has("replace_with_plan") && (
        <button type="button" onClick={() => void onResolve(decision.id, "replace_with_plan")} disabled={busy}>
          Restore Glide plan
        </button>
      )}
      {actions.has("recreate_journey") && (
        <button type="button" onClick={() => void onResolve(decision.id, "recreate_journey")} disabled={busy}>
          Recreate travel event
        </button>
      )}
      {actions.has("treat_as_virtual") && (
        <button type="button" onClick={() => void onResolve(decision.id, "treat_as_virtual")} disabled={busy}>
          No travel needed
        </button>
      )}
      {actions.has("skip_journey") && (
        <button type="button" onClick={() => void onResolve(decision.id)} disabled={busy}>
          Skip this journey
        </button>
      )}
    </div>
  );
}

function DecisionExplanation({
  decision,
}: {
  decision: DayResponse["decisions"][number];
}) {
  const available = Number(decision.calculated_facts.available_seconds ?? 0);
  const required = Number(decision.calculated_facts.required_seconds ?? 0);
  const shortfall = Number(decision.calculated_facts.shortfall_seconds ?? 0);

  if (decision.reason === "insufficient_time" && required > 0) {
    return (
      <p>
        This journey needs {Math.ceil(required / 60)} minutes, but only{" "}
        {Math.floor(available / 60)} minutes are available between the
        appointments. Shortfall: {Math.ceil(shortfall / 60)} minutes.
      </p>
    );
  }
  if (
    decision.reason === "unknown_location" ||
    decision.reason === "unknown_start"
  ) {
    return (
      <p>
        A location could not be resolved, so travel time could not be planned.
        Add or correct the location and recheck.
      </p>
    );
  }
  if (decision.reason === "hybrid_meeting") {
    return (
      <p>
        This meeting might be in person or online. Decide whether travel time
        is needed, or edit the appointment to make its mode clear.
      </p>
    );
  }
  if (decision.reason === "all_day") {
    return (
      <p>
        An all-day commitment occupies this day. Glide will not schedule
        travel around it automatically; review the day manually.
      </p>
    );
  }
  if (decision.reason === "downstream_uncertain") {
    return (
      <p>
        An earlier journey could not be resolved, so travel after this point
        is paused until that journey is corrected or skipped.
      </p>
    );
  }
  return <p>This journey needs your input before travel can be planned.</p>;
}
