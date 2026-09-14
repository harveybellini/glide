import type { AutomationStatus } from "../types";
import { formatRelative } from "../time";

interface Props {
  automation: AutomationStatus;
  live: boolean;
  busy: boolean;
  onToggle: (next: boolean) => void;
}

/**
 * One line that answers "what has the agent been doing without me?".
 *
 * The day view used to be a dashboard you checked; this strip is the part
 * that makes the background work visible, including the next scheduled run
 * and how many checks happened while the tab was closed.
 */
export default function BackgroundStatus({
  automation,
  live,
  busy,
  onToggle,
}: Props) {
  const lastCheck = automation.last_check_at
    ? formatRelative(automation.last_check_at)
    : null;
  const nextCheck = automation.next_check_at
    ? formatRelative(automation.next_check_at)
    : null;

  let title: string;
  if (automation.watching) {
    title = live
      ? `Watching your calendar — a check every ${automation.interval_minutes} minutes.`
      : `Watching this sample day — a check every ${automation.interval_minutes} minutes.`;
  } else if (automation.enabled) {
    title = "Automation is on, but background watching is off.";
  } else {
    title = "Watching is paused.";
  }

  const details = automation.watching
    ? [
        lastCheck ? `Last check ${lastCheck}` : "First check on its way",
        nextCheck ? `next ${nextCheck}` : null,
        automation.checks_since_last_view > 0
          ? `${automation.checks_since_last_view} since you were last here`
          : null,
      ].filter((part): part is string => part !== null)
    : [
        live
          ? "Turn it on and Glide keeps working while this tab is closed."
          : "Turn it on to see the agent work without pressing anything.",
      ];

  return (
    <section
      className={automation.watching ? "background-status" : "background-status paused"}
      aria-label="Background agent"
      data-tour="background"
    >
      <span
        className={automation.watching ? "status-dot" : "status-dot paused"}
        aria-hidden="true"
      />
      <div className="background-copy">
        <strong>{title}</strong>
        <span>{details.join(" · ")}</span>
      </div>
      <button
        type="button"
        className="text-button"
        onClick={() => onToggle(!automation.watching)}
        disabled={busy}
      >
        {automation.watching ? "Stop watching" : "Start watching"}
      </button>
    </section>
  );
}
