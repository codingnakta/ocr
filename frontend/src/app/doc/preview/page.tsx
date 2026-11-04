"use client";
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PdfPreview } from "@/components/viewer/pdf-preview";

export default function PreviewPageRoute() {
  return (
    <Suspense>
      <PreviewPage />
    </Suspense>
  );
}

function PreviewPage() {
  const search = useSearchParams();
  const id = search.get("id") ?? "";
  const exportId = search.get("export");
  const url = exportId ? `/api/exports/${exportId}/download` : `/api/documents/${id}/download?format=pdf`;
  return (
    <div className="mx-auto max-w-5xl px-4 py-5">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/doc/?id=${id}&tab=export`}>
            <ArrowLeft /> 문서로
          </Link>
        </Button>
        <h1 className="text-lg font-semibold">PDF 미리보기 (PDF.js)</h1>
        <Button size="sm" className="ml-auto" asChild>
          <a href={url}>
            <Download /> 다운로드
          </a>
        </Button>
      </div>
      <p className="mb-3 text-sm text-muted-foreground">
        손글씨 위를 마우스로 드래그해 보세요. 선택된 글자가 위에 표시되고 복사할 수 있습니다. Chrome·Acrobat 등 다른 뷰어에서의
        동작은 다운로드 후 확인하세요.
      </p>
      <PdfPreview url={url} />
    </div>
  );
}
