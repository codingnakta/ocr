"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Switch, Textarea } from "@/components/ui/primitives";
import { apiGet, apiUpload } from "@/lib/api";
import type { SettingsInfo } from "@/types/api";
import { formatUsd } from "@/lib/utils";

interface EvalResult {
  engine: string;
  model?: string | null;
  text?: string;
  seconds?: number;
  api_calls?: number;
  cost_usd?: number;
  lines?: number;
  uncertain_lines?: number;
  unaligned_lines?: number;
  pdf?: { position_ok_ratio: number | null; extract_similarity_min: number | null };
  accuracy?: { cer: number; cer_with_spaces: number; wer: number; missed_lines: string[]; pdf_text_cer: number };
  warnings?: string[];
  error?: string;
}

interface EvalResponse {
  ground_truth_provided: boolean;
  note: string | null;
  results: EvalResult[];
  source?: { kind: "image" | "pdf"; page: number; pages: number };
}

const isPdf = (f: File) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);

const NAMES: Record<string, string> = { local: "로컬 (RapidOCR)", ai: "AI (Claude)", hybrid: "하이브리드" };

export default function EvaluatePage() {
  const [file, setFile] = useState<File | null>(null);
  const [page, setPage] = useState(1);
  const [gt, setGt] = useState("");
  const [engines, setEngines] = useState<Record<string, boolean>>({ local: true, ai: false, hybrid: true });
  const [choices, setChoices] = useState<{ id: string; name: string; per_page_usd: number }[]>([]);
  const [models, setModels] = useState<Record<string, boolean>>({});
  useEffect(() => {
    apiGet<SettingsInfo>("/api/settings")
      .then((st) => {
        setChoices(st.claude.model_choices ?? []);
        setModels({ [st.claude.model]: true });
      })
      .catch(() => undefined);
  }, []);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<EvalResponse | null>(null);

  const run = async () => {
    if (!file) return;
    setBusy(true);
    setRes(null);
    const fd = new FormData();
    fd.append("image", file);
    fd.append("ground_truth", gt);
    fd.append("engines", Object.keys(engines).filter((k) => engines[k]).join(","));
    fd.append("models", Object.keys(models).filter((k) => models[k]).join(","));
    if (isPdf(file)) fd.append("page", String(page));
    try {
      setRes(await apiUpload("/api/evaluations", fd));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 py-6">
      <div>
        <h1 className="text-xl font-semibold">OCR 정확도 평가</h1>
        <p className="text-sm text-muted-foreground">
          같은 이미지(또는 PDF의 한 쪽)를 로컬·AI·하이브리드로 처리해 비교합니다. 정답 텍스트를 넣어야 CER/WER을 계산합니다 (정답
          없이는 정확도를 표시하지 않습니다).
        </p>
      </div>
      <Card>
        <CardContent className="grid gap-4 pt-5 md:grid-cols-2">
          <div className="space-y-3">
            <div>
              <Label>이미지 또는 PDF</Label>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf"
                className="mt-1 block w-full text-sm"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setPage(1);
                }}
              />
              <p className="mt-1 text-xs text-muted-foreground">JPG · PNG · WEBP · PDF. PDF는 한 번에 한 쪽만 평가합니다.</p>
            </div>
            {file && isPdf(file) && (
              <div>
                <Label htmlFor="eval-page">평가할 PDF 쪽 번호</Label>
                <Input
                  id="eval-page"
                  type="number"
                  min={1}
                  step={1}
                  value={page}
                  onChange={(e) => setPage(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                  className="mt-1 w-32"
                />
              </div>
            )}
            <div className="flex flex-wrap gap-4">
              {Object.keys(NAMES).map((k) => (
                <label key={k} className="flex items-center gap-2 text-sm">
                  <Switch checked={engines[k]} onCheckedChange={(v) => setEngines({ ...engines, [k]: v })} /> {NAMES[k]}
                </label>
              ))}
            </div>
            {choices.length > 0 && (
              <div>
                <p className="mb-1 text-sm font-medium">비교할 Claude 모델 (AI·하이브리드)</p>
                <div className="flex flex-wrap gap-4">
                  {choices.map((m) => (
                    <label key={m.id} className="flex items-center gap-2 text-sm">
                      <Switch checked={!!models[m.id]} onCheckedChange={(v) => setModels({ ...models, [m.id]: v })} />
                      {m.name.replace("Claude ", "")} <span className="text-xs text-muted-foreground">≈{formatUsd(m.per_page_usd)}/쪽</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <p className="text-xs text-muted-foreground">AI·하이브리드 평가는 선택한 모델마다 API 비용이 발생합니다.</p>
            <Button onClick={run} disabled={!file || busy}>
              {busy ? <Loader2 className="animate-spin" /> : <FlaskConical />} 평가 실행
            </Button>
          </div>
          <div>
            <Label>정답 텍스트 (선택, 한 줄에 한 줄씩)</Label>
            <Textarea value={gt} onChange={(e) => setGt(e.target.value)} rows={9} className="mt-1" placeholder="사람이 직접 확인한 정답을 입력하세요" />
          </div>
        </CardContent>
      </Card>
      {res && (
        <>
          {res.source?.kind === "pdf" && (
            <p className="text-sm text-muted-foreground">
              PDF {res.source.pages}쪽 중 {res.source.page}쪽을 평가했습니다.
            </p>
          )}
          {res.note && <p className="rounded-lg bg-warning-soft p-3 text-sm">{res.note}</p>}
          <div className="grid gap-4 lg:grid-cols-3">
            {res.results.map((r) => (
              <Card key={`${r.engine}-${r.model ?? "local"}`}>
                <CardHeader>
                  <CardTitle>{NAMES[r.engine] ?? r.engine}</CardTitle>
                  <CardDescription>{r.model ?? "로컬 (무료)"}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  {r.error ? (
                    <p className="text-destructive">{r.error}</p>
                  ) : (
                    <>
                      {r.accuracy && (
                        <div className="grid grid-cols-3 gap-2">
                          <Metric label="CER" value={`${(r.accuracy.cer * 100).toFixed(1)}%`} />
                          <Metric label="WER" value={`${(r.accuracy.wer * 100).toFixed(1)}%`} />
                          <Metric label="PDF 텍스트 CER" value={`${(r.accuracy.pdf_text_cer * 100).toFixed(1)}%`} />
                        </div>
                      )}
                      <div className="flex flex-wrap gap-1">
                        <Badge variant="secondary">{r.seconds}초</Badge>
                        <Badge variant="secondary">{formatUsd(r.cost_usd ?? 0)}</Badge>
                        <Badge variant="secondary">{r.lines}줄</Badge>
                        {!!r.uncertain_lines && <Badge variant="warning">불확실 {r.uncertain_lines}</Badge>}
                        {!!r.unaligned_lines && <Badge variant="secondary">위치 없음 {r.unaligned_lines}</Badge>}
                        {r.pdf && <Badge variant={r.pdf.position_ok_ratio === 1 ? "success" : "warning"}>PDF 위치 {Math.round((r.pdf.position_ok_ratio ?? 0) * 100)}%</Badge>}
                      </div>
                      {r.accuracy?.missed_lines.length ? (
                        <p className="text-xs text-destructive">누락 의심: {r.accuracy.missed_lines.join(" / ")}</p>
                      ) : null}
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs leading-relaxed">{r.text}</pre>
                      {r.warnings?.map((w) => (
                        <p key={w} className="text-xs text-muted-foreground">
                          • {w}
                        </p>
                      ))}
                    </>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted px-2 py-1.5 text-center">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}
