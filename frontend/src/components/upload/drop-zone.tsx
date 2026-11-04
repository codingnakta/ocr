"use client";
import { useRef, useState } from "react";
import { Camera, FileUp, ImagePlus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Drag & drop + file picker (multi) + direct camera capture on phones. */
export function DropZone({
  onFiles,
  disabled,
  compact = false,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const pick = useRef<HTMLInputElement>(null);
  const cam = useRef<HTMLInputElement>(null);

  const emit = (list: FileList | null) => {
    if (list && list.length) onFiles(Array.from(list));
  };

  return (
    <div
      data-testid="drop-zone"
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) emit(e.dataTransfer.files);
      }}
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed bg-card text-center transition-colors",
        compact ? "p-4" : "px-6 py-12 sm:py-16",
        over ? "border-primary bg-accent" : "border-border",
        disabled && "opacity-60",
      )}
    >
      {!compact && (
        <div className="flex size-12 items-center justify-center rounded-full bg-accent text-primary">
          <FileUp className="size-6" />
        </div>
      )}
      {!compact && (
        <div>
          <p className="font-medium">필기 사진이나 PDF를 끌어다 놓으세요</p>
          <p className="mt-1 text-sm text-muted-foreground">JPG · PNG · WEBP · PDF, 여러 장 한 번에 (최대 수백 쪽)</p>
        </div>
      )}
      <div className="flex flex-wrap justify-center gap-2">
        <Button type="button" onClick={() => pick.current?.click()} disabled={disabled} size={compact ? "sm" : "default"}>
          <ImagePlus /> 파일 선택
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => cam.current?.click()}
          disabled={disabled}
          size={compact ? "sm" : "default"}
          className="sm:hidden"
        >
          <Camera /> 카메라로 촬영
        </Button>
      </div>
      <input
        ref={pick}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf"
        className="hidden"
        data-testid="file-input"
        onChange={(e) => {
          emit(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={cam}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          emit(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
