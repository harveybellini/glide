import { useState, type FormEvent } from "react";
import { moveEvent } from "../api";
import type { CalendarEvent } from "../types";
import { localIso, localWallTime } from "../time";

interface Props {
  event: CalendarEvent;
  dateIso: string;
  timeZone?: string;
  onSaved: (event: CalendarEvent) => void;
  onCancel: () => void;
}

function parseWallTime(value: string): [number, number] {
  const [hours, minutes] = value.split(":").map((part) => Number(part));
  return [hours, minutes];
}

export default function EventEditor({
  event,
  dateIso,
  timeZone,
  onSaved,
  onCancel,
}: Props) {
  const [start, setStart] = useState(localWallTime(event.start, timeZone));
  const [end, setEnd] = useState(localWallTime(event.end, timeZone));
  const [location, setLocation] = useState(event.location ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    if (end <= start) {
      setError("End time must be after start time.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const [startHour, startMinute] = parseWallTime(start);
      const [endHour, endMinute] = parseWallTime(end);
      const updated = await moveEvent(
        event.occurrence_id,
        localIso(dateIso, startHour, startMinute, timeZone),
        localIso(dateIso, endHour, endMinute, timeZone),
        location.trim(),
      );
      onSaved(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the appointment.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="panel event-editor"
      onSubmit={submit}
      onKeyDown={(keyEvent) => {
        if (keyEvent.key === "Escape" && !busy) {
          onCancel();
        }
      }}
      aria-label={`Edit ${event.title}`}
    >
      <div className="field-grid">
        <label>
          Start
          <input
            type="time"
            value={start}
            onChange={(changeEvent) => setStart(changeEvent.target.value)}
            autoFocus
          />
        </label>
        <label>
          End
          <input
            type="time"
            value={end}
            onChange={(changeEvent) => setEnd(changeEvent.target.value)}
          />
        </label>
        <label className="wide">
          Location
          <input
            type="text"
            value={location}
            onChange={(changeEvent) => setLocation(changeEvent.target.value)}
            placeholder="Leave blank for an unresolved location"
          />
        </label>
      </div>
      <div className="panel-actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save changes"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
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
