import { useCallback, useEffect, useState } from "react";
import { fetchAuthStatus, signOut } from "../api";
import type { AuthStatus } from "../types";

interface Props {
  compact?: boolean;
  onDisconnected?: () => void;
}

export default function ConnectionStatus({ compact = false, onDisconnected }: Props) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchAuthStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const disconnect = async () => {
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      const result = await signOut();
      if (result.warnings.length > 0) {
        setWarning(result.warnings.join(" "));
      }
      onDisconnected?.();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not sign out.");
    } finally {
      setBusy(false);
    }
  };

  if (!status) {
    return <p className="muted">Checking Google Calendar connection…</p>;
  }

  if (status.connected) {
    if (status.requires_reconnect) {
      return (
        <div className={compact ? "connection compact" : "connection"}>
          <span>
            Google Calendar needs updated event-write permission for the primary
            calendar.
          </span>
          <a className="button-link primary" href="/api/auth/google/start">
            Reconnect Google Calendar
          </a>
        </div>
      );
    }
    return (
      <div className={compact ? "connection compact" : "connection"}>
        <span>
          Connected as <strong>{status.email}</strong>
        </span>
        <button type="button" onClick={disconnect} disabled={busy}>
          {busy ? "Signing out…" : "Disconnect"}
        </button>
        {error && (
          <span className="error" role="alert">
            {error}
          </span>
        )}
        {warning && (
          <span className="warning" role="status">
            {warning}
          </span>
        )}
      </div>
    );
  }

  if (!status.provider_available) {
    return (
      <p className="muted">
        Google Calendar connection is currently unavailable. You can explore
        the sample day without an account.
      </p>
    );
  }

  return (
    <div className={compact ? "connection compact" : "connection"}>
      <a className="button-link primary" href="/api/auth/google/start">
        Connect Google Calendar
      </a>
      {!compact && <span className="muted">Ready for your own day? Connect your calendar.</span>}
      {warning && (
        <span className="warning" role="status">
          {warning}
        </span>
      )}
    </div>
  );
}
