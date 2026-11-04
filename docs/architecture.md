# 아키텍처

```
브라우저 ──(same origin)──▶ Next.js 16 (frontend, :3000)
                             │  /api/* rewrite (서버 측 프록시, API 키 없음)
                             ▼
                          FastAPI (backend, :8000, 외부 비공개)
                             │  업로드 검증 · 저장 · 작업 등록 · 진행률 조회
                             ▼
             ┌──── DB (SQLite / PostgreSQL): 문서·페이지·영역·작업·API 호출 장부
             │
          Worker (embedded 스레드 또는 별도 프로세스)
             │  page_tasks를 원자적으로 점유(CAS) → 페이지 단위 처리
             ▼
   전처리 → 로컬 검출(RapidOCR) → Claude 인식 → 하이브리드 검증/재인식 → 좌표 역변환
             │
             ▼
   파일 저장소 (로컬 디렉터리 / S3 호환) : 원본 · 전처리본 · 썸네일 · 내보내기 결과
```

- **API 키**는 백엔드 환경변수에만 있습니다. 프런트엔드에는 `NEXT_PUBLIC_*` 변수가 없고, 브라우저는 Next.js와만 통신합니다.
- 긴 작업은 HTTP 요청 안에서 처리하지 않습니다. `POST /ocr`는 작업을 등록하고 즉시 202를 반환하며, 브라우저는 `GET /jobs/{id}`(문서 조회에 포함)로 진행률을 폴링합니다.

## 디렉터리

```
backend/app
  api/          REST 라우트 (documents, jobs/exports, system: auth·settings·usage·evaluation)
  core/         설정(pydantic-settings), DB, 인증·CSRF·레이트리밋, 로그 마스킹
  models/       SQLAlchemy 모델
  services/     업로드·PDF 분석(ingest), 저장소, OCR 처리(ocr_service), 작업(jobs), 비용(cost), 내보내기, 보관기간
  ocr/          base, local_engine(RapidOCR), claude_engine, hybrid_engine, pipeline, orientation, prompts
  imaging/      이미지 입출력(EXIF), 전처리, 기하 변환
  pdf/          merger(페이지 구성), text_layer(보이지 않는 텍스트), exporter(PDF/TXT/MD/JSON + 자동 검증)
  workers/      DB 기반 영속 작업 큐 워커
  evaluation/   CER/WER 지표, 엔진 비교, CLI
frontend/src
  app/          /, /documents/[id], /documents/[id]/preview, /history, /settings, /evaluate, /login
  components/   upload(드롭존·페이지 그리드), viewer(페이지 뷰어·PDF.js 미리보기), editor(OCR·검수·내보내기), ui
```

## OCR 파이프라인 (페이지 1장)

1. **표시 이미지**: EXIF 방향 + 사용자 회전 적용 (PDF 페이지는 200 DPI 렌더링)
2. **전처리** (`imaging/preprocess.py`) — 원본은 절대 덮어쓰지 않고 별도 저장
   - 크기 조정(긴 변 1400~3300px), 종이 영역 검출 + 원근 보정, 조명 보정(채널별 배경 나눗셈), 대비(모든 채널 동일 선형 변환 → **색조 보존**), 노이즈 제거
   - 모든 기하 변환은 3×3 행렬(H)로 누적 → 결과 좌표를 원본으로 되돌릴 때 사용
3. **텍스트 줄 검출** (RapidOCR PP-OCRv6 small, 휠 내장) → 방향 판정 → 기울기 보정 → 재검출
   - 방향: 문서 방향 모델(RapidOrientation)이 **확률 0.8 이상**일 때만 회전. 줄 단위 0/180 분류기는 결정적일 때만 보조로 사용 (한글 손글씨에서 무작위에 가까움을 실측)
4. **인식**
   - 로컬: 각 줄을 원근 잘라내기 → 인식 (한국어 PP-OCRv5 모델, 불가 시 다국어 모델로 대체·표시)
   - Claude: 이미지(모델 고해상도 한계 2576px·4784 토큰 이내로 미리 축소 → API 측 리사이즈 없음) + 줄 상자 목록 `r3: [x0,y0,x1,y1]` → 구조화 출력(Pydantic, `beta.messages.parse`)으로 `{id, text, uncertain, uncertain_parts, block, block_type}` 수신
   - Claude가 페이지가 회전되어 있다고 보고(`page_rotation`)하면 이미지를 세운 뒤 한 번 다시 읽음
5. **하이브리드 검증** (`ocr/hybrid_engine.py`) — 의심 줄 판정:
   - 글자 수·폭 비율 불일치(상자 길이/높이 대비 텍스트 폭) → 다른 상자의 글을 넣었을 가능성
   - Claude 누락, 로컬 결과와 큰 차이(로컬 모델이 그 문자를 지원할 때만), Claude 자체 불확실 표시
   - 의심 줄만 잘라 여러 이미지를 한 번의 요청으로 재인식 → 일치하면 확정, 다르면 대체안 보관 + "확인 필요"
6. **좌표 역변환**: 상자를 H⁻¹로 원본 좌표로 되돌리고 0~1로 정규화해 저장

## 검색 가능한 PDF (`pdf/`)

- 이미지 페이지: 원본 JPEG/PNG 바이트를 **재압축 없이** 삽입하고 회전은 배치 행렬로 처리. iPhone MPO는 기본 JPEG 프레임만 바이트 그대로 추출. WEBP는 PNG(무손실)로 변환.
- 기존 PDF 페이지: 원본 페이지를 그대로 복사(덮어쓰지 않음). 이미 같은 위치에 텍스트가 있으면 OCR 텍스트를 추가하지 않음(중복 방지).
- 텍스트 레이어: 렌더 모드 3(보이지 않음), 줄마다 하나의 텍스트 런. 글꼴 크기는 줄 높이의 85%, 가로 배율·회전은 상자에 맞춤. 같은 줄의 겹치는 상자는 경계 가운데에서 나눠 드래그 시 이웃 줄이 잡히지 않게 함. 줄 끝에 공백을 넣어 맞닿은 런이 한 단어로 합쳐지지 않게 함.
- 폰트: Droid Sans Fallback(한글·영문·CJK 포함)을 Type0/Identity-H + ToUnicode로 **서브셋 임베딩** → 뷰어 PC에 폰트가 없어도 복사·검색 시 한글이 깨지지 않음.
- 생성 직후 자동 검증: 다시 열어 (1) 유니코드 추출이 입력과 같은지 (2) 각 영역 사각형 안의 글자가 그 줄의 텍스트인지 확인 → 내보내기 화면에 표시. **파일 검증일 뿐 뷰어별 드래그 동작 검증은 아님** ([testing.md](testing.md)).

## 작업 큐와 안정성

- Redis 대신 **DB 기반 영속 큐**를 선택: 페이지 작업 상태가 이미 DB에 있으므로 큐와 DB를 동시에 쓰는 이중 기록 불일치가 없고, SQLite(개발)·PostgreSQL(배포) 모두 같은 코드로 동작합니다. 점유는 `UPDATE ... SET status='running' WHERE id=? AND status='pending'`(compare-and-set)으로 여러 워커가 동시에 돌아도 중복 처리되지 않습니다.
- 서버 재시작 시: `running`으로 남은 작업은 `pending`으로 되돌리고, 결과를 받지 못한 API 호출(`started`)은 **`uncertain`(과금되었을 수 있음)** 으로 표시합니다. 이 호출의 예상 비용은 비용 상한 계산에 계속 포함됩니다.
- 모든 Claude 요청은 **보내기 전에** 장부(`api_calls`)에 기록하고, 결과(토큰·비용·request-id·재시도)를 갱신합니다. 재시도는 SDK 내부가 아니라 직접 수행해 횟수를 정확히 기록합니다(429/5xx/연결 오류만, 지수 백오프).
- 비용 상한(작업별·사용자 하루) 초과가 예상되면 **요청 전에** 차단하고 작업을 일시중지합니다. 상한을 올려 재개하면 남은 페이지부터 이어서 처리합니다.
- 결과 캐시: (원본 해시, 회전, 모드, 모델, effort, 프롬프트 버전, 전처리 설정, 로컬 모델) 키로 저장 → 같은 이미지 재처리 시 API 호출 없음.
- 사용자 수정(`text_user`)은 별도 컬럼에 저장되고 재OCR로 덮어쓰지 않습니다(새 영역과 겹치면 이어받고, 아니면 그대로 유지). 원본 OCR(`text_ocr`), Claude 1차(`text_claude`), 로컬(`text_local`)도 따로 보존합니다.

## 기술 선택과 버전

| 구성 | 버전 | 이유 |
|---|---|---|
| Python | 3.13 | onnxruntime·opencv 휠 제공 확인 |
| anthropic SDK | 2.4.0 | `beta.messages.parse` + `fallbacks` 지원 |
| Claude 모델 | `claude-opus-5-5` (설정 `OCR_CLAUDE_MODEL`) | Opus 5.5는 thinking을 끌 수 없어 `effort`(기본 medium)로 비용을 조절 |
| RapidOCR | 3.10.0 | PP-OCRv6 검출 모델 휠 내장, 한국어 PP-OCRv5 인식 모델 레지스트리 |
| OpenCV | 5.0 headless | RapidOCR 3.10과 함께 동작 확인 |
| PyMuPDF | 2.1 | TextWriter + 서브셋 임베딩으로 한글 ToUnicode 정상 |
| Next.js | 16.4.2 | 16.4.5는 배포 하루 된 버전이라 3주 지난 16.4.2 선택 |
| React | 19.3.0 | |
| TypeScript | 5.9.3 | 7.0(새 네이티브 컴파일러)은 Next 플러그인 호환을 확인하지 못해 5.9 유지 |
| Tailwind | 4.3.3 | |
| pdfjs-dist | 6.4.299 | 미리보기·드래그 테스트용 |
| shadcn/ui | 소스 복사 방식 | 이 개발 환경에서 레지스트리 CLI 접속 불가 → 같은 방식(radix-ui + cva)으로 컴포넌트 직접 작성 |

설정 변수 이름은 `OCR_CLAUDE_*` 접두사를 씁니다. Claude Code 같은 도구가 `CLAUDE_EFFORT` 등을 설정하는 환경에서 실제로 값이 섞이는 문제를 겪었기 때문입니다.
