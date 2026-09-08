import { useCallback, useEffect, useState } from "react";
import {
  clearSession,
  createSampleSession,
  fetchActivity,
  fetchDay,
  fetchSettings,
  pauseAutomation,
  resolveDecision,
  resetSample,
  resumeAutomation,
  runCheck,
  storedSessionId,
  waitForRun,
} from "./api";
import ConnectionStatus from "./components/ConnectionStatus";
import EventEditor from "./components/EventEditor";
import SettingsPanel from "./components/SettingsPanel";
import type {
  ActivityResponse,
  CalendarEvent,
  DayResponse,
  ManagedBlock,
  UserSettings,
} from "./types";
import { formatDate, formatTime } from "./time";

type Item =
  | { kind: "event"; data: CalendarEvent }
  | { kind: "travel"; data: ManagedBlock };

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(storedSessionId());
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
    if (!storedSessionId()) {
      return;
    }
    loadDay().catch(() => {
      clearSession();
      setSessionId(null);
    });
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
    if (!sessionId) {
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

  const skipJourney = async (decisionId: string) => {
    setBusy(true);
    setError(null);
    try {
      const resolved = await resolveDecision(decisionId);
      if (resolved.run_id) {
        const result = await waitForRun(resolved.run_id);
        if (result.run.status === "failed") {
          throw new Error("The check could not be completed. Try again.");
        }
      }
      await loadDay();
      setStatus("Journey skipped.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not skip the journey.");
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

  if (!sessionId || !day) {
    return (
      <main className="landing">
        <h1>Glide</h1>
        <p className="tagline">Your calendar, with time to get there.</p>
        <ConnectionStatus />
        <p>
          Glide reads only your primary calendar and never edits your source
          appointments; travel blocks live in a separate Glide Travel calendar.
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
          <button type="button" onClick={reset} disabled={busy}>
            Reset sample
          </button>
          <button type="button" onClick={toggleAutomation} disabled={busy}>
            {settings?.enabled ? "Pause automation" : "Resume automation"}
          </button>
        </div>
      </header>

      <div className="account-bar">
        <ConnectionStatus compact />
      </div>

      {showSettings && settings && (
        <SettingsPanel
          settings={settings}
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
              <div className="decision-actions">
                <button
                  type="button"
                  onClick={() => {
                    document.getElementById("timeline")?.scrollIntoView();
                  }}
                  disabled={busy}
                >
                  Edit appointments
                </button>
                <button
                  type="button"
                  onClick={() => skipJourney(decision.id)}
                  disabled={busy}
                >
                  Skip this journey
                </button>
              </div>
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
