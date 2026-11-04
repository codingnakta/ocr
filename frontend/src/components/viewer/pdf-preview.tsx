"use client";
import { useEffect, useRef, useState } from "react";
import { Copy, Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/primitives";
import { toast } from "sonner";

type PdfJs = typeof import("pdfjs-dist");

/**
 * Renders the generated PDF with PDF.js (canvas + text layer), exactly the way the
 * Firefox/PDF.js viewer does, so the user can try drag-select, copy and search on
 * the real output file before downloading it.
 */
export function PdfPreview({ url }: { url: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [pages, setPages] = useState(0);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<number | null>(null);
  const [selection, setSelection] = useState("");

  useEffect(() => {
    let cancelled = false;
    let task: { destroy: () => Promise<void> } | null = null;
    (async () => {
      try {
        const pdfjs: PdfJs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.mjs";
        const data = await fetch(url, { credentials: "same-origin" }).then((r) => {
          if (!r.ok) throw new Error(`PDF를 불러오지 못했습니다 (${r.status})`);
          return r.arrayBuffer();
        });
        const loading = pdfjs.getDocument({ data });
        task = loading;
        const pdf = await loading.promise;
        if (cancelled) return;
        setPages(pdf.numPages);
        const container = host.current!;
        container.innerHTML = "";
        const width = Math.min(container.clientWidth || 800, 1000);
        for (let n = 1; n <= pdf.numPages; n++) {
          const page = await pdf.getPage(n);
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: width / base.width });
          const wrap = document.createElement("div");
          wrap.className = "relative mx-auto mb-4 bg-white shadow";
          wrap.style.width = `${viewport.width}px`;
          wrap.style.height = `${viewport.height}px`;
          wrap.style.setProperty("--scale-factor", String(viewport.scale));
          wrap.style.setProperty("--total-scale-factor", String(viewport.scale));
          wrap.dataset.page = String(n);
          const canvas = document.createElement("canvas");
          const ratio = window.devicePixelRatio || 1;
          canvas.width = Math.floor(viewport.width * ratio);
          canvas.height = Math.floor(viewport.height * ratio);
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;
          wrap.appendChild(canvas);
          const layer = document.createElement("div");
          layer.className = "textLayer";
          wrap.appendChild(layer);
          container.appendChild(wrap);
          await page.render({
            canvas,
            viewport,
            transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
          }).promise;
          const tl = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport });
          await tl.render();
          const end = document.createElement("div");
          end.className = "endOfContent";
          layer.appendChild(end);
          if (cancelled) return;
        }
        setState("ready");
      } catch (e) {
        if (!cancelled) {
          setState("error");
          toast.error((e as Error).message);
        }
      }
    })();
    const onSel = () => {
      const sel = window.getSelection();
      const inside = sel && sel.anchorNode && host.current?.contains(sel.anchorNode);
      setSelection(inside ? sel!.toString() : "");
    };
    // Like PDF.js' TextLayerBuilder: while dragging, stretch .endOfContent over the
    // whole layer so passing over gaps between lines does not make the selection jump.
    const onDown = (e: PointerEvent) => {
      const layer = (e.target as Element | null)?.closest?.(".textLayer");
      if (layer && host.current?.contains(layer)) layer.classList.add("selecting");
    };
    const onUp = () => host.current?.querySelectorAll(".textLayer.selecting").forEach((l) => l.classList.remove("selecting"));
    document.addEventListener("selectionchange", onSel);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("pointerup", onUp);
    return () => {
      cancelled = true;
      document.removeEventListener("selectionchange", onSel);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerup", onUp);
      void task?.destroy();
    };
  }, [url]);

  const search = () => {
    const root = host.current;
    if (!root) return;
    root.querySelectorAll(".pb-hit").forEach((el) => el.classList.remove("pb-hit"));
    const q = query.replace(/\s+/g, "");
    if (!q) {
      setHits(null);
      return;
    }
    let count = 0;
    let first: Element | null = null;
    root.querySelectorAll(".textLayer").forEach((layer) => {
      const spans = Array.from(layer.querySelectorAll("span")).filter((s) => !s.children.length);
      // match across spans of the same page (lines are split into several spans)
      const joined = spans.map((s) => (s.textContent ?? "").replace(/\s+/g, ""));
      const all = joined.join("");
      let from = 0;
      for (;;) {
        const at = all.indexOf(q, from);
        if (at < 0) break;
        count++;
        let pos = 0;
        spans.forEach((s, i) => {
          const len = joined[i].length;
          if (pos + len > at && pos < at + q.length) {
            s.classList.add("pb-hit");
            first ??= s;
          }
          pos += len;
        });
        from = at + q.length;
      }
    });
    setHits(count);
    (first as Element | null)?.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  return (
    <div className="space-y-3">
      <div className="sticky top-14 z-10 flex flex-wrap items-center gap-2 rounded-lg border bg-card p-2">
        <form
          className="flex flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            search();
          }}
        >
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="PDF 안에서 검색 (예: 순차회로)" className="max-w-xs" data-testid="pdf-search" />
          <Button type="submit" size="sm" variant="secondary">
            <Search /> 검색
          </Button>
          {hits !== null && <span className="text-sm text-muted-foreground" data-testid="pdf-hits">{hits}건</span>}
        </form>
        <span className="text-xs text-muted-foreground">{pages ? `${pages}쪽` : ""}</span>
      </div>
      {selection && (
        // fixed overlay: showing the selection must not shift the pages while dragging
        <div className="fixed inset-x-3 bottom-3 z-30 mx-auto flex max-w-3xl select-none items-start gap-2 rounded-lg border bg-card p-2 text-sm shadow-lg">
          <span className="max-h-24 min-w-0 flex-1 overflow-auto whitespace-pre-wrap break-all" data-testid="pdf-selection">
            {selection}
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigator.clipboard?.writeText(selection).then(() => toast.success("복사했습니다."))}
          >
            <Copy /> 복사
          </Button>
        </div>
      )}
      {state === "loading" && (
        <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> PDF.js로 렌더링 중…
        </div>
      )}
      <div ref={host} data-testid="pdf-pages" data-state={state} className="overflow-x-auto" />
    </div>
  );
}
