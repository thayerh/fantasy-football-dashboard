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
