"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { Download, FileCheck2, LayoutGrid, Loader2, Pencil, ScanText, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Input, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { DropZone } from "@/components/upload/drop-zone";
import { PageGrid } from "@/components/upload/page-grid";
import { OcrPanel } from "@/components/editor/ocr-panel";
import { ReviewEditor } from "@/components/editor/review-editor";
import { ExportPanel } from "@/components/editor/export-panel";
import { useDocument } from "@/hooks/use-document";
import { naturalSort, useUploader } from "@/hooks/use-uploader";
import { apiGet, apiSend } from "@/lib/api";
import type { PageInfo, SettingsInfo } from "@/types/api";

const TABS = ["pages", "ocr", "review", "export"] as const;

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const { doc, reload, error } = useDocument(id);
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [tab, setTab] = useState<string>(search.get("tab") ?? "pages");
  const [title, setTitle] = useState<string | null>(null);
  const uploader = useUploader();

  useEffect(() => {
    apiGet<SettingsInfo>("/api/settings").then(setSettings).catch(() => undefined);
  }, []);

  const switchTab = (t: string) => {
    setTab(t);
    const u = new URL(window.location.href);
    u.searchParams.set("tab", t);
    window.history.replaceState(null, "", u.toString());
  };

  if (error && !doc) return <p className="p-10 text-center text-destructive">{error}</p>;
  if (!doc) {
    return (
      <div className="flex justify-center p-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const pages = doc.pages ?? [];
  const job = doc.active_job;
  const jobRunning = !!job && ["queued", "running"].includes(job.status);

  const reorder = async (ids: string[]) => {
    try {
      await apiSend("PUT", `/api/documents/${doc.id}/pages/order`, { page_ids: ids });
      reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const rotate = async (p: PageInfo) => {
    await apiSend("PATCH", `/api/documents/${doc.id}/pages/${p.id}`, { rotate: 90 }).catch((e) => toast.error(e.message));
    reload();
  };
  const remove = async (p: PageInfo) => {
    if (!confirm(`${p.number}쪽을 삭제할까요?`)) return;
    await apiSend("DELETE", `/api/documents/${doc.id}/pages/${p.id}`).catch((e) => toast.error(e.message));
    reload();
  };
  const addFiles = async (files: File[]) => {
    const res = await uploader.upload(naturalSort(files), { documentId: doc.id, maxFileMb: settings?.limits.max_file_mb });
    if (res.failed) toast.error(`${res.failed}개 파일을 올리지 못했습니다.`);
    uploader.reset();
    reload();
  };
  const saveTitle = async () => {
    if (title === null || title.trim() === doc.title) return setTitle(null);
    await apiSend("PATCH", `/api/documents/${doc.id}`, { title }).catch((e) => toast.error(e.message));
    setTitle(null);
    reload();
  };
  const deleteDoc = async () => {
    if (!confirm("문서와 모든 결과 파일을 삭제할까요? 되돌릴 수 없습니다.")) return;
    await apiSend("DELETE", `/api/documents/${doc.id}`);
    router.push("/history");
  };

  const uncertain = pages.reduce((a, p) => a + p.uncertain, 0);

  return (
    <div className="mx-auto max-w-[1600px] px-4 py-5">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        {title === null ? (
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            {doc.title}
            <Button size="icon-sm" variant="ghost" onClick={() => setTitle(doc.title)} aria-label="제목 수정">
              <Pencil />
            </Button>
          </h1>
        ) : (
          <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} onBlur={saveTitle} onKeyDown={(e) => e.key === "Enter" && saveTitle()} className="max-w-md" />
        )}
        <Badge variant="secondary">{doc.page_count}쪽</Badge>
        <Badge variant={doc.ocr_done === doc.page_count && doc.page_count ? "success" : "secondary"}>
          OCR {doc.ocr_done}/{doc.page_count}
        </Badge>
        {jobRunning && (
          <Badge>
            <Loader2 className="animate-spin" /> 처리 중 {Math.round((job!.progress ?? 0) * 100)}%
          </Badge>
        )}
        <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground" onClick={deleteDoc}>
          <Trash2 /> 문서 삭제
        </Button>
      </div>

      <Tabs value={tab} onValueChange={switchTab}>
        <TabsList className="mb-4 w-full sm:w-auto">
          <TabsTrigger value="pages" data-testid="tab-pages">
            <LayoutGrid /> <span className="hidden sm:inline">페이지</span> 정리
          </TabsTrigger>
          <TabsTrigger value="ocr" data-testid="tab-ocr">
            <ScanText /> OCR
          </TabsTrigger>
          <TabsTrigger value="review" data-testid="tab-review">
            <FileCheck2 /> 검수{uncertain ? <Badge variant="warning" className="ml-1">{uncertain}</Badge> : null}
          </TabsTrigger>
          <TabsTrigger value="export" data-testid="tab-export">
            <Download /> 내보내기
          </TabsTrigger>
        </TabsList>

        <TabsContent value="pages" className="space-y-4">
          <DropZone onFiles={addFiles} disabled={uploader.busy} compact />
          {uploader.busy && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> 업로드 중 {uploader.items.filter((i) => i.status === "done").length}/{uploader.items.length}
            </p>
          )}
          {pages.length ? (
            <>
              <p className="text-sm text-muted-foreground">
                끌어서 순서를 바꾸세요 (휴대폰은 길게 눌러 이동). 이 순서대로 PDF가 만들어집니다.
              </p>
              <PageGrid pages={pages} onReorder={reorder} onRotate={rotate} onDelete={remove} disabled={jobRunning} />
              <div className="flex justify-end">
                <Button onClick={() => switchTab("ocr")} data-testid="go-ocr">
                  다음: OCR 설정 <ScanText />
                </Button>
              </div>
            </>
          ) : (
            <p className="p-8 text-center text-muted-foreground">페이지가 없습니다. 파일을 추가하세요.</p>
          )}
        </TabsContent>

        <TabsContent value="ocr">
          <OcrPanel doc={doc} settings={settings} onChanged={reload} />
          {job?.status === "completed" && (
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="outline" onClick={() => switchTab("review")}>
                결과 검수
              </Button>
              <Button onClick={() => switchTab("export")}>PDF 만들기</Button>
            </div>
          )}
        </TabsContent>

        <TabsContent value="review">{tab === "review" && <ReviewEditor doc={doc} onChanged={reload} />}</TabsContent>

        <TabsContent value="export">
          <ExportPanel doc={doc} onChanged={reload} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
