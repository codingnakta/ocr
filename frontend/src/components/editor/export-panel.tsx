"use client";
import { useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { CheckCircle2, Download, Eye, FileJson, FileText, FileType2, Loader2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { apiSend } from "@/lib/api";
import { cn, formatBytes } from "@/lib/utils";
import type { DocumentInfo, ExportInfo } from "@/types/api";

const FORMATS = [
  { id: "pdf", title: "검색 가능한 PDF", desc: "원본 이미지 + 보이지 않는 텍스트 레이어", icon: FileType2 },
  { id: "txt", title: "TXT", desc: "페이지별 일반 텍스트", icon: FileText },
  { id: "md", title: "Markdown", desc: "제목·목록 구조 유지", icon: FileText },
  { id: "json", title: "JSON", desc: "좌표·원본 OCR·수정본 포함", icon: FileJson },
] as const;

export function ExportPanel({ doc, onChanged }: { doc: DocumentInfo; onChanged: () => void }) {
  const [format, setFormat] = useState<string>("pdf");
  const [sizeMode, setSizeMode] = useState(doc.page_size_mode);
  const [imageSource, setImageSource] = useState(doc.pdf_image_source);
  const [busy, setBusy] = useState(false);
  const notDone = (doc.pages ?? []).filter((p) => p.ocr_status !== "done" && p.ocr_status !== "skipped").length;
  const uncertain = (doc.pages ?? []).reduce((a, p) => a + p.uncertain, 0);
  const unaligned = (doc.pages ?? []).reduce((a, p) => a + p.unaligned, 0);

  const create = async () => {
    setBusy(true);
    try {
      await apiSend("POST", `/api/documents/${doc.id}/export`, {
        format,
        size_mode: format === "pdf" ? sizeMode : undefined,
        image_source: format === "pdf" ? imageSource : undefined,
      });
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>내보내기</CardTitle>
          <CardDescription>기본은 검색 가능한 PDF입니다. 수정한 텍스트가 반영됩니다.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2">
            {FORMATS.map((f) => (
              <button
                key={f.id}
                onClick={() => setFormat(f.id)}
                className={cn("rounded-lg border p-3 text-left", format === f.id ? "border-primary bg-accent ring-1 ring-primary" : "hover:bg-muted")}
                data-testid={`format-${f.id}`}
              >
                <div className="flex items-center gap-2 text-sm font-medium">
                  <f.icon className="size-4 text-primary" /> {f.title}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{f.desc}</p>
              </button>
            ))}
          </div>
          {format === "pdf" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <Choice
                label="페이지 크기"
                value={sizeMode}
                onChange={(v) => setSizeMode(v as "original" | "a4")}
                options={[
                  ["original", "원본 비율 (기본)"],
                  ["a4", "A4에 맞춤"],
                ]}
              />
              <Choice
                label="PDF 이미지"
                value={imageSource}
                onChange={(v) => setImageSource(v as "original" | "enhanced")}
                options={[
                  ["original", "원본 그대로 (기본)"],
                  ["enhanced", "보정본 (원근·조명 보정)"],
                ]}
              />
            </div>
          )}
          {(notDone > 0 || uncertain > 0 || unaligned > 0) && (
            <ul className="space-y-1 rounded-lg bg-muted p-3 text-xs text-muted-foreground">
              {notDone > 0 && <li>• OCR이 안 된 페이지 {notDone}쪽은 텍스트 없이 이미지만 들어갑니다.</li>}
              {uncertain > 0 && <li>• 확인이 필요한 줄 {uncertain}개가 남아 있습니다 (검수 탭).</li>}
              {unaligned > 0 && <li>• 위치를 확인하지 못한 줄 {unaligned}개는 PDF 텍스트 레이어에서 제외되고 TXT/MD/JSON에는 포함됩니다.</li>}
            </ul>
          )}
          <Button className="w-full" size="lg" onClick={create} disabled={busy} data-testid="create-export">
            {busy ? <Loader2 className="animate-spin" /> : <Download />} {FORMATS.find((f) => f.id === format)?.title} 만들기
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>생성된 파일</CardTitle>
          <CardDescription>다운로드 링크는 로그인한 본인만 사용할 수 있습니다.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {!doc.exports?.length && <p className="text-sm text-muted-foreground">아직 만든 파일이 없습니다.</p>}
          {doc.exports?.map((e) => <ExportRow key={e.id} e={e} docId={doc.id} />)}
        </CardContent>
      </Card>
    </div>
  );
}

function ExportRow({ e, docId }: { e: ExportInfo; docId: string }) {
  const r = e.report;
  return (
    <div className="rounded-lg border p-3" data-testid="export-row">
      <div className="flex items-center gap-2">
        {e.status === "done" ? (
          <CheckCircle2 className="size-4 text-success" />
        ) : e.status === "failed" ? (
          <XCircle className="size-4 text-destructive" />
        ) : (
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        )}
        <span className="text-sm font-medium uppercase">{e.format}</span>
        <span className="text-xs text-muted-foreground">
          {new Date(e.created_at).toLocaleString("ko-KR")} · {formatBytes(e.size_bytes)}
        </span>
        <div className="ml-auto flex gap-1">
          {e.status === "done" && e.format === "pdf" && (
            <Button size="sm" variant="outline" asChild>
              <Link href={`/doc/preview/?id=${docId}&export=${e.id}`}>
                <Eye /> 미리보기
              </Link>
            </Button>
          )}
          {e.download_url && (
            <Button size="sm" asChild>
              <a href={e.download_url} data-testid="download-link">
                <Download /> 다운로드
              </a>
            </Button>
          )}
        </div>
      </div>
      {e.error && <p className="mt-1 text-xs text-destructive">{e.error}</p>}
      {e.format === "pdf" && e.status === "done" && r.items !== undefined && (
        <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
          <Badge variant={r.position_ok_ratio === 1 ? "success" : "warning"}>
            텍스트 위치 검증 {r.position_ok}/{r.items}
          </Badge>
          <Badge variant={r.extract_similarity_min === 1 ? "success" : "warning"}>
            유니코드 추출 {r.extract_similarity_min != null ? `${Math.round(r.extract_similarity_min * 100)}%` : "-"}
          </Badge>
          {!!r.unaligned_lines_excluded && <Badge variant="secondary">위치 없는 줄 {r.unaligned_lines_excluded}개 제외</Badge>}
        </div>
      )}
    </div>
  );
}

function Choice({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <div>
      <p className="mb-1.5 text-sm font-medium">{label}</p>
      <div className="flex flex-col gap-1">
        {options.map(([v, l]) => (
          <label key={v} className="flex cursor-pointer items-center gap-2 text-sm">
            <input type="radio" checked={value === v} onChange={() => onChange(v)} className="accent-[var(--primary)]" />
            {l}
          </label>
        ))}
      </div>
    </div>
  );
}
