const DISPLAY_ZONE = "Europe/London";

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_ZONE,
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(`${value}T00:00:00Z`));
}

export function localWallTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_ZONE,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
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

export function localIso(dateIso: string, hour: number, minute: number): string {
  const instant = new Date(`${dateIso}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`);
  const offsetMinutes = Math.round(offsetFor(instant, DISPLAY_ZONE) / 60000);
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absolute = Math.abs(offsetMinutes);
  const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
  const minutes = String(absolute % 60).padStart(2, "0");
  return `${dateIso}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00${sign}${hours}:${minutes}`;
}
