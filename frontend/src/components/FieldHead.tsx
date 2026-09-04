import type { ReactNode } from "react";

/**
 * The three-part page header from GAMEDAY.md §2 — kicker, h1, facts — plus
 * whatever sits on the right (a freshness pill, so far). Every page fills all
 * three parts; an h1 on its own floats.
 */
export function FieldHead({
  kicker,
  title,
  facts,
  aside,
}: {
  kicker: ReactNode;
  title: ReactNode;
  facts: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <div className="field-head">
      <div>
        <p className="kicker">{kicker}</p>
        <h1>{title}</h1>
        <p className="subline">{facts}</p>
      </div>
      {aside}
    </div>
  );
}
