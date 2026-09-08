import { useState, type FormEvent } from "react";
import { patchSettings } from "../api";
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

interface Props {
  settings: UserSettings;
  onSaved: (settings: UserSettings) => void;
  onClose: () => void;
}

export default function SettingsPanel({ settings, onSaved, onClose }: Props) {
  const [padding, setPadding] = useState(String(settings.padding_minutes));
  const [departure, setDeparture] = useState(settings.earliest_departure ?? "");
  const [startPlace, setStartPlace] = useState(settings.start_place?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const paddingValue = Number(padding);
    if (!Number.isInteger(paddingValue) || paddingValue < 0 || paddingValue > 60) {
      setError("Arrival buffer must be a whole number between 0 and 60 minutes.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updates: {
        padding_minutes?: number;
        earliest_departure?: string;
        start_place?: PlaceRef | null;
      } = {
        padding_minutes: paddingValue,
      };
      if (departure) {
        updates.earliest_departure = departure;
      }
      if (startPlace !== (settings.start_place?.id ?? "")) {
        updates.start_place =
          SAMPLE_START_PLACES.find((place) => place.id === startPlace) ?? null;
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
      className="panel settings-panel"
      onSubmit={submit}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
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
