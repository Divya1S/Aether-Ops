import { useEffect, useState } from "react";

// Hash routing: the console ships as one self-contained file that must work
// from the API root, from GitHub Pages under a sub-path, and from file://.
// The hash is the only part of the URL all three leave alone.

export interface Route {
  view: string; // "", "alerts", "incidents", ...
  id: string | null; // second segment, e.g. an incident id
}

export function parseHash(hash: string): Route {
  const [view = "", id] = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return { view, id: id ? decodeURIComponent(id) : null };
}

export function href(view: string, id?: string): string {
  return `#/${view}${id ? `/${encodeURIComponent(id)}` : ""}`;
}

export function navigate(view: string, id?: string): void {
  window.location.hash = href(view, id).slice(1);
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = (): void => {
      setRoute(parseHash(window.location.hash));
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
