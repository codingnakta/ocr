# REST API

모든 경로는 `/api` 아래입니다. 브라우저는 Next.js(같은 출처)를 통해 호출합니다.

- **인증**: `AUTH_MODE=password`이면 `POST /api/auth/login` 후 HttpOnly 쿠키(`pb_session`)로 인증합니다. 다른 사용자의 리소스는 존재 여부와 관계없이 404를 반환합니다.
- **CSRF**: 상태를 바꾸는 요청(POST/PUT/PATCH/DELETE)에는 `X-PB-CSRF: 1` 헤더가 필요합니다.
- **오류 형식**: `{"detail": "한국어 메시지"}`. 429 응답에는 `Retry-After`가 붙습니다.
- 개발 모드에서는 OpenAPI 문서를 `/api/docs`에서 볼 수 있습니다.

## 문서·파일

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/documents` | 내 문서 목록 |
| POST | `/api/documents` | 문서 생성. JSON `{title}` 또는 multipart(`files`*, `title`)로 업로드와 동시에 생성 |
| POST | `/api/documents/{id}/files?brief=1` | 파일 추가(여러 개 가능). 파일별 오류는 `upload.errors`로 반환하며, 나머지 파일은 계속 처리합니다 |
| GET | `/api/documents/{id}` | 문서, 페이지, 최근 OCR 작업 진행률, 내보내기 목록 |
| PATCH | `/api/documents/{id}` | `{title, page_size_mode: original\|a4, pdf_image_source: original\|enhanced}` |
| DELETE | `/api/documents/{id}` | 문서와 모든 파일 삭제 |
| PUT | `/api/documents/{id}/pages/order` | `{page_ids: [...]}` 전체 순서 |
| PATCH | `/api/documents/{id}/pages/{page}` | `{rotate: 90\|180\|270\|-90, label}`. 회전하면 OCR 좌표도 함께 회전합니다 |
| DELETE | `/api/documents/{id}/pages/{page}` | 페이지 삭제 |
| GET | `/api/documents/{id}/pages/{page}/thumbnail` | 썸네일 JPEG |
| GET | `/api/documents/{id}/pages/{page}/image?variant=display\|processed` | 화면 표시용 이미지 또는 전처리 이미지 |

`{page}` 자리에는 페이지 id나 1부터 시작하는 페이지 번호를 넣을 수 있습니다.

## OCR

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/documents/{id}/ocr/estimate` | `{mode, page_ids?, rerun}` → 예상 페이지 수·토큰·비용(추정치 표시) |
| POST | `/api/documents/{id}/ocr` | `{mode: ai\|local\|hybrid, page_ids?, rerun, cost_limit_usd, effort?, preprocess?, use_cache}` → 202, 작업 진행 정보 |
| GET | `/api/documents/{id}/jobs` | 작업 기록 |
| GET | `/api/jobs/{job_id}` | 진행률, 현재 페이지, 토큰, 누적 비용, 호출·실패·재시도 수, 과금 불확실 호출, 실패 페이지 |
| POST | `/api/jobs/{job_id}/pause` | 일시 중지. 처리 중인 페이지는 끝까지 마칩니다 |
| POST | `/api/jobs/{job_id}/resume` | `{cost_limit_usd?}` 재개(상한을 올려 재개할 수 있음) |
| POST | `/api/jobs/{job_id}/cancel` | 취소. 남은 페이지는 `cancelled` |
| POST | `/api/jobs/{job_id}/retry-failed` | 실패한 페이지만 다시 처리 |
| GET | `/api/documents/{id}/pages/{page}/ocr` | 페이지 인식 결과(영역별 텍스트, 원본 OCR, Claude 1차, 로컬, 사용자 수정, 좌표, 불확실·검수 표시) |
| PATCH | `/api/documents/{id}/pages/{page}/ocr` | `{regions:[{id, text_user?, revert?, reviewed?}], create:[{polygon, text_user}], delete:[id]}`. 사용자 수정은 이후 OCR로 덮어쓰이지 않습니다 |
| POST | `/api/documents/{id}/pages/{page}/reocr` | `{rect:[x0,y0,x1,y1] \| region_id, engine: claude\|local, create_region}` → 후보 텍스트. 적용은 사용자가 결정합니다 |

### 작업 상태 값

`queued` → `running` → `completed` 또는 `completed_with_errors`. 그 밖에 `paused`(사용자 요청 또는 `stop_reason=cost_limit`)와 `cancelled`가 있습니다.

## 내보내기

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/documents/{id}/export` | `{format: pdf\|txt\|md\|json, size_mode?, image_source?}` → 백그라운드 생성 |
| GET | `/api/exports/{export_id}` | 상태와 PDF 자동 검증 결과(`report.position_ok`, `extract_similarity_min`) |
| GET | `/api/exports/{export_id}/download` | 파일 다운로드(본인만, `Cache-Control: no-store`) |
| GET | `/api/documents/{id}/download?format=pdf` | 해당 형식의 최신 결과 다운로드 |

## 시스템

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 상태 확인 |
| POST | `/api/auth/login`, `/api/auth/logout` | 로그인과 로그아웃(로그인 시도는 IP당 15분에 10회 제한) |
| GET | `/api/auth/me` | 로그인 상태 |
| GET | `/api/settings` | 모델, effort, 단가, 로컬 OCR 모델 정보, 업로드 제한, 데이터 전송 안내. API 키는 포함하지 않습니다 |
| GET | `/api/usage?days=30` | 모델별·일별 비용, 최근 호출(상태, 토큰, 재시도, 오류), 오늘 사용액과 하루 상한 |
| POST | `/api/evaluations` | multipart `image`, `ground_truth?`, `engines=local,ai,hybrid` → 엔진별 결과, 시간, 비용, PDF 검증. 정답 텍스트가 있을 때만 CER/WER을 계산합니다 |
