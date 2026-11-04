"use client";
import { useEffect, useState } from "react";
import { Info, Loader2, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from "@/components/ui/primitives";
import { apiGet, apiSend } from "@/lib/api";
import { formatTokens, formatUsd } from "@/lib/utils";
import type { SettingsInfo } from "@/types/api";

interface Usage {
  days: number;
  pages_processed: number;
  total_cost_usd: number;
  uncertain_calls: number;
  uncertain_cost_estimate_usd: number;
  today_spent_usd: number;
  daily_limit_usd: number;
  by_model: Record<string, { calls: number; input_tokens: number; output_tokens: number; cost_usd: number; failed: number; uncertain: number }>;
  recent: {
    id: string;
    at: string;
    purpose: string;
    model: string;
    status: string;
    input_tokens: number;
    output_tokens: number;
    thinking_tokens: number;
    cost_usd: number;
    estimated_cost_usd: number;
    retry: number;
    error: string | null;
  }[];
  note: string;
}

export default function SettingsPage() {
  const [s, setS] = useState<SettingsInfo | null>(null);
  const [u, setU] = useState<Usage | null>(null);
  useEffect(() => {
    apiGet<SettingsInfo>("/api/settings").then(setS).catch(() => undefined);
    apiGet<Usage>("/api/usage?days=30").then(setU).catch(() => undefined);
  }, []);
  if (!s) return <Loader2 className="mx-auto mt-16 size-5 animate-spin text-muted-foreground" />;

  return (
    <div className="mx-auto max-w-5xl space-y-4 px-4 py-6">
      <div className="flex items-center">
        <h1 className="text-xl font-semibold">설정 · 사용량</h1>
        {s.auth_mode === "password" && (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={async () => {
              await apiSend("POST", "/api/auth/logout");
              location.href = "/login";
            }}
          >
            <LogOut /> 로그아웃
          </Button>
        )}
      </div>

      {s.desktop_mode && <ApiKeyCard s={s} onSaved={() => apiGet<SettingsInfo>("/api/settings").then(setS)} />}
      {s.desktop_mode && <ModelCard s={s} onSaved={() => apiGet<SettingsInfo>("/api/settings").then(setS)} />}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Claude API</CardTitle>
            <CardDescription>
              {s.desktop_mode ? "모델은 위의 'Claude 모델'에서 고릅니다." : "모델은 서버 환경변수 OCR_CLAUDE_MODEL로 변경합니다."}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row k="상태" v={s.claude.configured ? <Badge variant="success">API 키 설정됨</Badge> : <Badge variant="destructive">API 키 없음</Badge>} />
            <Row k="모델" v={<code data-testid="model">{s.claude.model}</code>} />
            <Row k="effort" v={s.claude.effort} />
            <Row k="거절 시 대체 모델" v={s.claude.fallbacks ?? "해당 없음"} />
            <Row
              k="단가 (100만 토큰)"
              v={s.claude.pricing ? `입력 $${s.claude.pricing.input} · 출력 $${s.claude.pricing.output}` : "알 수 없음 (추정)"}
            />
            <Row k="요금표 확인일" v={s.claude.pricing_checked} />
            <Row k="이미지 전송 한도" v={`긴 변 ${s.claude.image_limits.long_edge_px}px · ${s.claude.image_limits.max_visual_tokens} 토큰`} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>로컬 OCR (RapidOCR)</CardTitle>
            <CardDescription>무료 모드와 하이브리드 위치 검출에 사용됩니다.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row k="상태" v={s.local_ocr.available ? <Badge variant="success">사용 가능</Badge> : <Badge variant="destructive">사용 불가</Badge>} />
            <Row k="검출 모델" v={s.local_ocr.det_model ?? "-"} />
            <Row k="인식 모델" v={s.local_ocr.rec_model ?? "-"} />
            <Row k="한글 인식" v={s.local_ocr.supports_hangul ? "지원" : "미지원"} />
            {s.local_ocr.note && <p className="text-xs text-[oklch(0.5_0.12_70)]">{s.local_ocr.note}</p>}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>API 사용량 (최근 30일)</CardTitle>
          <CardDescription>{u?.note}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {u ? (
            <>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Tile label="총 비용" value={formatUsd(u.total_cost_usd)} />
                <Tile label="오늘 사용 / 일일 상한" value={`${formatUsd(u.today_spent_usd)} / ${formatUsd(u.daily_limit_usd, 0)}`} />
                <Tile label="처리 페이지" value={`${u.pages_processed}쪽`} />
                <Tile label="과금 불확실 호출" value={`${u.uncertain_calls}건`} sub={u.uncertain_calls ? `예상 ${formatUsd(u.uncertain_cost_estimate_usd)}` : undefined} />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="py-1.5">시각</th>
                      <th>용도</th>
                      <th>모델</th>
                      <th>상태</th>
                      <th className="text-right">입력</th>
                      <th className="text-right">출력</th>
                      <th className="text-right">비용</th>
                    </tr>
                  </thead>
                  <tbody>
                    {u.recent.slice(0, 50).map((c) => (
                      <tr key={c.id} className="border-t">
                        <td className="py-1.5 text-xs">{new Date(c.at).toLocaleString("ko-KR")}</td>
                        <td className="text-xs">{c.purpose}</td>
                        <td className="text-xs">{c.model}</td>
                        <td>
                          <Badge variant={c.status === "succeeded" ? "success" : c.status === "uncertain" ? "warning" : "destructive"} title={c.error ?? undefined}>
                            {c.status}
                            {c.retry ? ` (재시도 ${c.retry})` : ""}
                          </Badge>
                        </td>
                        <td className="text-right tabular-nums">{formatTokens(c.input_tokens)}</td>
                        <td className="text-right tabular-nums">{formatTokens(c.output_tokens)}</td>
                        <td className="text-right tabular-nums">{c.status === "uncertain" ? `≈${formatUsd(c.estimated_cost_usd)}` : formatUsd(c.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!u.recent.length && <p className="py-4 text-center text-sm text-muted-foreground">아직 API 호출이 없습니다.</p>}
              </div>
            </>
          ) : (
            <Loader2 className="size-4 animate-spin" />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>제한 · 데이터 안내</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <div className="grid gap-2 sm:grid-cols-3">
            <Tile label="파일당 최대" value={`${s.limits.max_file_mb}MB`} />
            <Tile label="문서당 최대" value={`${s.limits.max_pages_per_document}쪽`} />
            <Tile label="기본 작업 비용 상한" value={formatUsd(s.limits.default_job_cost_limit_usd, 2)} />
          </div>
          <p className="text-muted-foreground">
            파일 보관: {s.limits.retention_days ? `${s.limits.retention_days}일 후 자동 삭제` : "삭제할 때까지 보관 (개발 모드)"}
          </p>
          <ul className="space-y-1.5">
            {s.notices.map((n) => (
              <li key={n} className="flex gap-2">
                <Info className="mt-0.5 size-4 shrink-0 text-primary" /> {n}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}

function ApiKeyCard({ s, onSaved }: { s: SettingsInfo; onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const r = await apiSend<{ warning: string | null }>("POST", "/api/settings/api-key", { api_key: key.trim() });
      setKey("");
      toast.success("API 키를 저장했습니다.");
      if (r.warning) toast.warning(r.warning);
      onSaved();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!confirm("저장된 API 키를 이 컴퓨터에서 지울까요?")) return;
    await apiSend("DELETE", "/api/settings/api-key").catch((e) => toast.error(e.message));
    onSaved();
  };
  return (
    <Card data-testid="api-key-card">
      <CardHeader>
        <CardTitle>Claude API 키</CardTitle>
        <CardDescription>
          이 컴퓨터에만 저장됩니다(다른 곳으로 전송되지 않음). 키는{" "}
          <a href="https://platform.claude.com" target="_blank" rel="noreferrer" className="text-primary underline">
            platform.claude.com
          </a>{" "}
          → API Keys에서 만들 수 있습니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">
          현재:{" "}
          {s.claude.configured ? (
            <Badge variant="success">설정됨 {s.claude.key_hint}</Badge>
          ) : (
            <Badge variant="warning">없음</Badge>
          )}
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            type="password"
            placeholder="sk-ant-..."
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="off"
            data-testid="api-key-input"
          />
          <Button onClick={save} disabled={busy || key.trim().length < 20} data-testid="api-key-save">
            {busy ? <Loader2 className="animate-spin" /> : null} 저장
          </Button>
          {s.claude.configured && (
            <Button variant="ghost" onClick={remove}>
              지우기
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ModelCard({ s, onSaved }: { s: SettingsInfo; onSaved: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const choose = async (id: string) => {
    setBusy(id);
    try {
      await apiSend("POST", "/api/settings/model", { model: id });
      toast.success("OCR 모델을 바꿨습니다. 새로 시작하는 OCR부터 적용됩니다.");
      onSaved();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card data-testid="model-card">
      <CardHeader>
        <CardTitle>Claude 모델 (AI·하이브리드 OCR)</CardTitle>
        <CardDescription>
          저렴한 모델일수록 손글씨를 틀릴 수 있습니다. <a href="/evaluate/" className="text-primary underline">정확도 평가</a>에서
          내 필기로 모델을 비교해 보고 고르세요. 비용은 사진 1쪽 기준 추정치입니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2 sm:grid-cols-3">
        {(s.claude.model_choices ?? []).map((m) => {
          const active = s.claude.model === m.id;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => !active && choose(m.id)}
              disabled={!!busy}
              className={`rounded-lg border p-3 text-left transition-colors ${active ? "border-primary bg-accent ring-1 ring-primary" : "hover:bg-muted"}`}
              data-testid={`model-${m.id}`}
            >
              <div className="flex items-center gap-2 text-sm font-medium">
                {busy === m.id && <Loader2 className="size-4 animate-spin" />}
                {m.name}
                {active && <Badge className="ml-auto">사용 중</Badge>}
              </div>
              <div className="mt-1 text-lg font-semibold tabular-nums">≈ {formatUsd(m.per_page_usd)} / 쪽</div>
              <p className="mt-0.5 text-xs text-muted-foreground">{m.note}</p>
              <p className="text-[11px] text-muted-foreground">200쪽 ≈ {formatUsd(m.per_page_usd * 200, 2)}</p>
            </button>
          );
        })}
      </CardContent>
    </Card>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b pb-1.5 last:border-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-muted px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}
