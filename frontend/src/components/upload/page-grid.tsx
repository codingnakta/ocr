"use client";
import { useEffect, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { AlertTriangle, CheckCircle2, FileText, GripVertical, Maximize2, RotateCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Dialog, DialogContent } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { PageInfo } from "@/types/api";

const CONTENT_LABEL: Record<string, string> = {
  text: "텍스트 PDF",
  mixed: "텍스트+이미지",
  image_only: "스캔/손글씨",
  empty: "빈 페이지",
};

export function PageGrid({
  pages,
  onReorder,
  onRotate,
  onDelete,
  disabled,
}: {
  pages: PageInfo[];
  onReorder: (ids: string[]) => void;
  onRotate: (p: PageInfo) => void;
  onDelete: (p: PageInfo) => void;
  disabled?: boolean;
}) {
  const [order, setOrder] = useState(pages.map((p) => p.id));
  const [preview, setPreview] = useState<PageInfo | null>(null);
  useEffect(() => setOrder(pages.map((p) => p.id)), [pages]);
  const byId = Object.fromEntries(pages.map((p) => [p.id, p]));

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    // touch: press-and-hold to drag so normal scrolling still works on phones
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return;
    const next = arrayMove(order, order.indexOf(String(e.active.id)), order.indexOf(String(e.over.id)));
    setOrder(next);
    onReorder(next);
  };

  return (
    <>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <SortableContext items={order} strategy={rectSortingStrategy} disabled={disabled}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6" data-testid="page-grid">
            {order.map((id, i) =>
              byId[id] ? (
                <PageCard
                  key={id}
                  page={byId[id]}
                  index={i}
                  onRotate={onRotate}
                  onDelete={onDelete}
                  onPreview={setPreview}
                  disabled={disabled}
                />
              ) : null,
            )}
          </div>
        </SortableContext>
      </DndContext>
      <Dialog open={!!preview} onOpenChange={(o) => !o && setPreview(null)}>
        {preview && (
          <DialogContent title={`${preview.number}쪽 · ${preview.label}`} className="max-w-4xl">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={preview.image_url} alt={preview.label} className="max-h-[75vh] w-full rounded-md object-contain" />
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}

function PageCard({
  page,
  index,
  onRotate,
  onDelete,
  onPreview,
  disabled,
}: {
  page: PageInfo;
  index: number;
  onRotate: (p: PageInfo) => void;
  onDelete: (p: PageInfo) => void;
  onPreview: (p: PageInfo) => void;
  disabled?: boolean;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: page.id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("group relative flex flex-col overflow-hidden rounded-lg border bg-card", isDragging && "z-10 shadow-lg ring-2 ring-primary")}
      data-testid="page-card"
    >
      <div
        className="relative aspect-[3/4] cursor-grab touch-manipulation bg-muted active:cursor-grabbing"
        {...attributes}
        {...listeners}
        aria-label={`${index + 1}쪽, 끌어서 순서 변경`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={page.thumb_url} alt="" className="absolute inset-0 size-full object-contain p-1" draggable={false} loading="lazy" />
        <span className="absolute left-1.5 top-1.5 rounded bg-foreground/75 px-1.5 text-xs font-medium text-background">{index + 1}</span>
        <GripVertical className="absolute right-1 top-1.5 size-4 text-muted-foreground opacity-0 group-hover:opacity-100" />
        <div className="absolute bottom-1 left-1 flex flex-wrap gap-1">
          {page.ocr_status === "done" && (
            <Badge variant="success">
              <CheckCircle2 /> OCR
            </Badge>
          )}
          {page.ocr_status === "failed" && (
            <Badge variant="destructive">
              <AlertTriangle /> 실패
            </Badge>
          )}
          {page.uncertain > 0 && <Badge variant="warning">확인 {page.uncertain}</Badge>}
          {page.source_kind === "pdf" && CONTENT_LABEL[page.content_type] && (
            <Badge variant="secondary">
              <FileText /> {CONTENT_LABEL[page.content_type]}
            </Badge>
          )}
        </div>
      </div>
      <div className="flex items-center gap-0.5 border-t px-1.5 py-1">
        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" title={page.label}>
          {page.label}
        </span>
        <Button size="icon-sm" variant="ghost" onClick={() => onPreview(page)} aria-label="확대">
          <Maximize2 />
        </Button>
        <Button size="icon-sm" variant="ghost" onClick={() => onRotate(page)} disabled={disabled} aria-label="회전">
          <RotateCw />
        </Button>
        <Button size="icon-sm" variant="ghost" onClick={() => onDelete(page)} disabled={disabled} aria-label="삭제">
          <Trash2 />
        </Button>
      </div>
    </div>
  );
}
