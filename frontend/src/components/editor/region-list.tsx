"use client";
import { useEffect, useRef } from "react";
import { AlertTriangle, Check, MapPinOff, RotateCcw, ScanText, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Textarea } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { Region } from "@/types/api";

const ENGINE: Record<string, string> = {
  claude: "Claude",
  claude_region: "Claude 재인식",
  local: "로컬",
  user: "직접 입력",
};

export function RegionList({
  regions,
  drafts,
  selectedId,
  onSelect,
  onEdit,
  onRevert,
  onReviewed,
  onReread,
  showRaw,
}: {
  regions: Region[];
  drafts: Record<string, string>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onEdit: (r: Region, text: string) => void;
  onRevert: (r: Region) => void;
  onReviewed: (r: Region, v: boolean) => void;
  onReread: (r: Region) => void;
  showRaw: boolean;
}) {
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    if (selectedId) refs.current[selectedId]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

  if (!regions.length) {
    return <p className="p-6 text-center text-sm text-muted-foreground">인식된 텍스트가 없습니다. OCR을 실행하거나, 원본에서 영역을 선택해 추가하세요.</p>;
  }
  let lastBlock = -1;
  return (
    <div className="space-y-1.5 p-3" data-testid="region-list">
      {regions.map((r) => {
        const text = drafts[r.id] ?? r.text;
        const newBlock = r.block !== lastBlock;
        lastBlock = r.block;
        const sel = r.id === selectedId;
        return (
          <div key={r.id} ref={(el) => { refs.current[r.id] = el; }} className={cn(newBlock && "pt-2")}>
            <div
              onClick={() => onSelect(r.id)}
              className={cn(
                "rounded-lg border bg-card p-2 transition-colors",
                sel && "border-primary ring-1 ring-primary",
                r.uncertain && !r.reviewed && !sel && "border-[oklch(0.85_0.1_80)] bg-warning-soft/40",
              )}
              data-testid="region-item"
            >
              <div className="mb-1 flex flex-wrap items-center gap-1">
                <span className="text-[11px] text-muted-foreground">
                  {r.block_type === "heading" ? "제목" : r.block_type === "equation" ? "수식" : r.block_type === "list_item" ? "목록" : ""}
                </span>
                {r.uncertain && !r.reviewed && (
                  <Badge variant="warning">
                    <AlertTriangle /> 확인 필요
                  </Badge>
                )}
                {r.edited && <Badge variant="success">수정됨</Badge>}
                {r.reviewed && (
                  <Badge variant="success">
                    <Check /> 검수
                  </Badge>
                )}
                {!r.aligned && (
                  <Badge variant="secondary" title="글자 위치를 확인하지 못해 PDF 텍스트 레이어에서 제외됩니다">
                    <MapPinOff /> 위치 없음
                  </Badge>
                )}
                <span className="ml-auto text-[11px] text-muted-foreground">{ENGINE[r.engine] ?? r.engine}</span>
              </div>
              <Textarea
                value={text}
                onChange={(e) => onEdit(r, e.target.value)}
                onFocus={() => onSelect(r.id)}
                rows={Math.max(1, Math.ceil(text.length / 34))}
                className="resize-none border-transparent bg-transparent px-1 py-0.5 text-[15px] leading-relaxed shadow-none focus:border-input focus:bg-card"
                aria-label={`영역 ${r.key} 텍스트`}
                data-testid="region-text"
              />
              {r.uncertain_parts.length > 0 && !r.reviewed && (
                <p className="mt-1 px-1 text-xs text-[oklch(0.5_0.12_70)]">불확실: {r.uncertain_parts.map((p) => `“${p}”`).join(", ")}</p>
              )}
              {r.alternatives.length > 0 && (
                <div className="mt-1 flex flex-wrap items-center gap-1 px-1 text-xs">
                  <span className="text-muted-foreground">다른 판독:</span>
                  {r.alternatives.map((a) => (
                    <button
                      key={a}
                      className="rounded border bg-muted px-1.5 py-0.5 hover:border-primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        onEdit(r, a);
                      }}
                    >
                      {a}
                    </button>
                  ))}
                </div>
              )}
              {showRaw && (
                <div className="mt-1 space-y-0.5 rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
                  <div>OCR 원본: {r.text_ocr || "∅"}</div>
                  {r.text_claude !== null && r.text_claude !== r.text_ocr && <div>Claude 1차: {r.text_claude}</div>}
                  {r.text_local && <div>로컬: {r.text_local}</div>}
                </div>
              )}
              {sel && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  <Button size="sm" variant={r.reviewed ? "secondary" : "outline"} onClick={() => onReviewed(r, !r.reviewed)}>
                    <Check /> {r.reviewed ? "검수 취소" : "검수 완료"}
                  </Button>
                  {r.edited && (
                    <Button size="sm" variant="ghost" onClick={() => onRevert(r)}>
                      <Undo2 /> OCR 결과로 되돌리기
                    </Button>
                  )}
                  {r.aligned && (
                    <Button size="sm" variant="ghost" onClick={() => onReread(r)}>
                      <ScanText /> 이 줄 재인식
                    </Button>
                  )}
                  {drafts[r.id] !== undefined && (
                    <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
                      <RotateCcw className="size-3 animate-spin" /> 저장 중
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
