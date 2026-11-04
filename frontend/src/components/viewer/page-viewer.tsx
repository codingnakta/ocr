"use client";
import { useRef, useState } from "react";
import { Minus, Plus, ScanLine, Scan } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tip } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { Region } from "@/types/api";

/** Page image with OCR region overlays (normalized polygons). Supports zoom and
 * drawing a rectangle to re-recognize an arbitrary area. */
export function PageViewer({
  src,
  regions,
  selectedId,
  onSelect,
  onDrawn,
  showBoxes = true,
}: {
  src: string;
  regions: Region[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDrawn?: (rect: [number, number, number, number]) => void;
  showBoxes?: boolean;
}) {
  const [zoom, setZoom] = useState(1);
  const [draw, setDraw] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const pos = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 border-b bg-card px-2 py-1.5">
        <Tip label="축소">
          <Button size="icon-sm" variant="ghost" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} aria-label="축소">
            <Minus />
          </Button>
        </Tip>
        <button className="w-12 text-center text-xs tabular-nums text-muted-foreground" onClick={() => setZoom(1)}>
          {Math.round(zoom * 100)}%
        </button>
        <Tip label="확대">
          <Button size="icon-sm" variant="ghost" onClick={() => setZoom((z) => Math.min(4, z + 0.25))} aria-label="확대">
            <Plus />
          </Button>
        </Tip>
        {onDrawn && (
          <Button
            size="sm"
            variant={draw ? "default" : "ghost"}
            className="ml-auto"
            onClick={() => setDraw((d) => !d)}
            data-testid="draw-toggle"
          >
            {draw ? <ScanLine /> : <Scan />} {draw ? "영역을 드래그하세요" : "영역 선택 재인식"}
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-muted p-3">
        <div className="mx-auto" style={{ width: `${zoom * 100}%`, maxWidth: zoom === 1 ? 900 : undefined }}>
          <div
            ref={box}
            className={cn("relative select-none", draw && "cursor-crosshair touch-none")}
            onPointerDown={(e) => {
              if (!draw) return;
              (e.target as Element).setPointerCapture?.(e.pointerId);
              const p = pos(e);
              setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
            }}
            onPointerMove={(e) => {
              if (!draw || !drag) return;
              const p = pos(e);
              setDrag({ ...drag, x1: p.x, y1: p.y });
            }}
            onPointerUp={() => {
              if (!draw || !drag) return;
              const r: [number, number, number, number] = [
                Math.min(drag.x0, drag.x1),
                Math.min(drag.y0, drag.y1),
                Math.max(drag.x0, drag.x1),
                Math.max(drag.y0, drag.y1),
              ];
              setDrag(null);
              if (r[2] - r[0] > 0.01 && r[3] - r[1] > 0.005) {
                setDraw(false);
                onDrawn?.(r);
              }
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt="원본 페이지" className="block w-full rounded shadow-sm" draggable={false} data-testid="page-image" />
            {showBoxes && (
              <svg className="absolute inset-0 size-full" viewBox="0 0 1 1" preserveAspectRatio="none">
                {regions
                  .filter((r) => r.polygon)
                  .map((r) => {
                    const pts = r.polygon!.map(([x, y]) => `${x},${y}`).join(" ");
                    const sel = r.id === selectedId;
                    const color = r.edited ? "oklch(0.58 0.13 155)" : r.uncertain && !r.reviewed ? "oklch(0.72 0.16 70)" : "oklch(0.55 0.15 262)";
                    return (
                      <polygon
                        key={r.id}
                        points={pts}
                        vectorEffect="non-scaling-stroke"
                        style={{
                          fill: sel ? "oklch(0.6 0.15 262 / 0.18)" : r.uncertain && !r.reviewed ? "oklch(0.85 0.15 85 / 0.16)" : "transparent",
                          stroke: color,
                          strokeWidth: sel ? 2.5 : 1.2,
                          cursor: draw ? "crosshair" : "pointer",
                          pointerEvents: draw ? "none" : "auto",
                        }}
                        onClick={() => onSelect(r.id)}
                        data-testid="region-box"
                      />
                    );
                  })}
                {drag && (
                  <rect
                    x={Math.min(drag.x0, drag.x1)}
                    y={Math.min(drag.y0, drag.y1)}
                    width={Math.abs(drag.x1 - drag.x0)}
                    height={Math.abs(drag.y1 - drag.y0)}
                    vectorEffect="non-scaling-stroke"
                    style={{ fill: "oklch(0.6 0.15 262 / 0.12)", stroke: "oklch(0.55 0.15 262)", strokeWidth: 1.5, strokeDasharray: "4 3" }}
                  />
                )}
              </svg>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
