"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ChevronLeft, ChevronRight, Eye, FileImage, ListOrdered, Loader2, ScanText, Text } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Dialog, DialogContent, Switch, Tabs, TabsContent, TabsList, TabsTrigger, Textarea } from "@/components/ui/primitives";
import { PageViewer } from "@/components/viewer/page-viewer";
import { RegionList } from "@/components/editor/region-list";
import { apiGet, apiSend } from "@/lib/api";
import { useMediaQuery } from "@/hooks/use-media-query";
import { cn } from "@/lib/utils";
import type { DocumentInfo, PageInfo, PageOcr, Region } from "@/types/api";

type RereadState = {
  rect?: [number, number, number, number];
  region?: Region;
  loading: boolean;
  text?: string;
  uncertain?: boolean;
  engine?: string;
};

export function ReviewEditor({ doc, onChanged }: { doc: DocumentInfo; onChanged: () => void }) {
  const pages = doc.pages ?? [];
  const [pageId, setPageId] = useState<string | null>(pages[0]?.id ?? null);
  const [data, setData] = useState<PageOcr | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [processed, setProcessed] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [fullText, setFullText] = useState(false);
  const [reread, setReread] = useState<RereadState | null>(null);
  const [mobileTab, setMobileTab] = useState("text");
  const desktop = useMediaQuery("(min-width: 1024px)");
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pending = useRef<Record<string, string>>({});

  const page = pages.find((p) => p.id === pageId) ?? pages[0];

  const load = useCallback(async (id: string) => {
    const d = await apiGet<PageOcr>(`/api/documents/${doc.id}/pages/${id}/ocr`);
    setData(d);
  }, [doc.id]);

  useEffect(() => {
    if (!pageId && pages[0]) setPageId(pages[0].id);
  }, [pages, pageId]);

  useEffect(() => {
    if (!page) return;
    setSelected(null);
    setData(null);
    load(page.id).catch((e) => toast.error(e.message));
  }, [page?.id, page?.ocr_status, load]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- autosave: debounce per region, flush on page change/unmount
  const flush = useCallback(
    async (regionId: string) => {
      const text = pending.current[regionId];
      if (text === undefined || !page) return;
      delete pending.current[regionId];
      try {
        const res = await apiSend<{ regions: Region[] }>("PATCH", `/api/documents/${doc.id}/pages/${page.id}/ocr`, {
          regions: [{ id: regionId, text_user: text }],
        });
        setData((d) => (d ? { ...d, regions: res.regions } : d));
        setDrafts((dr) => {
          const n = { ...dr };
          if (pending.current[regionId] === undefined) delete n[regionId];
          return n;
        });
      } catch (e) {
        toast.error(`저장 실패: ${(e as Error).message}`);
      }
    },
    [doc.id, page],
  );

  useEffect(() => {
    const t = timers.current;
    return () => {
      Object.keys(t).forEach((k) => {
        clearTimeout(t[k]);
        void flush(k);
      });
    };
  }, [flush]);

  const onEdit = (r: Region, text: string) => {
    setDrafts((d) => ({ ...d, [r.id]: text }));
    pending.current[r.id] = text;
    clearTimeout(timers.current[r.id]);
    timers.current[r.id] = setTimeout(() => flush(r.id), 700);
  };

  const patch = async (body: unknown) => {
    if (!page) return;
    const res = await apiSend<{ regions: Region[] }>("PATCH", `/api/documents/${doc.id}/pages/${page.id}/ocr`, body);
    setData((d) => (d ? { ...d, regions: res.regions } : d));
    onChanged();
  };

  const runReread = async (engine: "claude" | "local") => {
    if (!reread || !page) return;
    setReread({ ...reread, loading: true });
    try {
      const res = await apiSend<{ text: string; uncertain: boolean; engine: string; created: Region | null }>(
        "POST",
        `/api/documents/${doc.id}/pages/${page.id}/reocr`,
        reread.region ? { region_id: reread.region.id, engine } : { rect: reread.rect, engine, create_region: false },
      );
      setReread({ ...reread, loading: false, text: res.text, uncertain: res.uncertain, engine: res.engine });
    } catch (e) {
      toast.error((e as Error).message);
      setReread({ ...reread, loading: false });
    }
  };

  const applyReread = async () => {
    if (!reread?.text && reread?.text !== "") return;
    if (reread.region) {
      onEdit(reread.region, reread.text!);
    } else if (reread.rect) {
      const [x0, y0, x1, y1] = reread.rect;
      await patch({ create: [{ polygon: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text_user: reread.text }] });
    }
    setReread(null);
    toast.success("적용했습니다.");
  };

  const idx = pages.findIndex((p) => p.id === page?.id);
  const go = (d: number) => {
    const p = pages[idx + d];
    if (p) setPageId(p.id);
  };

  if (!page) return <p className="p-8 text-center text-muted-foreground">페이지가 없습니다.</p>;
  const regions = data?.regions ?? [];
  const imgSrc = processed && page.processed_url ? page.processed_url : page.image_url;

  const viewer = (
    <PageViewer
      src={imgSrc}
      regions={processed ? [] : regions}
      selectedId={selected}
      onSelect={(id) => {
        setSelected(id);
        setMobileTab("text");
      }}
      onDrawn={(rect) => setReread({ rect, loading: false })}
    />
  );

  const textPane = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b bg-card px-3 py-2 text-sm">
        <span className="font-medium">인식 결과</span>
        {page.ocr_engine && <Badge variant="secondary">{page.ocr_engine}</Badge>}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          원본 OCR 비교 <Switch checked={showRaw} onCheckedChange={setShowRaw} />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Text className="size-3.5" /> 전체 글
          <Switch checked={fullText} onCheckedChange={setFullText} />
        </label>
      </div>
      {page.warnings.length > 0 && (
        <div className="border-b bg-warning-soft/50 px-3 py-2 text-xs text-[oklch(0.45_0.1_70)]">
          {page.warnings.map((w) => (
            <p key={w}>• {w}</p>
          ))}
        </div>
      )}
      {page.ocr_status === "failed" && <p className="px-3 py-2 text-sm text-destructive">OCR 실패: {page.ocr_error}</p>}
      <div className="min-h-0 flex-1 overflow-auto">
        {!data ? (
          <div className="flex justify-center p-8">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : fullText ? (
          <Textarea
            readOnly
            className="m-3 h-[calc(100%-1.5rem)] w-[calc(100%-1.5rem)] resize-none text-[15px] leading-relaxed"
            value={regions.map((r) => drafts[r.id] ?? r.text).filter(Boolean).join("\n")}
          />
        ) : (
          <RegionList
            regions={regions}
            drafts={drafts}
            selectedId={selected}
            onSelect={setSelected}
            onEdit={onEdit}
            onRevert={(r) => patch({ regions: [{ id: r.id, revert: true }] })}
            onReviewed={(r, v) => patch({ regions: [{ id: r.id, reviewed: v }] })}
            onReread={(r) => setReread({ region: r, loading: false })}
            showRaw={showRaw}
          />
        )}
      </div>
    </div>
  );

  const thumbs = (
    <div className="space-y-2 p-2" data-testid="thumb-list">
      {pages.map((p) => (
        <ThumbButton key={p.id} p={p} active={p.id === page.id} onClick={() => { setPageId(p.id); setMobileTab("image"); }} />
      ))}
    </div>
  );

  const toolbar = (
    <div className="flex items-center gap-2 border-b bg-card px-3 py-2">
      <Button size="icon-sm" variant="outline" onClick={() => go(-1)} disabled={idx <= 0} aria-label="이전 페이지">
        <ChevronLeft />
      </Button>
      <span className="text-sm tabular-nums">
        {idx + 1} / {pages.length}쪽
      </span>
      <Button size="icon-sm" variant="outline" onClick={() => go(1)} disabled={idx >= pages.length - 1} aria-label="다음 페이지">
        <ChevronRight />
      </Button>
      <span className="hidden truncate text-sm text-muted-foreground sm:inline">{page.label}</span>
      {page.processed_url && (
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <Eye className="size-3.5" /> 전처리 이미지 <Switch checked={processed} onCheckedChange={setProcessed} />
        </label>
      )}
    </div>
  );

  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      {toolbar}
      {/* desktop: 3 columns; phones/tablets: tabs. Only one layout is rendered. */}
      {desktop ? (
      <div className="grid h-[calc(100dvh-14rem)] min-h-[520px] grid-cols-[150px_minmax(0,1fr)_minmax(360px,440px)]">
        <div className="overflow-auto border-r bg-muted/40">{thumbs}</div>
        <div className="min-h-0 border-r">{viewer}</div>
        <div className="min-h-0">{textPane}</div>
      </div>
      ) : (
      <div>
        <Tabs value={mobileTab} onValueChange={setMobileTab}>
          <div className="border-b p-2">
            <TabsList className="w-full">
              <TabsTrigger value="image">
                <FileImage /> 원본
              </TabsTrigger>
              <TabsTrigger value="text">
                <ScanText /> 인식 결과
              </TabsTrigger>
              <TabsTrigger value="pages">
                <ListOrdered /> 페이지 목록
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="image" className="h-[70dvh]">{viewer}</TabsContent>
          <TabsContent value="text" className="h-[70dvh]">{textPane}</TabsContent>
          <TabsContent value="pages" className="max-h-[70dvh] overflow-auto">
            <div className="grid grid-cols-3 gap-2 p-2 sm:grid-cols-4">
              {pages.map((p) => (
                <ThumbButton key={p.id} p={p} active={p.id === page.id} onClick={() => { setPageId(p.id); setMobileTab("image"); }} />
              ))}
            </div>
          </TabsContent>
        </Tabs>
      </div>
      )}

      <Dialog open={!!reread} onOpenChange={(o) => !o && setReread(null)}>
        {reread && (
          <DialogContent
            title={reread.region ? "이 줄 다시 인식" : "선택 영역 인식"}
            description="결과는 '적용'을 눌러야 반영됩니다. Claude 재인식은 API 비용이 발생합니다(약 $0.01)."
          >
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => runReread("claude")} disabled={reread.loading} data-testid="reread-claude">
                {reread.loading ? <Loader2 className="animate-spin" /> : <ScanText />} Claude로 인식
              </Button>
              <Button variant="outline" onClick={() => runReread("local")} disabled={reread.loading}>
                로컬 OCR로 인식 (무료)
              </Button>
            </div>
            {reread.text !== undefined && (
              <div className="space-y-2">
                <Textarea value={reread.text} onChange={(e) => setReread({ ...reread, text: e.target.value })} rows={3} />
                {reread.uncertain && <p className="text-xs text-[oklch(0.5_0.12_70)]">읽기 어려운 글자가 있다고 표시되었습니다.</p>}
                <Button onClick={applyReread} className="w-full">
                  {reread.region ? "이 줄에 적용" : "새 영역으로 추가"}
                </Button>
              </div>
            )}
          </DialogContent>
        )}
      </Dialog>
    </div>
  );
}

function ThumbButton({ p, active, onClick }: { p: PageInfo; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn("relative block w-full overflow-hidden rounded-md border bg-card text-left", active ? "ring-2 ring-primary" : "hover:border-primary/50")}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={p.thumb_url} alt="" className="aspect-[3/4] w-full object-contain p-0.5" loading="lazy" />
      <span className="absolute left-1 top-1 rounded bg-foreground/70 px-1 text-[10px] text-background">{p.number}</span>
      {p.uncertain > 0 && <span className="absolute right-1 top-1 size-2 rounded-full bg-warning" title="확인 필요" />}
      {p.ocr_status === "failed" && <span className="absolute right-1 top-1 size-2 rounded-full bg-destructive" />}
    </button>
  );
}
