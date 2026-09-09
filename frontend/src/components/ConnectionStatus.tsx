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
    try {
      await signOut();
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
      </div>
    );
  }

  if (!status.provider_available) {
    return (
      <p className="muted">
        Google Calendar connection is unavailable until provider credentials are
        configured. The sample day below needs no account.
      </p>
    );
  }

  return (
    <div className={compact ? "connection compact" : "connection"}>
      <a className="button-link primary" href="/api/auth/google/start">
        Connect Google Calendar
      </a>
      {!compact && <span className="muted">or try the sample day below.</span>}
    </div>
  );
}
