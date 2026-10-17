import { Component, type ReactNode } from "react";

/**
 * The last line of defence: a render that throws shows a panel, not a blank
 * page.
 *
 * This exists because of a real failure. The API and the built frontend are
 * deployed as one unit but *loaded* separately — a leaguemate's tab can hold
 * yesterday's bundle against today's API, or the reverse when the API has been
 * restarted and their browser has not. A field one side expects and the other
 * has never heard of then reaches a component as `undefined`, and one
 * `.length` on it takes down every route at once, including the ones that
 * would have worked.
 *
 * Components should still tolerate a payload from the other version — this
 * catches the case where one did not. React has no hook form of this; a class
 * is the only way to implement it.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    // The journal only sees the server, so the browser console is the record.
    console.error("Render failed:", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <section className="panel">
        <div className="state state--error">
          <h3>Something went wrong on this page</h3>
          <p>
            Reload the page. If it keeps happening, this tab is probably running a
            different version from the server &mdash; a hard refresh, or a restart of{" "}
            <code>ffd-api</code>, puts the two back in step.
          </p>
        </div>
      </section>
    );
  }
}
