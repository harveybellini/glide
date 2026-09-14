// The owner's time zone is the one they chose in Settings; every wall-clock
// conversion goes through the same value so the timeline, the editor and the
// planner agree on what "09:00" means. Europe/London stays the fallback for a
// visitor who has not chosen anything.
export const DEFAULT_TIME_ZONE = "Europe/London";

// Building an Intl.DateTimeFormat is expensive relative to formatting a value,
// and these helpers run once per event per render, so the formatters are built
// once per time zone and reused. The options are fixed here, so a formatter can
// never leak between the different display formats.
const timeFormatters = new Map<string, Intl.DateTimeFormat>();
const dateFormatters = new Map<string, Intl.DateTimeFormat>();
const offsetFormatters = new Map<string, Intl.DateTimeFormat>();

function timeFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = timeFormatters.get(timeZone);
  if (formatter === undefined) {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    timeFormatters.set(timeZone, formatter);
  }
  return formatter;
}

function dateFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = dateFormatters.get(timeZone);
  if (formatter === undefined) {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    });
    dateFormatters.set(timeZone, formatter);
  }
  return formatter;
}

function offsetFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = offsetFormatters.get(timeZone);
  if (formatter === undefined) {
    formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    offsetFormatters.set(timeZone, formatter);
  }
  return formatter;
}

export function formatTime(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return timeFormatter(timeZone).format(new Date(value));
}

export function formatDate(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return dateFormatter(timeZone).format(new Date(`${value}T00:00:00Z`));
}

// "3 min ago" / "in 12 min" for the background status line. Keeping the
// rounding coarse on purpose: an agent countdown does not need seconds.
export function formatRelative(
  value: string,
  now: Date = new Date(),
): string {
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) {
    return "";
  }
  const deltaMinutes = Math.round((target - now.getTime()) / 60000);
  const magnitude = Math.abs(deltaMinutes);
  if (magnitude < 1) {
    return "just now";
  }
  const amount =
    magnitude < 60
      ? `${magnitude} min`
      : magnitude < 1440
        ? `${Math.round(magnitude / 60)} hr`
        : `${Math.round(magnitude / 1440)} day`;
  return deltaMinutes < 0 ? `${amount} ago` : `in ${amount}`;
}

export function localWallTime(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return timeFormatter(timeZone).format(new Date(value));
}

// "Europe/London" -> "London", "America/New_York" -> "New York", "UTC" -> "UTC".
export function timeZoneLabel(timeZone: string): string {
  const zone = timeZone.trim() || DEFAULT_TIME_ZONE;
  if (zone.toUpperCase() === "UTC") {
    return "UTC";
  }
  const city = zone.split("/").pop() ?? zone;
  return city.replace(/_/g, " ");
}

function offsetFor(date: Date, timeZone: string): number {
  const parts = offsetFormatter(timeZone).formatToParts(date);
  const values: Record<string, string> = {};
  for (const part of parts) {
    values[part.type] = part.value;
  }
  const asUtc = new Date(
    `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}:${values.second}Z`,
  );
  // Wall-clock offset: the local wall time is ahead of UTC by this amount.
  return asUtc.getTime() - date.getTime();
}

export function localIso(
  dateIso: string,
  hour: number,
  minute: number,
  timeZone: string = DEFAULT_TIME_ZONE,
): string {
  const instant = new Date(`${dateIso}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`);
  const offsetMinutes = Math.round(offsetFor(instant, timeZone) / 60000);
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absolute = Math.abs(offsetMinutes);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = String(absolute % 60).padStart(2, "0");
  return `${dateIso}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00${sign}${hours}:${minutes}`;
}
