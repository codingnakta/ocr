"use client";
import { useEffect } from "react";

/** Registers the PWA service worker (installable app shell). OCR itself always needs
 * the network: the worker never caches /api responses. */
export function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  return null;
}
