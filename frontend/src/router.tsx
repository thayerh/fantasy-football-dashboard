import { useCallback, useEffect, useState, type ReactNode } from "react";

/**
 * Two pages' worth of routing, and no more.
 *
 * A router library buys parameterised routes, nested layouts and loaders —
 * none of which exist here yet. This is the minimum that gives each page a
 * real URL: the SPA fallback in main.py already serves index.html for any
 * path, so /draft survives a hard refresh.
 *
 * Swap in react-router when the first route with a parameter arrives.
 */

const POP = "popstate";

/**
 * Nav label -> path, for the pages that exist. Everything else in the masthead
 * and rail is a placeholder, and both render placeholders as plain text rather
 * than as links that go nowhere.
 */
export const ROUTES: Record<string, string> = {
  Standings: "/",
  Draft: "/draft",
};

export function usePath(): string {
  const [path, setPath] = useState(() => window.location.pathname);

  useEffect(() => {
    const sync = () => setPath(window.location.pathname);
    window.addEventListener(POP, sync);
    return () => window.removeEventListener(POP, sync);
  }, []);

  return path;
}

export function navigate(to: string): void {
  if (to === window.location.pathname) return;
  window.history.pushState({}, "", to);
  // usePath listens for popstate; pushState doesn't fire it on its own.
  window.dispatchEvent(new PopStateEvent(POP));
  window.scrollTo({ top: 0 });
}

/**
 * A real anchor with a real href — middle-click, ctrl-click and "copy link"
 * all keep working — that skips the full page load on a plain left click.
 */
export function Link({
  href,
  className,
  children,
  ...rest
}: {
  href: string;
  className?: string;
  children: ReactNode;
} & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const onClick = useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return; // let the browser open a tab / window
      }
      event.preventDefault();
      navigate(href);
    },
    [href],
  );

  return (
    <a href={href} className={className} onClick={onClick} {...rest}>
      {children}
    </a>
  );
}
