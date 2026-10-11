import { useEffect, useState } from "react";

/** Client-side navigation: the agents service serves index.html for every path, so links work on reload too. */
export function navigate(to: string, replace = false) {
  if (to === window.location.pathname + window.location.search) return;
  window.history[replace ? "replaceState" : "pushState"](null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function useLocation(): { path: string; query: URLSearchParams } {
  const read = () => ({ path: window.location.pathname, query: new URLSearchParams(window.location.search) });
  const [loc, setLoc] = useState(read);
  useEffect(() => {
    const on = () => setLoc(read());
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return loc;
}

/** Update query parameters in place (no new history entry): tabs, filters and sorting survive a reload. */
export function setQuery(changes: Record<string, string | null>) {
  const q = new URLSearchParams(window.location.search);
  for (const [k, v] of Object.entries(changes)) {
    if (v === null || v === "") q.delete(k);
    else q.set(k, v);
  }
  const s = q.toString();
  navigate(window.location.pathname + (s ? `?${s}` : ""), true);
}

/** A link that navigates without reloading the page (and still opens in a new tab with ctrl/cmd-click). */
export function linkProps(to: string) {
  return {
    href: to,
    onClick: (e: React.MouseEvent) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(to);
    },
  };
}
