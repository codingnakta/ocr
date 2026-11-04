"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import type { DocumentInfo } from "@/types/api";

const ACTIVE = new Set(["queued", "running"]);

/** Loads a document and keeps polling while an OCR job or export is in progress. */
export function useDocument(id: string) {
  const [doc, setDoc] = useState<DocumentInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await apiGet<DocumentInfo>(`/api/documents/${id}`);
      setDoc(d);
      setError(null);
      return d;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [id]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const d = await load();
      if (!alive) return;
      const busy =
        (d?.active_job && ACTIVE.has(d.active_job.status)) ||
        d?.exports?.some((e) => e.status === "queued" || e.status === "running");
      timer.current = setTimeout(tick, busy ? 1500 : 8000);
    };
    tick();
    return () => {
      alive = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  return { doc, setDoc, error, reload: load };
}
