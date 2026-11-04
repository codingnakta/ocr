"use client";
import { useCallback, useRef, useState } from "react";
import { apiSend, apiUpload } from "@/lib/api";
import type { DocumentInfo } from "@/types/api";

export interface UploadItem {
  key: string;
  file: File;
  status: "waiting" | "uploading" | "done" | "error";
  progress: number;
  error?: string;
  pageIds?: string[];
}

const ACCEPT = ["image/jpeg", "image/png", "image/webp", "application/pdf"];
const EXT = /\.(jpe?g|png|webp|pdf)$/i;

export function isAcceptable(f: File) {
  return ACCEPT.includes(f.type) || EXT.test(f.name);
}

/** Natural sort (IMG_2 < IMG_10) so photos keep the order they were taken in. */
export function naturalSort(files: File[]) {
  const coll = new Intl.Collator("ko", { numeric: true, sensitivity: "base" });
  return [...files].sort((a, b) => coll.compare(a.name, b.name));
}

/**
 * Upload many files robustly: one request per file (small requests survive flaky
 * mobile networks), 3 in parallel, failures retried once. Page order is restored to
 * the selection order afterwards, independent of which upload finished first.
 */
export function useUploader() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  const itemsRef = useRef<UploadItem[]>([]);

  const update = (key: string, patch: Partial<UploadItem>) => {
    itemsRef.current = itemsRef.current.map((it) => (it.key === key ? { ...it, ...patch } : it));
    setItems(itemsRef.current);
  };

  const upload = useCallback(
    async (files: File[], opts: { documentId?: string; title?: string; maxFileMb?: number }) => {
      const valid = files.filter(isAcceptable);
      const list: UploadItem[] = valid.map((file, i) => ({
        key: `${Date.now()}-${i}-${file.name}`,
        file,
        status: "waiting",
        progress: 0,
      }));
      const rejected = files.length - valid.length;
      itemsRef.current = list;
      setItems(list);
      setBusy(true);
      let docId = opts.documentId;
      let existingIds: string[] = [];
      try {
        if (!docId) {
          const doc = await apiSend<DocumentInfo>("POST", "/api/documents", { title: opts.title });
          docId = doc.id;
        } else {
          const doc = await fetch(`/api/documents/${docId}`, { credentials: "same-origin" }).then((r) => r.json());
          existingIds = (doc.pages ?? []).map((p: { id: string }) => p.id);
        }
        const queue = [...list];
        const maxBytes = (opts.maxFileMb ?? 40) * 1024 * 1024;
        const worker = async () => {
          while (queue.length) {
            const it = queue.shift()!;
            if (it.file.size > maxBytes) {
              update(it.key, { status: "error", error: `파일이 너무 큽니다 (최대 ${opts.maxFileMb ?? 40}MB)` });
              continue;
            }
            for (let attempt = 1; attempt <= 2; attempt++) {
              try {
                update(it.key, { status: "uploading", progress: 0, error: undefined });
                const fd = new FormData();
                fd.append("files", it.file, it.file.name);
                const res = await apiUpload<{ upload: { page_ids: string[]; errors: { error: string }[] } }>(
                  `/api/documents/${docId}/files?brief=1`,
                  fd,
                  (l, t) => update(it.key, { progress: t ? l / t : 0 }),
                );
                if (res.upload.errors.length) {
                  update(it.key, { status: "error", error: res.upload.errors[0].error });
                } else {
                  update(it.key, { status: "done", progress: 1, pageIds: res.upload.page_ids });
                }
                break;
              } catch (e) {
                if (attempt === 2) update(it.key, { status: "error", error: (e as Error).message });
              }
            }
          }
        };
        await Promise.all([worker(), worker(), worker()]);
        // restore selection order
        const ordered = itemsRef.current.flatMap((it) => it.pageIds ?? []);
        if (ordered.length) {
          await apiSend("PUT", `/api/documents/${docId}/pages/order`, { page_ids: [...existingIds, ...ordered] });
        }
        return { documentId: docId!, rejected, failed: itemsRef.current.filter((i) => i.status === "error").length };
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  return { items, busy, upload, reset: () => setItems([]) };
}
