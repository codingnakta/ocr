"use client";
import { useEffect, useState } from "react";
import { Info, Loader2, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
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

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Claude API</CardTitle>
            <CardDescription>모델은 서버 환경변수 OCR_CLAUDE_MODEL로 변경합니다.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <Row k="상태" v={s.claude.configured ? <Badge variant="success">API 키 설정됨</Badge> : <Badge variant="destructive">API 키 없음</Badge>} />
            <Row k="모델" v={<code data-testid="model">{s.claude.model}</code>} />
            <Row k="effort" v={s.claude.effort} />
            <Row k="거절 시 대체 모델" v={s.claude.fallbacks ?? "사용 안 함"} />
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
