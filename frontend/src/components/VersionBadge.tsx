import { useCallback, useEffect, useRef, useState } from "react";
import { readStored, writeStored } from "../storage";
import {
  buildInfo,
  CHANGELOG_URL,
  describeStatus,
  differsFromBuild,
  fetchApiVersion,
  fetchDeployedVersion,
  formatBuildTime,
  formatClock,
  type MonitorStatus,
  type VersionReport,
} from "../version";

// The deployed manifest is small and served uncached, but there is no reason to
// ask more than once a minute. A tab returning to the foreground re-checks,
// debounced so clicking between windows does not hammer the host.
const POLL_MS = 60_000;
const RECHECK_DEBOUNCE_MS = 15_000;
const DISMISSED_KEY = "glide-dismissed-version";

interface Report {
  status: MonitorStatus;
  deployed: VersionReport | null;
  api: string | null;
  checkedAt: Date | null;
}

const INITIAL: Report = {
  status: "checking",
  deployed: null,
  api: null,
  checkedAt: null,
};

/** Dismissal is remembered per build, so the next deploy still speaks up. */
function identity(report: VersionReport): string {
  return report.commit || report.version;
}

function dotClass(status: MonitorStatus): string {
  if (status === "outdated") {
    return "status-dot ready";
  }
  if (status === "unavailable") {
    return "status-dot quiet";
  }
  return "status-dot";
}

export default function VersionBadge() {
  const [report, setReport] = useState<Report>(INITIAL);
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(() =>
    readStored(DISMISSED_KEY),
  );
  const container = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  const lastChecked = useRef(0);

  const check = useCallback(async (manual = false) => {
    if (inFlight.current) {
      return;
    }
    if (!manual && Date.now() - lastChecked.current < RECHECK_DEBOUNCE_MS) {
      return;
    }
    inFlight.current = true;
    lastChecked.current = Date.now();
    if (manual) {
      setReport((current) => ({ ...current, status: "checking" }));
    }
    const [deployed, api] = await Promise.all([
      fetchDeployedVersion(),
      fetchApiVersion(),
    ]);
    setReport({
      status:
        deployed === null
          ? "unavailable"
          : differsFromBuild(deployed)
            ? "outdated"
            : "current",
      deployed,
      api,
      checkedAt: new Date(),
    });
    inFlight.current = false;
  }, []);

  useEffect(() => {
    void check();
    const wake = () => {
      if (document.visibilityState === "visible") {
        void check();
      }
    };
    const timer = window.setInterval(wake, POLL_MS);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, [check]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    const onPointerDown = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  const deployed = report.deployed;
  const commit = buildInfo.dirty ? `${buildInfo.commit}+` : buildInfo.commit;
  const isStale = report.status === "outdated" && deployed !== null;
  const noticeVisible =
    isStale && deployed !== null && dismissed !== identity(deployed);
  const apiMatches = report.api !== null && report.api === buildInfo.version;

  return (
    <div className="version-badge" ref={container}>
      {noticeVisible && deployed && (
        <div className="update-banner">
          <p role="status">
            <strong>A new version of Glide is ready.</strong>{" "}
            <span className="muted">
              v{deployed.version} - {deployed.commit}
            </span>
          </p>
          <button
            type="button"
            className="primary"
            onClick={() => window.location.reload()}
          >
            Reload
          </button>
          <button
            type="button"
            className="text-button"
            onClick={() => {
              writeStored(DISMISSED_KEY, identity(deployed));
              setDismissed(identity(deployed));
            }}
          >
            Later
          </button>
        </div>
      )}
      <button
        type="button"
        className="version-trigger"
        aria-expanded={open}
        aria-controls={open ? "version-panel" : undefined}
        aria-label={`Version details: Glide v${buildInfo.version}, build ${commit}`}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={dotClass(report.status)} aria-hidden="true" />
        <span>v{buildInfo.version}</span>
        <span className="version-commit">{commit}</span>
      </button>
      {open && (
        <div className="version-panel" id="version-panel">
          <p className="eyebrow">VERSION MONITOR</p>
          <dl className="version-rows">
            <div className="version-row">
              <dt>This tab</dt>
              <dd>
                v{buildInfo.version} - {commit}
                {buildInfo.dirty ? " (local edits)" : ""}
              </dd>
            </div>
            <div className="version-row">
              <dt>Deployed</dt>
              <dd>{describeStatus(report.status, deployed)}</dd>
            </div>
            <div className="version-row">
              <dt>API</dt>
              <dd>
                {report.api
                  ? `v${report.api}${apiMatches ? "" : " (differs)"}`
                  : "not reporting"}
              </dd>
            </div>
            <div className="version-row">
              <dt>Built</dt>
              <dd>{formatBuildTime(buildInfo.builtAt)}</dd>
            </div>
            <div className="version-row">
              <dt>Checked</dt>
              <dd>{report.checkedAt ? formatClock(report.checkedAt) : "…"}</dd>
            </div>
          </dl>
          <div className="version-actions">
            <button
              type="button"
              onClick={() => void check(true)}
              disabled={report.status === "checking"}
            >
              {report.status === "checking" ? "Checking…" : "Check now"}
            </button>
            {isStale && (
              <button
                type="button"
                className="primary"
                onClick={() => window.location.reload()}
              >
                Reload
              </button>
            )}
          </div>
          <p className="small muted">
            Every deploy publishes <code>/version.json</code>.{" "}
            <a href={CHANGELOG_URL} target="_blank" rel="noreferrer">
              What changed
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
