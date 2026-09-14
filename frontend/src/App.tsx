import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  startWatching,
  storedSessionId,
  stopWatching,
  waitForRun,
} from "./api";
import BackgroundStatus from "./components/BackgroundStatus";
import ConnectionStatus from "./components/ConnectionStatus";
import EventEditor from "./components/EventEditor";
import SettingsPanel from "./components/SettingsPanel";
import Brand from "./components/Brand";
import BootScreen from "./components/BootScreen";
import Tour from "./components/Tour";
import VersionBadge from "./components/VersionBadge";
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
import { readStored, removeStored, writeStored } from "./storage";
import { DEFAULT_TIME_ZONE, formatDate, formatTime, timeZoneLabel } from "./time";
import {
  firstStepForStage,
  stepForStage,
  TOUR_STEPS,
  TOUR_STORAGE_KEY,
  type TourActionId,
  type TourStage,
} from "./tour";

type Item =
  | { kind: "event"; data: CalendarEvent }
  | { kind: "travel"; data: ManagedBlock };

// Set once a live Google day has loaded, so a returning visitor sees the
// branded boot screen immediately instead of the landing page. A stale hint
// only costs one loading pass before the landing page appears.
const LIVE_HINT_KEY = "glide-live-hint";
// Same trick for the anonymous sample: without it a returning visitor sees the
// marketing hero for a frame before the stored day loads.
const SAMPLE_HINT_KEY = "glide-sample-hint";

// Copy for the redirect the auth callback sends after a failed sign-in.
const AUTH_ERROR_MESSAGES: Record<string, string> = {
  access_denied: "Google sign-in was cancelled, so nothing changed.",
  missing_params: "That sign-in link was incomplete. Please start again.",
  state_expired: "That sign-in link has expired. Please start again.",
  exchange_failed: "Google sign-in could not be completed. Please try again.",
  provider_error: "Google sign-in could not be completed. Please try again.",
};

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(storedSessionId());
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authResolved, setAuthResolved] = useState(false);
  const [liveHint, setLiveHint] = useState(
    () => readStored(LIVE_HINT_KEY) === "1",
  );
  const [sampleHint, setSampleHint] = useState(
    () => readStored(SAMPLE_HINT_KEY) === "1",
  );
  const [day, setDay] = useState<DayResponse | null>(null);
  const [activity, setActivity] = useState<ActivityResponse | null>(null);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [busy, setBusy] = useState(false);
  // A fast double click reached the handler twice before React re-rendered the
  // disabled button, so two planning runs were queued for one intent. Guard the
  // in-flight check on a ref, which updates synchronously.
  const recheckInFlight = useRef(false);
  // Polling every 30 seconds must not rebuild the timeline when nothing moved,
  // or the page would re-render (and re-sort) on a timer.
  const daySnapshot = useRef("");
  const [error, setError] = useState<string | null>(() => {
    const code = new URLSearchParams(window.location.search).get("auth_error");
    if (!code) {
      return null;
    }
    return AUTH_ERROR_MESSAGES[code] ?? AUTH_ERROR_MESSAGES.provider_error;
  });
  const [status, setStatus] = useState("");
  const [editingOccurrenceId, setEditingOccurrenceId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  // The sidebar marks the section you are actually looking at. "My day" used to
  // be hardcoded as current, so the indicator never moved.
  const [activeNav, setActiveNav] = useState<"day" | "settings" | "activity">("day");
  // A decision email links back with ?decision=<id>; the card is highlighted
  // and scrolled into view once the day that contains it has loaded.
  const [focusedDecisionId, setFocusedDecisionId] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("decision"),
  );
  // The guided tour. It opens itself once for a visitor who has never seen it,
  // and ?tour=1 forces it open (that is how the browser check and the demo
  // recording walk it without waiting for a fresh browser profile).
  const [tourOpen, setTourOpen] = useState(
    () => new URLSearchParams(window.location.search).get("tour") === "1",
  );
  const [tourStepIndex, setTourStepIndex] = useState(0);
  const tourAutoStarted = useRef(false);
  const tourReturnFocus = useRef<HTMLElement | null>(null);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const editButton = useRef<HTMLButtonElement | null>(null);

  // Which screen the tour has to describe, derived before the render branches
  // below decide what to show. "loading" keeps the tour off an empty page.
  const loadingSample = sampleHint && Boolean(sessionId) && !day;
  const loadingLive =
    liveHint && !day && (authResolved ? Boolean(authStatus?.connected) : true);
  const tourStage: TourStage | "loading" = day
    ? "day"
    : loadingSample || loadingLive || !authResolved
      ? "loading"
      : "landing";

  // Start once the screen has settled. A visitor who has already finished or
  // skipped the tour keeps their workspace, and ?tour=1 overrides that.
  useEffect(() => {
    if (tourAutoStarted.current || tourStage === "loading") {
      return;
    }
    tourAutoStarted.current = true;
    if (!tourOpen && readStored(TOUR_STORAGE_KEY) === "done") {
      return;
    }
    if (!tourOpen) {
      setTourStepIndex(firstStepForStage(tourStage));
      setTourOpen(true);
    }
  }, [tourStage, tourOpen]);

  // A step belongs to one screen. When the visitor moves between them - the
  // sample day loads, or the tour is started from inside the workspace - the
  // tour follows to the first step for the screen that is actually on show.
  useEffect(() => {
    if (!tourOpen || tourStage === "loading") {
      return;
    }
    setTourStepIndex((current) => stepForStage(current, tourStage));
  }, [tourOpen, tourStage]);

  const closeSettings = () => {
    setShowSettings(false);
    settingsButton.current?.focus();
  };

  const closeEditor = () => {
    setEditingOccurrenceId(null);
    editButton.current?.focus();
  };

  const startTour = (trigger?: HTMLElement) => {
    tourReturnFocus.current = trigger ?? null;
    setTourStepIndex(firstStepForStage(tourStage === "day" ? "day" : "landing"));
    setTourOpen(true);
  };

  const endTour = () => {
    setTourOpen(false);
    writeStored(TOUR_STORAGE_KEY, "done");
    tourReturnFocus.current?.focus();
    tourReturnFocus.current = null;
  };

  const nextTourStep = () => {
    setTourStepIndex((current) => Math.min(current + 1, TOUR_STEPS.length - 1));
  };

  const backTourStep = () => {
    setTourStepIndex((current) => Math.max(current - 1, 0));
  };

  // Steps that ask the visitor to press something move on when that work has
  // actually finished, whether they used the real control or the tour's button.
  const tourNotify = (action: TourActionId) => {
    setTourStepIndex((current) =>
      TOUR_STEPS[current]?.action === action
        ? Math.min(current + 1, TOUR_STEPS.length - 1)
        : current,
    );
  };

  const tourStep = tourOpen ? TOUR_STEPS[tourStepIndex] : null;
  const tourIsLast = tourStepIndex === TOUR_STEPS.length - 1;
  // The tour's primary button does the real work rather than describing it, so
  // a visitor who would rather be shown than told still ends up in the product.
  const runTourPrimary = () => {
    if (tourStep?.action === "start-sample") {
      void startSample();
      return;
    }
    if (tourStep?.action === "run-check") {
      void recheck();
      return;
    }
    if (tourIsLast) {
      endTour();
      return;
    }
    nextTourStep();
  };
  const tourElement = tourStep ? (
    <Tour
      step={tourStep}
      stepNumber={tourStepIndex + 1}
      totalSteps={TOUR_STEPS.length}
      isFirst={tourStepIndex === 0}
      isLast={tourIsLast}
      busy={busy}
      primaryLabel={tourStep.actionLabel ?? (tourIsLast ? "Finish" : "Next")}
      onPrimary={runTourPrimary}
      onBack={backTourStep}
      onClose={endTour}
    />
  ) : null;

  useEffect(() => {
    if (showSettings) {
      setActiveNav("settings");
      return;
    }
    const updateActiveNav = () => {
      const activitySection = document.getElementById("activity");
      if (!activitySection) {
        return;
      }
      const pageHeight = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
      );
      const atBottom = window.innerHeight + window.scrollY >= pageHeight - 8;
      const activityTop = activitySection.getBoundingClientRect().top;
      const next =
        atBottom || activityTop <= window.innerHeight * 0.35 ? "activity" : "day";
      // Scroll fires dozens of times per second; only a real section change is
      // worth a re-render.
      setActiveNav((current) => (current === next ? current : next));
    };
    updateActiveNav();
    window.addEventListener("scroll", updateActiveNav, { passive: true });
    window.addEventListener("resize", updateActiveNav);
    return () => {
      window.removeEventListener("scroll", updateActiveNav);
      window.removeEventListener("resize", updateActiveNav);
    };
  }, [showSettings, day, activity]);

  const loadDay = useCallback(async () => {
    const [next, nextActivity, nextSettings] = await Promise.all([
      fetchDay(),
      fetchActivity(),
      fetchSettings(),
    ]);
    const snapshot = JSON.stringify(next);
    if (snapshot !== daySnapshot.current) {
      daySnapshot.current = snapshot;
      setDay(next);
    }
    setActivity((current) =>
      JSON.stringify(current) === JSON.stringify(nextActivity)
        ? current
        : nextActivity,
    );
    setSettings((current) =>
      JSON.stringify(current) === JSON.stringify(nextSettings)
        ? current
        : nextSettings,
    );
  }, []);

  // The agent works whether or not this tab is open, so the tab keeps looking.
  // A 30-second poll is far cheaper than the background cadence and means a
  // decision raised between visits appears without pressing anything.
  useEffect(() => {
    if (!day) {
      return;
    }
    const refresh = () => {
      if (document.visibilityState !== "visible" || busy) {
        return;
      }
      void loadDay().catch(() => {
        // A transient read failure keeps the last good day on screen; the
        // next poll retries.
      });
    };
    const timer = window.setInterval(refresh, 30000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [day, busy, loadDay]);

  useEffect(() => {
    if (!focusedDecisionId || !day) {
      return;
    }
    const matches = day.decisions.some(
      (decision) => decision.id === focusedDecisionId,
    );
    if (!matches) {
      // An expired or mistyped link should not leave a dead query parameter in
      // the address bar for the rest of the session.
      const stale = new URL(window.location.href);
      stale.searchParams.delete("decision");
      window.history.replaceState(null, "", stale.toString());
      setFocusedDecisionId(null);
      return;
    }
    document
      .getElementById(`decision-${focusedDecisionId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
    const url = new URL(window.location.href);
    url.searchParams.delete("decision");
    window.history.replaceState(null, "", url.toString());
    const timer = window.setTimeout(() => setFocusedDecisionId(null), 6000);
    return () => window.clearTimeout(timer);
  }, [day, focusedDecisionId]);

  useEffect(() => {
    fetchAuthStatus()
      .then((status) => {
        setAuthStatus(status);
        if (status.connected) {
          writeStored(LIVE_HINT_KEY, "1");
          setLiveHint(true);
          setLiveMode(true);
          return loadDay();
        }
        setLiveMode(false);
        if (storedSessionId()) {
          writeStored(SAMPLE_HINT_KEY, "1");
          setSampleHint(true);
          return loadDay().catch(() => {
            clearSession();
            removeStored(SAMPLE_HINT_KEY);
            setSampleHint(false);
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
      })
      .finally(() => setAuthResolved(true));
  }, [loadDay]);

  useEffect(() => {
    // One-shot: drop ?auth_error after it has been turned into the banner so a
    // refresh does not keep replaying the message.
    const url = new URL(window.location.href);
    if (url.searchParams.has("auth_error")) {
      url.searchParams.delete("auth_error");
      window.history.replaceState(null, "", url.toString());
    }
  }, []);

  const onDisconnected = useCallback(() => {
    removeStored(LIVE_HINT_KEY);
    setLiveHint(false);
    removeStored(SAMPLE_HINT_KEY);
    setSampleHint(false);
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
      writeStored(SAMPLE_HINT_KEY, "1");
      setSampleHint(true);
      await loadDay();
      tourNotify("start-sample");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the sample.");
    } finally {
      setBusy(false);
    }
  };

  const recheck = async () => {
    if (recheckInFlight.current) {
      return;
    }
    if (!sessionId && !authStatus?.connected) {
      return;
    }
    recheckInFlight.current = true;
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
      tourNotify("run-check");
      setStatus(
        result.run.status === "needs_input"
          ? "A decision needs your input."
          : settings && !settings.enabled
            ? "Glide is paused - resume to plan travel."
            : "Travel plan updated.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Check failed.");
    } finally {
      recheckInFlight.current = false;
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
      writeStored(SAMPLE_HINT_KEY, "1");
      setSampleHint(true);
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
    note?: string,
  ) => {
    setBusy(true);
    setError(null);
    try {
      const resolved = await resolveDecision(decisionId, action, place, note);
      if (resolved.run_id) {
        const result = await waitForRun(resolved.run_id);
        if (result.run.status === "failed") {
          throw new Error("The check could not be completed. Try again.");
        }
      }
      await loadDay();
      setStatus(
        action === "correct_location" ? "Location corrected."
          : action === "add_anyway" ? "Travel added anyway. Glide will arrive as the appointment starts."
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

  const toggleWatching = async (next: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const updated = next ? await startWatching() : await stopWatching();
      setSettings(updated);
      if (next) {
        setStatus(
          "Glide is watching in the background. It surfaces a decision only when it needs you.",
        );
        await loadDay();
      } else {
        setStatus("Background watching stopped. Planned travel is untouched.");
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not update watching.",
      );
    } finally {
      setBusy(false);
    }
  };

  // The timeline merges two lists and sorts them; that must not run on every
  // keystroke or scroll-driven render.
  const items: Item[] = useMemo(() => {
    if (!day) {
      return [];
    }
    return [
      ...day.source_events.map((data) => ({ kind: "event" as const, data })),
      ...day.travel_blocks.map((data) => ({ kind: "travel" as const, data })),
    ].sort((left, right) => left.data.start.localeCompare(right.data.start));
  }, [day]);

  if (loadingSample) {
    return <BootScreen label="Opening your day…" />;
  }
  if (!authResolved && liveHint) {
    return <BootScreen label="Checking your calendar…" />;
  }
  if (loadingLive) {
    return <BootScreen label="Opening your day…" />;
  }
  if ((!authStatus?.connected && !sessionId) || !day) {
    return (
      <>
        <Welcome
          busy={busy}
          error={error}
          onStart={startSample}
          onDisconnected={onDisconnected}
          onStartTour={startTour}
        />
        {tourElement}
      </>
    );
  }

  const checkCompleted = day.last_run?.status === "completed";
  const zone = settings?.time_zone?.trim() || DEFAULT_TIME_ZONE;

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
          <a
            href="#timeline"
            className={activeNav === "day" ? "nav-current" : undefined}
            aria-current={activeNav === "day" ? "page" : undefined}
            onClick={() => setActiveNav("day")}
          >
            <span aria-hidden="true">▦</span> My day <span aria-hidden="true">↗</span>
          </a>
          <button
            type="button"
            ref={settingsButton}
            className={activeNav === "settings" ? "nav-current" : undefined}
            onClick={() => {
              if (showSettings) {
                closeSettings();
              } else {
                setShowSettings(true);
                setActiveNav("settings");
              }
            }}
            disabled={busy}
            aria-expanded={showSettings}
            aria-controls={showSettings ? "travel-settings" : undefined}
          >
            <span aria-hidden="true">⚙</span> Settings
          </button>
          <a
            href="#activity"
            className={activeNav === "activity" ? "nav-current" : undefined}
            aria-current={activeNav === "activity" ? "page" : undefined}
            onClick={() => setActiveNav("activity")}
          >
            <span aria-hidden="true">◷</span> Activity
          </a>
        </nav>
        <div className="sidebar-bottom" data-tour="controls">
          <div className="automation-note"><span className={settings?.enabled ? "status-dot" : "status-dot paused"} /><strong>{settings?.enabled ? "Glide is on" : "Glide is paused"}</strong></div>
          <p className="small muted">{settings?.enabled ? "Watching in the background." : "Resume when you’re ready to plan."}</p>
          <button type="button" onClick={toggleAutomation} disabled={busy}>
            {settings?.enabled ? "Pause automation" : "Resume automation"}
          </button>
          {!authStatus?.connected && (
            <button type="button" className="text-button" onClick={reset} disabled={busy}>
              Reset sample
            </button>
          )}
          <button
            type="button"
            className="text-button"
            onClick={(event) => startTour(event.currentTarget)}
          >
            Show me around
          </button>
        </div>
      </aside>

      <div className="workspace">
      <header className="workspace-header"><span className="eyebrow">MY DAY / OVERVIEW</span><span className="mode-badge"><span className="status-dot" />{authStatus?.connected ? "Google Calendar" : "Sample workspace"}</span></header>
      <div className="day-heading"><div><p className="eyebrow">MAKE ROOM FOR WHAT MATTERS</p><h2>Your day, <em>in good time.</em></h2><p className="label">{day.label}</p></div><button type="button" className="primary" data-tour="recheck" onClick={recheck} disabled={busy}>{busy ? "Checking…" : "Recheck now"}<span aria-hidden="true">↻</span></button></div>

      <div className="account-bar">
        <ConnectionStatus compact onDisconnected={onDisconnected} />
      </div>

      {day.automation && (
        <BackgroundStatus
          automation={day.automation}
          live={Boolean(authStatus?.connected)}
          busy={busy}
          onToggle={(next) => void toggleWatching(next)}
        />
      )}

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
      <div className="section-heading"><div><p className="eyebrow">THE PLAN</p><h2>{formatDate(day.date, zone)}</h2></div><span className="small muted">Times in {timeZoneLabel(zone)}</span></div>

      <section id="timeline" data-tour="timeline" tabIndex={-1} aria-label="Calendar timeline" className="timeline">
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
              <article key={`travel-${item.data.journey_key}`} className="row travel" data-tour="travel-block">
                <span className="time"><time>{formatTime(item.data.start, zone)}</time><span>{formatTime(item.data.end, zone)}</span></span>
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
                <span className="time"><time>{formatTime(event.start, zone)}</time><span>{formatTime(event.end, zone)}</span></span>
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
                    aria-label={`Edit ${event.title}`}
                  >
                    Edit
                  </button>
                )}
              </article>
              {editingOccurrenceId === event.occurrence_id && (
                <EventEditor
                  event={event}
                  dateIso={day.date}
                  timeZone={zone}
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
      </div><aside className="insights" data-tour="guidance" aria-label="Travel guidance">
      <section className="journey-note"><span className="eyebrow">A LITTLE BREATHING ROOM</span><span className="note-symbol" aria-hidden="true">↗</span><h2>Enjoy the<br /> <em>in-between.</em></h2><p>Driving time, with {settings?.padding_minutes ?? 0} minutes to arrive and settle in.</p><div className="note-footer">{settings?.start_place?.label ?? "No fixed starting point"}</div></section>

      {day.automation && (
        <section className="inbox-note" aria-label="While you were away">
          <span className="eyebrow">WHILE YOU WERE AWAY</span>
          <p>
            {day.automation.checks_since_last_view > 0
              ? `Glide checked ${day.automation.checks_since_last_view} time${
                  day.automation.checks_since_last_view === 1 ? "" : "s"
                } on its own.`
              : day.automation.watching
                ? "Glide is watching this day in the background."
                : "Start watching and Glide will check without being asked."}
          </p>
          {day.decisions.length > 0 && (
            <p className="inbox-decision">
              {day.decisions.length === 1
                ? "One decision needs you."
                : `${day.decisions.length} decisions need you.`}
            </p>
          )}
        </section>
      )}

      {day.decisions.length > 0 && (
        <section aria-label="Needs your decision" className="decisions" data-tour="decision">
          <h2>Needs your decision</h2>
          {day.decisions.map((decision) => (
            <article
              key={decision.id}
              id={`decision-${decision.id}`}
              className={
                focusedDecisionId === decision.id
                  ? "decision decision-focused"
                  : "decision"
              }
            >
              <DecisionContext
                decision={decision}
                events={day.source_events}
                startLabel={settings?.start_place?.label ?? null}
                timeZone={zone}
              />
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
            : day.automation?.watching
              ? "The agent checks on its own. Recheck now is just for seeing it happen."
              : "Recheck once to see it now, or start watching and let the agent keep checking."}</p>
        </section>
      )}
      </aside></div>

      <section id="activity" data-tour="activity" aria-label="Activity" className="activity">
        <h2>Activity</h2>
        {day.last_run && (
          <p className="last-run">
            Last check: {day.last_run.status}
            {day.last_run.ended_at ? ` at ${formatTime(day.last_run.ended_at, zone)}` : ""}
            {day.last_run.safe_failure_code ? ` (${day.last_run.safe_failure_code})` : ""}
          </p>
        )}
        {activity && activity.receipts.length > 0 ? (
          <ul>
            {activity.receipts.map((receipt, index) => (
              <li key={`${receipt.id}-${index}`}>
                <span className="badge">{receipt.operation}</span>
                <span>{receipt.outcome}</span>
                <time>{formatTime(receipt.timestamp, zone)}</time>
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
      <footer className="site-footer"><span>Made for the space between.</span><VersionBadge /><span>{authStatus?.connected ? "Your appointments stay yours." : "Fictional events · Simulated routes"}</span></footer>
      </div>
      {tourElement}
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
  onResolve: (
    decisionId: string,
    action?: string,
    place?: PlaceRef,
    note?: string,
  ) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<PlaceRef[]>([]);
  const [selected, setSelected] = useState("");
  const [note, setNote] = useState("");
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
      {actions.has("add_anyway") && (
        <div className="decision-override">
          <input
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={280}
            placeholder="Why? (optional)"
            aria-label="Why you are adding this journey anyway"
            disabled={busy}
          />
          <button
            type="button"
            onClick={() => void onResolve(decision.id, "add_anyway", undefined, note)}
            disabled={busy}
          >
            Add it anyway
          </button>
        </div>
      )}
      {actions.has("skip_journey") && (
        <button type="button" onClick={() => void onResolve(decision.id)} disabled={busy}>
          Skip this journey
        </button>
      )}
    </div>
  );
}

function DecisionContext({
  decision,
  events,
  startLabel,
  timeZone,
}: {
  decision: DayResponse["decisions"][number];
  events: DayResponse["source_events"];
  startLabel: string | null;
  timeZone: string;
}) {
  const originId = String(decision.calculated_facts.origin_occurrence_id ?? "");
  const destinationId = String(
    decision.calculated_facts.destination_occurrence_id ??
      decision.occurrence_id ??
      "",
  );
  const describe = (occurrenceId: string): string | null => {
    if (!occurrenceId) {
      return null;
    }
    if (occurrenceId === "start_place") {
      return startLabel ?? "your start address";
    }
    const event = events.find(
      (candidate) => candidate.occurrence_id === occurrenceId,
    );
    if (!event) {
      return null;
    }
    return `${formatTime(event.start, timeZone)} ${event.title}`;
  };
  const origin = describe(originId);
  const destination = describe(destinationId);
  if (!origin && !destination) {
    return null;
  }
  return (
    <p className="decision-context">
      {origin ?? "an earlier appointment"} → {destination ?? "this appointment"}
    </p>
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
        {decision.allowed_actions.includes("add_anyway") &&
          " Add it anyway and Glide will write the block regardless, ending as the appointment starts."}
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
  if (decision.reason === "no_route") {
    return (
      <p>
        No route could be resolved between these two places, so travel time
        could not be planned. Check the locations and recheck the day.
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
