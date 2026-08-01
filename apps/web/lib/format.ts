export const DEFAULT_TIME_ZONE = "Asia/Shanghai";

export function parseUtcDate(value: string): Date {
  const trimmed = value.trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  const normalized = hasZone || /^\d{4}-\d{2}-\d{2}$/.test(trimmed)
    ? trimmed
    : `${trimmed.replace(" ", "T")}Z`;
  return new Date(normalized);
}

function withTimeZone(
  locale: string,
  timeZone: string,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
  try {
    return new Intl.DateTimeFormat(locale, { ...options, timeZone });
  } catch {
    return new Intl.DateTimeFormat(locale, {
      ...options,
      timeZone: DEFAULT_TIME_ZONE,
    });
  }
}

export function formatDate(
  value: string,
  timeZone = DEFAULT_TIME_ZONE,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium" },
  locale = "zh-CN",
): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) {
    return withTimeZone(locale, "UTC", options).format(new Date(`${value.trim()}T00:00:00Z`));
  }
  const date = parseUtcDate(value);
  return Number.isNaN(date.getTime()) ? "" : withTimeZone(locale, timeZone, options).format(date);
}

export function formatDateTime(
  value: string,
  timeZone = DEFAULT_TIME_ZONE,
  locale = "zh-CN",
): string {
  return formatDate(
    value,
    timeZone,
    { dateStyle: "medium", timeStyle: "medium" },
    locale,
  );
}

export function formatBytes(n: number, locale = "zh-CN"): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const value = n / Math.pow(1024, i);
  return `${new Intl.NumberFormat(locale, {
    maximumFractionDigits: i ? 1 : 0,
  }).format(value)} ${units[i]}`;
}

export function formatTokenCount(n: number, locale = "zh-CN"): string {
  const value = Number.isFinite(n) ? Math.max(0, n) : 0;
  return new Intl.NumberFormat(locale, {
    notation: value >= 1_000 ? "compact" : "standard",
    maximumFractionDigits: value >= 1_000 ? 1 : 0,
  }).format(value);
}

/** Compact duration between two ISO timestamps, e.g. "3s", "2m 5s", "1h 4m". */
export function formatDuration(startIso: string, endIso: string, locale = "zh-CN"): string {
  void locale;
  const start = parseUtcDate(startIso).getTime();
  const end = parseUtcDate(endIso).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "";
  const total = Math.max(0, Math.round((end - start) / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (total < 3600) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  return mins ? `${hours}h ${mins}m` : `${hours}h`;
}

export function relativeTime(
  iso: string,
  timeZone = DEFAULT_TIME_ZONE,
  locale = "zh-CN",
): string {
  const then = parseUtcDate(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = (then - Date.now()) / 1_000;
  const abs = Math.abs(seconds);
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60) return relative.format(0, "second");
  if (abs < 60 * 60) return relative.format(Math.round(seconds / 60), "minute");
  if (abs < 60 * 60 * 24) return relative.format(Math.round(seconds / 3_600), "hour");
  if (abs < 60 * 60 * 24 * 30) {
    return relative.format(Math.round(seconds / 86_400), "day");
  }
  return formatDate(iso, timeZone, { dateStyle: "medium" }, locale);
}
