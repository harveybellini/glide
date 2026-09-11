import { useCallback, useEffect, useRef, useState } from "react";
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
import Brand from "./components/Brand";
import Welcome from "./components/Welcome";
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
  const settingsButton = useRef<HTMLButtonElement>(null);
  const editButton = useRef<HTMLButtonElement | null>(null);

  const closeSettings = () => {
    setShowSettings(false);
    settingsButton.current?.focus();
  };

  const closeEditor = () => {
    setEditingOccurrenceId(null);
    editButton.current?.focus();
  };

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
      setStatus(
        action === "correct_location" ? "Location corrected."
          : action === "skip_journey" ? "Journey skipped."
          : "Decision saved.",
      );
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
      <Welcome busy={busy} error={error} onStart={startSample} onDisconnected={onDisconnected} />
    );
  }

  const items: Item[] = [
    ...day.source_events.map((data) => ({ kind: "event" as const, data })),
    ...day.travel_blocks.map((data) => ({ kind: "travel" as const, data })),
  ].sort((left, right) => left.data.start.localeCompare(right.data.start));
  const checkCompleted = day.last_run?.status === "completed";

  return (
    <main className="app" aria-busy={busy}>
      <a className="skip-link" href="#timeline">
        Skip to timeline
      </a>

      <aside className="sidebar" aria-label="Day controls">
        <h1 aria-label="Glide"><Brand /></h1>
        <p className="sidebar-tagline">Room for the journey.</p>
        <p className="eyebrow nav-label">YOUR SPACE</p>
        <nav className="header-actions" aria-label="Workspace">
          <a href="#timeline" className="nav-current" aria-current="page"><span aria-hidden="true">▦</span> My day <span aria-hidden="true">↗</span></a>
          <button
            type="button"
            ref={settingsButton}
            onClick={() => setShowSettings((visible) => !visible)}
            disabled={busy}
            aria-expanded={showSettings}
            aria-controls={showSettings ? "travel-settings" : undefined}
          >
            <span aria-hidden="true">⚙</span> Settings
          </button>
          <a href="#activity"><span aria-hidden="true">◷</span> Activity</a>
        </nav>
        <div className="sidebar-bottom">
          <div className="automation-note"><span className={settings?.enabled ? "status-dot" : "status-dot paused"} /><strong>{settings?.enabled ? "Glide is on" : "Glide is paused"}</strong></div>
          <p className="small muted">{settings?.enabled ? "A little help between appointments." : "Resume when you’re ready to plan."}</p>
          <button type="button" onClick={toggleAutomation} disabled={busy}>
            {settings?.enabled ? "Pause automation" : "Resume automation"}
          </button>
          {!authStatus?.connected && (
            <button type="button" className="text-button" onClick={reset} disabled={busy}>
              Reset sample
            </button>
          )}
        </div>
      </aside>

      <div className="workspace">
      <header className="workspace-header"><span className="eyebrow">MY DAY / OVERVIEW</span><span className="mode-badge"><span className="status-dot" />{authStatus?.connected ? "Google Calendar" : "Sample workspace"}</span></header>
      <div className="day-heading"><div><p className="eyebrow">MAKE ROOM FOR WHAT MATTERS</p><h2>Your day, <em>in good time.</em></h2><p className="label">{day.label}</p></div><button type="button" className="primary" onClick={recheck} disabled={busy}>{busy ? "Checking…" : "Recheck now"}<span aria-hidden="true">↻</span></button></div>

      <div className="account-bar">
        <ConnectionStatus compact onDisconnected={onDisconnected} />
      </div>

      {showSettings && settings && (
        <SettingsPanel
          settings={settings}
          live={Boolean(authStatus?.connected)}
          onSaved={(updated) => {
            setSettings(updated);
            closeSettings();
            setStatus("Settings saved.");
          }}
          onClose={closeSettings}
        />
      )}

      <div className="day-stats" aria-label="Day summary">
        <div><span className="stat-value">{day.source_events.length.toString().padStart(2, "0")}</span><span>Appointments</span></div>
        <div><span className="stat-value">{day.travel_blocks.length.toString().padStart(2, "0")}</span><span>Travel blocks</span></div>
        <div><span className="stat-value">{settings?.padding_minutes ?? 0}<small> min</small></span><span>Arrival buffer</span></div>
        <div><span className="stat-value">{day.decisions.length.toString().padStart(2, "0")}</span><span>Decisions to make</span></div>
      </div>

      <div className="day-layout"><div className="schedule-column">
      <div className="section-heading"><div><p className="eyebrow">THE PLAN</p><h2>{formatDate(day.date)}</h2></div><span className="small muted">Times in London</span></div>

      <section id="timeline" tabIndex={-1} aria-label="Calendar timeline" className="timeline">
        {items.length === 0 && <div className="empty"><h3>A little open space.</h3><p>No appointments on this day. Your plans will appear here when they’re available.</p></div>}
        {items.map((item) => {
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
              <article key={`travel-${item.data.journey_key}`} className="row travel">
                <span className="time"><time>{formatTime(item.data.start)}</time><span>{formatTime(item.data.end)}</span></span>
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
            <div key={`event-${event.occurrence_id}`}>
              <article className="row event">
                <span className="time"><time>{formatTime(event.start)}</time><span>{formatTime(event.end)}</span></span>
                <span className="content">
                  <strong>{event.title}</strong>
                  <span>{event.location || "No location"}</span>
                </span>
                {!authStatus?.connected && (
                  <button
                    type="button"
                    onClick={(clickEvent) => {
                      editButton.current = clickEvent.currentTarget;
                      setEditingOccurrenceId((current) =>
                        current === event.occurrence_id ? null : event.occurrence_id,
                      );
                    }}
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
                    closeEditor();
                    setStatus("Appointment updated. Recheck to replan travel.");
                    void loadDay();
                  }}
                  onCancel={closeEditor}
                />
              )}
            </div>
          );
        })}
      </section>
      <p className="timeline-footnote"><span className="legend-dot" /> Appointments <span className="legend-dot green" /> Travel by Glide</p>
      </div><aside className="insights" aria-label="Travel guidance">
      <section className="journey-note"><span className="eyebrow">A LITTLE BREATHING ROOM</span><span className="note-symbol" aria-hidden="true">↗</span><h2>Enjoy the<br /> <em>in-between.</em></h2><p>Driving time, with {settings?.padding_minutes ?? 0} minutes to arrive and settle in.</p><div className="note-footer">{settings?.start_place?.label ?? "No fixed starting point"}</div></section>

      {day.decisions.length > 0 && (
        <section aria-label="Needs your decision" className="decisions">
          <h2>Needs your decision</h2>
          {day.decisions.map((decision) => (
            <article key={decision.id} className="decision">
              <DecisionExplanation decision={decision} />
              <DecisionActions
                decision={decision}
                live={Boolean(authStatus?.connected)}
                busy={busy}
                onResolve={resolveJourney}
              />
            </article>
          ))}
        </section>
      )}
      {!day.decisions.length && (
        <section className="quiet-note">
          <span className="eyebrow">{checkCompleted ? "CHECK COMPLETE" : "READY WHEN YOU ARE"}</span>
          <h3>{checkCompleted ? "No decisions waiting." : "Let’s connect the dots."}</h3>
          <p>{checkCompleted
            ? "Any timing or location decisions will appear here after a check."
            : "Choose Recheck now to find travel time and spot any tight connections."}</p>
        </section>
      )}
      </aside></div>

      <section id="activity" aria-label="Activity" className="activity">
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
      <footer className="site-footer"><span>Made for the space between.</span><span>{authStatus?.connected ? "Your appointments stay yours." : "Fictional events · Simulated routes"}</span></footer>
      </div>
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
              <select aria-label="Corrected place" value={selected} onChange={(event) => setSelected(event.target.value)}>
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
          <a
            className="button-link"
            href="https://calendar.google.com/"
            target="_blank"
            rel="noreferrer"
          >
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
