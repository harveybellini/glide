import { useState, type FormEvent } from "react";
import { patchSettings, searchPlaces } from "../api";
import type { PlaceRef, UserSettings } from "../types";

const SAMPLE_START_PLACES: PlaceRef[] = [
  {
    id: "place_a",
    label: "Northside Community Centre",
    provenance: "sample",
    confirmed: true,
    storage_policy_status: "ephemeral",
  },
  {
    id: "place_b",
    label: "Westfield Surgery",
    provenance: "sample",
    confirmed: true,
    storage_policy_status: "ephemeral",
  },
  {
    id: "place_c",
    label: "Oakfield Primary School",
    provenance: "sample",
    confirmed: true,
    storage_policy_status: "ephemeral",
  },
];

const TIME_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "America/New_York",
  "America/Los_Angeles",
];

interface Props {
  settings: UserSettings;
  live: boolean;
  onSaved: (settings: UserSettings) => void;
  onClose: () => void;
}

export default function SettingsPanel({
  settings,
  live,
  onSaved,
  onClose,
}: Props) {
  const [padding, setPadding] = useState(String(settings.padding_minutes));
  // The API returns a wall time with seconds ("07:45:00"); the time input only
  // understands HH:MM, so normalise once here and compare in the same shape.
  const [departure, setDeparture] = useState(
    (settings.earliest_departure ?? "").slice(0, 5),
  );
  const [startPlace, setStartPlace] = useState(settings.start_place?.id ?? "");
  const [placeQuery, setPlaceQuery] = useState("");
  const [candidates, setCandidates] = useState<PlaceRef[]>([]);
  const [timeZone, setTimeZone] = useState(settings.time_zone);
  const [notificationEmail, setNotificationEmail] = useState(
    settings.notification_email ?? "",
  );
  const [notifyOnDecisions, setNotifyOnDecisions] = useState(
    settings.notify_on_decisions,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  const search = async () => {
    if (!placeQuery.trim()) {
      return;
    }
    setSearching(true);
    setError(null);
    try {
      setCandidates(await searchPlaces(placeQuery));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Place search failed.");
    } finally {
      setSearching(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const paddingText = padding.trim();
    const paddingValue = Number(paddingText);
    if (
      paddingText === "" ||
      !/^\d+$/.test(paddingText) ||
      !Number.isInteger(paddingValue) ||
      paddingValue < 0 ||
      paddingValue > 60
    ) {
      setError("Arrival buffer must be a whole number between 0 and 60 minutes.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updates: {
        padding_minutes?: number;
        earliest_departure?: string | null;
        start_place?: PlaceRef | null;
        time_zone?: string;
        notification_email?: string;
        notify_on_decisions?: boolean;
      } = {
        padding_minutes: paddingValue,
      };
      const departureValue = departure.slice(0, 5);
      if (departureValue !== (settings.earliest_departure ?? "").slice(0, 5)) {
        updates.earliest_departure = departureValue || null;
      }
      if (startPlace !== (settings.start_place?.id ?? "")) {
        updates.start_place = live
          ? candidates.find((place) => place.id === startPlace) ?? null
          : SAMPLE_START_PLACES.find((place) => place.id === startPlace) ?? null;
      }
      if (timeZone !== settings.time_zone) {
        updates.time_zone = timeZone;
      }
      if (live) {
        const trimmedEmail = notificationEmail.trim();
        if (trimmedEmail !== (settings.notification_email ?? "")) {
          updates.notification_email = trimmedEmail;
        }
        if (notifyOnDecisions !== settings.notify_on_decisions) {
          updates.notify_on_decisions = notifyOnDecisions;
        }
      }
      onSaved(await patchSettings(updates));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save settings.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      id="travel-settings"
      className="panel settings-panel"
      onSubmit={submit}
      noValidate
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) {
          onClose();
        }
      }}
      aria-label="Travel settings"
    >
      <h2>Travel settings</h2>
      <label>
        Arrival buffer (minutes)
        <input
          type="number"
          min={0}
          max={60}
          step={1}
          value={padding}
          onChange={(event) => setPadding(event.target.value)}
          autoFocus
        />
      </label>
      <label>
        Earliest departure (optional)
        <input
          type="time"
          value={departure}
          onChange={(event) => setDeparture(event.target.value)}
        />
      </label>
      {live ? (
        <div className="place-search">
          <label>
            Start address
            <div className="search-row">
              <input
                type="text"
                value={placeQuery}
                onChange={(event) => setPlaceQuery(event.target.value)}
                placeholder="Search for your starting address"
              />
              <button type="button" onClick={search} disabled={searching}>
                {searching ? "Searching…" : "Search"}
              </button>
            </div>
          </label>
          {candidates.length > 0 && (
            <label>
              Confirmed start
              <select
                value={startPlace}
                onChange={(event) => setStartPlace(event.target.value)}
              >
                <option value="">No fixed start (ask me)</option>
                {candidates.map((place) => (
                  <option key={place.id} value={place.id}>
                    {place.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      ) : (
        <label>
          Start address
          <select
            value={startPlace}
            onChange={(event) => setStartPlace(event.target.value)}
          >
            <option value="">No fixed start (ask me)</option>
            {SAMPLE_START_PLACES.map((place) => (
              <option key={place.id} value={place.id}>
                {place.label}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Time zone
        <select value={timeZone} onChange={(event) => setTimeZone(event.target.value)}>
          {TIME_ZONES.map((zone) => (
            <option key={zone} value={zone}>
              {zone}
            </option>
          ))}
        </select>
      </label>
      {live && (
        <fieldset className="notification-settings">
          <legend>Decision emails</legend>
          <label>
            Email for &ldquo;needs your decision&rdquo; messages
            <input
              type="email"
              maxLength={254}
              value={notificationEmail}
              placeholder="you@example.com"
              onChange={(event) => setNotificationEmail(event.target.value)}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={notifyOnDecisions}
              onChange={(event) => setNotifyOnDecisions(event.target.checked)}
            />
            Email me when a decision needs me
          </label>
          <p className="field-hint">
            Glide stays quiet otherwise. Clear the address to turn these
            messages off.
          </p>
        </fieldset>
      )}
      <div className="panel-actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save settings"}
        </button>
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
