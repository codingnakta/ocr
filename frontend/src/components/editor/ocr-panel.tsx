"use client";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Cloud, Cpu, Layers, Loader2, Pause, Play, RefreshCcw, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, Progress, Switch } from "@/components/ui/primitives";
import { apiSend } from "@/lib/api";
import { cn, formatTokens, formatUsd } from "@/lib/utils";
import type { DocumentInfo, Estimate, JobProgress, OcrMode, SettingsInfo } from "@/types/api";

const MODES: { id: OcrMode; title: string; desc: string; icon: typeof Cloud; badge?: string }[] = [
  { id: "hybrid", title: "하이브리드", desc: "로컬 엔진이 위치를 찾고 Claude가 읽은 뒤, 의심되는 줄만 잘라서 다시 확인합니다.", icon: Layers, badge: "추천" },
  { id: "ai", title: "AI OCR", desc: "Claude가 페이지를 한 번 읽습니다. 하이브리드보다 약간 저렴합니다.", icon: Cloud },
  { id: "local", title: "무료 OCR", desc: "RapidOCR로 내 서버에서만 처리합니다. 비용 없음, 손글씨 정확도는 낮습니다.", icon: Cpu },
];

const STATUS_LABEL: Record<string, string> = {
  queued: "대기 중",
  running: "처리 중",
  paused: "일시 중지",
  cancelled: "취소됨",
  completed: "완료",
  completed_with_errors: "일부 실패",
  failed: "실패",
};

export function OcrPanel({
  doc,
  settings,
  onChanged,
}: {
  doc: DocumentInfo;
  settings: SettingsInfo | null;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<OcrMode>("hybrid");
  const [rerun, setRerun] = useState(false);
  const [limit, setLimit] = useState<string>("");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [busy, setBusy] = useState(false);
  const job = doc.active_job;
  const claudeOk = settings?.claude.configured ?? true;

  useEffect(() => {
    if (settings && limit === "") setLimit(String(settings.limits.default_job_cost_limit_usd));
  }, [settings, limit]);

  useEffect(() => {
    let alive = true;
    apiSend<Estimate>("POST", `/api/documents/${doc.id}/ocr/estimate`, { mode, rerun })
      .then((e) => alive && setEstimate(e))
      .catch(() => alive && setEstimate(null));
    return () => {
      alive = false;
    };
  }, [doc.id, mode, rerun, doc.page_count, doc.ocr_done]);

  const start = async () => {
    setBusy(true);
    try {
      await apiSend("POST", `/api/documents/${doc.id}/ocr`, {
        mode,
        rerun,
        cost_limit_usd: mode === "local" ? null : Number(limit) || 0,
      });
      toast.success("OCR을 시작했습니다. 이 창을 닫아도 서버에서 계속 처리됩니다.");
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const tooHigh = estimate && mode !== "local" && Number(limit) > 0 && estimate.estimated_cost_usd > Number(limit);

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_380px]">
      <div className="space-y-4">
        {job && <JobCard job={job} onChanged={onChanged} />}
        <Card>
          <CardHeader>
            <CardTitle>OCR 모드</CardTitle>
            <CardDescription>문서 전체에 같은 모드를 적용합니다.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-3">
            {MODES.map((m) => {
              const disabled = m.id !== "local" && !claudeOk;
              return (
                <button
                  key={m.id}
                  type="button"
                  disabled={disabled}
                  onClick={() => setMode(m.id)}
                  className={cn(
                    "rounded-lg border p-3 text-left transition-colors disabled:opacity-50",
                    mode === m.id ? "border-primary bg-accent ring-1 ring-primary" : "hover:bg-muted",
                  )}
                  data-testid={`mode-${m.id}`}
                >
                  <div className="flex items-center gap-2 font-medium">
                    <m.icon className="size-4 text-primary" /> {m.title}
                    {m.badge && <Badge className="ml-auto">{m.badge}</Badge>}
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{m.desc}</p>
                </button>
              );
            })}
          </CardContent>
        </Card>
        {mode !== "local" && (
          <div className="flex gap-2 rounded-lg border border-[oklch(0.85_0.08_80)] bg-warning-soft p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[oklch(0.55_0.13_70)]" />
            <p>
              AI·하이브리드 모드는 페이지 이미지를 <b>Anthropic(Claude API)</b>으로 전송해 처리합니다. 민감한 문서라면
              무료 OCR(로컬) 모드를 사용하세요. 인터넷 연결이 필요합니다.
            </p>
          </div>
        )}
        {settings?.local_ocr && settings.local_ocr.supports_hangul === false && mode !== "ai" && (
          <p className="text-xs text-muted-foreground">
            참고: 현재 서버의 로컬 인식 모델은 한글을 지원하지 않습니다({settings.local_ocr.rec_model}). 하이브리드 모드에서는
            위치 검출에만 쓰이고, 한글 비교 검증은 생략됩니다.
          </p>
        )}
      </div>

      <Card className="h-fit">
        <CardHeader>
          <CardTitle>예상 비용</CardTitle>
          <CardDescription>실제 비용은 처리 후 API 사용량으로 계산됩니다.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg bg-muted p-4">
            <div className="text-xs text-muted-foreground">
              {estimate?.pages ?? 0}쪽 · {mode === "local" ? "로컬 처리" : estimate?.model}
            </div>
            <div className="mt-1 text-3xl font-bold tabular-nums" data-testid="estimate">
              {mode === "local" ? "$0" : `≈ ${formatUsd(estimate?.estimated_cost_usd ?? 0, 2)}`}
            </div>
            {mode !== "local" && estimate && (
              <div className="mt-1 text-xs text-muted-foreground">
                쪽당 약 {formatUsd(estimate.per_page_usd)} · 추정치
              </div>
            )}
          </div>
          {mode !== "local" && (
            <div className="space-y-1.5">
              <Label htmlFor="limit">비용 상한 (USD)</Label>
              <Input id="limit" type="number" min={0} step={0.5} value={limit} onChange={(e) => setLimit(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                상한에 닿으면 남은 페이지는 보류되고 작업이 일시 중지됩니다. 하루 상한 {formatUsd(settings?.limits.daily_cost_limit_usd ?? 0, 0)}
              </p>
              {tooHigh && <p className="text-xs text-destructive">예상 비용이 상한보다 높습니다. 일부 페이지만 처리될 수 있습니다.</p>}
            </div>
          )}
          <label className="flex items-center justify-between gap-3 text-sm">
            <span>
              완료된 페이지도 다시 처리
              <span className="block text-xs text-muted-foreground">수정한 텍스트는 그대로 보존됩니다</span>
            </span>
            <Switch checked={rerun} onCheckedChange={setRerun} />
          </label>
          <Button
            className="w-full"
            size="lg"
            onClick={start}
            disabled={busy || !!(job && ["queued", "running", "paused"].includes(job.status)) || !estimate?.pages}
            data-testid="start-ocr"
          >
            {busy ? <Loader2 className="animate-spin" /> : <Play />} OCR 시작
          </Button>
          {estimate && !estimate.pages && <p className="text-center text-xs text-muted-foreground">OCR할 페이지가 없습니다.</p>}
          {estimate?.basis && mode !== "local" && <p className="text-[11px] leading-relaxed text-muted-foreground">{estimate.basis}</p>}
        </CardContent>
      </Card>
    </div>
  );
}

function JobCard({ job, onChanged }: { job: JobProgress; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    try {
      await apiSend("POST", `/api/jobs/${job.id}/${path}`, body);
      onChanged();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const pct = Math.round(job.progress * 100);
  const running = job.status === "running" || job.status === "queued";
  return (
    <Card data-testid="job-card">
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <div>
          <CardTitle className="flex items-center gap-2">
            {running && <Loader2 className="size-4 animate-spin text-primary" />}
            OCR {STATUS_LABEL[job.status] ?? job.status}
          </CardTitle>
          <CardDescription>
            {job.mode === "hybrid" ? "하이브리드" : job.mode === "ai" ? "AI OCR" : "무료 OCR"}
            {job.api.model ? ` · ${job.api.model}` : ""}
          </CardDescription>
        </div>
        <div className="flex gap-1.5">
          {running && (
            <Button size="sm" variant="outline" onClick={() => act("pause")} disabled={busy}>
              <Pause /> 일시 중지
            </Button>
          )}
          {job.status === "paused" && (
            <Button size="sm" onClick={() => act("resume", job.stop_reason === "cost_limit" ? { cost_limit_usd: (job.cost_limit_usd ?? 0) * 2 || 5 } : undefined)} disabled={busy}>
              <Play /> {job.stop_reason === "cost_limit" ? "상한 2배로 재개" : "재개"}
            </Button>
          )}
          {job.failed_pages > 0 && !running && (
            <Button size="sm" variant="outline" onClick={() => act("retry-failed")} disabled={busy}>
              <RefreshCcw /> 실패 페이지 재시도
            </Button>
          )}
          {(running || job.status === "paused") && (
            <Button size="sm" variant="ghost" onClick={() => act("cancel")} disabled={busy}>
              <Square /> 취소
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1.5 flex justify-between text-sm">
            <span>
              <b className="tabular-nums">{job.completed_pages}</b> / {job.total_pages}쪽 완료
              {job.current_pages.length > 0 && <span className="text-muted-foreground"> · 처리 중: {job.current_pages.join(", ")}쪽</span>}
            </span>
            <span className="tabular-nums text-muted-foreground">{pct}%</span>
          </div>
          <Progress value={pct} />
        </div>
        <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <Stat label="남은 페이지" value={`${job.remaining_pages}쪽`} />
          <Stat label="누적 비용" value={formatUsd(job.api.cost_usd)} sub={job.cost_limit_usd != null ? `상한 ${formatUsd(job.cost_limit_usd, 2)}` : undefined} />
          <Stat label="토큰 (입력/출력)" value={`${formatTokens(job.api.input_tokens)} / ${formatTokens(job.api.output_tokens)}`} />
          <Stat label="API 호출" value={`${job.api.calls}회`} sub={`실패 ${job.api.failed} · 재시도 ${job.api.retries}`} />
        </div>
        {job.from_cache_pages > 0 && <p className="text-xs text-muted-foreground">캐시 재사용 {job.from_cache_pages}쪽 (API 호출 없음)</p>}
        {job.api.uncertain > 0 && (
          <p className="text-xs text-[oklch(0.5_0.12_70)]">
            중단으로 결과를 받지 못한 요청 {job.api.uncertain}건이 있습니다. 과금되었을 수 있어 예상 비용
            {` ${formatUsd(job.api.uncertain_cost_estimate_usd)}`}을 상한 계산에 포함했습니다.
          </p>
        )}
        {job.error && <p className="text-sm text-destructive">{job.error}</p>}
        {job.failed.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-destructive">실패한 페이지 {job.failed_pages}개</summary>
            <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
              {job.failed.map((f) => (
                <li key={f.page_id}>
                  {f.page_number}쪽: {f.error}
                </li>
              ))}
            </ul>
          </details>
        )}
        <p className="text-[11px] text-muted-foreground">남은 시간은 페이지마다 처리 시간이 달라 표시하지 않습니다.</p>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-muted px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}
