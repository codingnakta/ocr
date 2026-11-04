"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CheckCircle2, FileSearch, Loader2, ShieldCheck, Sparkles, XCircle } from "lucide-react";
import { DropZone } from "@/components/upload/drop-zone";
import { Card, CardContent, Input, Progress } from "@/components/ui/primitives";
import { naturalSort, useUploader } from "@/hooks/use-uploader";
import { apiGet } from "@/lib/api";
import { formatBytes } from "@/lib/utils";
import type { SettingsInfo } from "@/types/api";

export default function HomePage() {
  const router = useRouter();
  const { items, busy, upload } = useUploader();
  const [title, setTitle] = useState("");
  const [settings, setSettings] = useState<SettingsInfo | null>(null);

  useEffect(() => {
    apiGet<SettingsInfo>("/api/settings").then(setSettings).catch(() => undefined);
  }, []);

  const onFiles = async (files: File[]) => {
    const sorted = naturalSort(files);
    try {
      const res = await upload(sorted, { title: title.trim() || undefined, maxFileMb: settings?.limits.max_file_mb });
      if (res.rejected) toast.warning(`지원하지 않는 형식 ${res.rejected}개는 제외했습니다.`);
      if (res.failed) toast.error(`${res.failed}개 파일을 올리지 못했습니다. 문서 화면에서 다시 추가할 수 있습니다.`);
      router.push(`/documents/${res.documentId}`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const done = items.filter((i) => i.status === "done").length;
  const total = items.length;
  const overall = total ? items.reduce((a, i) => a + (i.status === "done" ? 1 : i.progress), 0) / total : 0;

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:py-12">
      <div className="mb-6 text-center">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">필기를 검색 가능한 PDF로</h1>
        <p className="mt-2 text-muted-foreground">
          사진 원본은 그대로, 글자는 드래그·복사·검색되도록. 한글 손글씨에 맞춘 AI OCR.
        </p>
      </div>

      <div className="mb-3">
        <Input
          placeholder="문서 제목 (선택) — 예: 디지털논리 3주차 필기"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
          maxLength={200}
        />
      </div>
      <DropZone onFiles={onFiles} disabled={busy} />

      {total > 0 && (
        <Card className="mt-4">
          <CardContent className="pt-5">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 font-medium">
                {busy && <Loader2 className="size-4 animate-spin" />} 업로드 {done}/{total}
              </span>
              <span className="text-muted-foreground">{Math.round(overall * 100)}%</span>
            </div>
            <Progress value={overall * 100} />
            <ul className="mt-3 max-h-56 space-y-1 overflow-auto text-sm">
              {items.map((it) => (
                <li key={it.key} className="flex items-center gap-2">
                  {it.status === "done" ? (
                    <CheckCircle2 className="size-4 text-success" />
                  ) : it.status === "error" ? (
                    <XCircle className="size-4 text-destructive" />
                  ) : (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  )}
                  <span className="truncate">{it.file.name}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                    {it.error ?? formatBytes(it.file.size)}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="mt-8 grid gap-3 sm:grid-cols-3">
        <Feature icon={Sparkles} title="AI + 로컬 하이브리드" text="로컬 엔진이 글자 위치를 찾고, Claude가 손글씨를 읽고, 의심 줄만 다시 확인합니다." />
        <Feature icon={FileSearch} title="검색 가능한 PDF" text="원본 이미지 위에 보이지 않는 텍스트를 정확한 위치에 배치합니다." />
        <Feature icon={ShieldCheck} title="비용·데이터 안내" text="시작 전 예상 비용과 상한을 확인합니다. AI 모드는 이미지를 Anthropic으로 전송합니다." />
      </div>
    </div>
  );
}

function Feature({ icon: Icon, title, text }: { icon: typeof Sparkles; title: string; text: string }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <Icon className="mb-2 size-5 text-primary" />
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{text}</p>
    </div>
  );
}
