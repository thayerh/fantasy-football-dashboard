/**
 * The server speaks UTC exclusively; the browser is the only place local time
 * appears. Keeping the conversion here means no component reimplements it.
 */

export function relativeAge(iso: string | null): string {
  if (!iso) return "never";

  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "just now";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  return `${Math.round(hours / 24)}d ago`;
}

export function localTimestamp(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
}

/**
 * Sleeper's league status is snake_case and shows up in three places on the
 * homepage. The hyphen in "Pre-draft" is why this is a map and not a
 * replace(/_/g, " ") — the mechanical version reads "Pre draft".
 */
const STATUS_LABELS: Record<string, string> = {
  pre_draft: "Pre-draft",
  drafting: "Drafting",
  in_season: "In season",
  complete: "Complete",
};

export function humanStatus(status: string | null | undefined): string {
  if (!status) return "Unknown";
  return STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

/**
 * Age in seconds-resolution, for anything that updates faster than a minute.
 *
 * relativeAge() rounds everything under 90 seconds to "just now", which is the
 * right answer for a five-minute sync and useless for a board that reloads
 * every twenty seconds.
 */
export function liveAge(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";

  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return relativeAge(iso);
}

/** Local date and time for a scheduled event, e.g. "Fri, Aug 29, 7:30 PM". */
export function localDateTime(iso: string | null): string {
  if (!iso) return "not scheduled";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(iso));
}

/**
 * A signed gap between two draft positions, rendered the way drafters talk.
 * Both arguments are pick numbers, so their difference is one too.
 *
 * Used for both board stats and the best-available ECR column, which is why it
 * lives here rather than in either component.
 */
export function signedGap(value: number | null, against: number | null): string | null {
  if (value === null || against === null) return null;
  const diff = Math.round(value - against);
  // U+2212, not a hyphen: it aligns with the digits either side of it.
  return diff < 0 ? `\u2212${Math.abs(diff)}` : `+${diff}`;
}
