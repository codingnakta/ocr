"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, Loader2, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Card } from "@/components/ui/primitives";
import { apiGet } from "@/lib/api";
import type { DocumentInfo } from "@/types/api";

export default function HistoryPage() {
  const [docs, setDocs] = useState<DocumentInfo[] | null>(null);
  useEffect(() => {
    apiGet<{ documents: DocumentInfo[] }>("/api/documents").then((r) => setDocs(r.documents)).catch(() => setDocs([]));
  }, []);
  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-center">
        <h1 className="text-xl font-semibold">작업 기록</h1>
        <Button asChild size="sm" className="ml-auto">
          <Link href="/">
            <Plus /> 새 작업
          </Link>
        </Button>
      </div>
      {docs === null ? (
        <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />
      ) : docs.length === 0 ? (
        <p className="p-10 text-center text-muted-foreground">아직 문서가 없습니다.</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {docs.map((d) => (
            <Link key={d.id} href={`/doc/?id=${d.id}`}>
              <Card className="flex gap-3 p-3 transition-colors hover:border-primary/50">
                <div className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted">
                  {d.thumb_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={d.thumb_url} alt="" className="size-full object-cover" />
                  ) : (
                    <FileText className="size-6 text-muted-foreground" />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="truncate font-medium">{d.title}</p>
                  <p className="text-xs text-muted-foreground">{new Date(d.updated_at).toLocaleString("ko-KR")}</p>
                  <div className="mt-1.5 flex gap-1">
                    <Badge variant="secondary">{d.page_count}쪽</Badge>
                    <Badge variant={d.ocr_done === d.page_count && d.page_count ? "success" : "secondary"}>
                      OCR {d.ocr_done}/{d.page_count}
                    </Badge>
                    {d.ocr_failed > 0 && <Badge variant="destructive">실패 {d.ocr_failed}</Badge>}
                  </div>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
