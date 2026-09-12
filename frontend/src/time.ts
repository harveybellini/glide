// The owner's time zone is the one they chose in Settings; every wall-clock
// conversion goes through the same value so the timeline, the editor and the
// planner agree on what "09:00" means. Europe/London stays the fallback for a
// visitor who has not chosen anything.
export const DEFAULT_TIME_ZONE = "Europe/London";

export function formatTime(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

export function formatDate(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00Z`));
}

export function localWallTime(value: string, timeZone: string = DEFAULT_TIME_ZONE): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
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
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
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
