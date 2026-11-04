# 필기봇 OCR Studio

손글씨 필기·인쇄 문서 사진(또는 PDF)을 올리면 **원본 모습은 그대로 두고, 글자를 드래그·복사·검색할 수 있는 PDF(Searchable PDF)** 로 만들어 주는 웹 앱입니다. 한글 손글씨를 중심으로 Claude Vision API와 로컬 OCR(RapidOCR)을 함께 사용합니다.

```
사진 200장 업로드 → 순서 정리·회전 → OCR 모드 선택·예상 비용 확인 → 백그라운드 처리(진행률·비용 표시, 일시중지/재개)
→ 검수·수정(원본과 나란히, 불확실한 줄 표시) → 검색 가능 PDF / TXT / Markdown / JSON 다운로드
```

## 빠른 시작

### 인터넷에 무료 배포 (Hugging Face Spaces, 결제 수단 불필요)

순서는 [docs/huggingface.md](docs/huggingface.md)를 보세요. 저장 공간이 임시라 결과 PDF는 바로 내려받아야 합니다.

### 유료 배포 (Render, 데이터 유지)

저장소의 `render.yaml`로 배포합니다. 순서는 [docs/render.md](docs/render.md)를 보세요.

### A. Docker (권장)

```bash
cp .env.example .env
```

`.env` 파일을 열어 `ANTHROPIC_API_KEY=` 에 API 키를 넣습니다.

```bash
docker compose up -d --build
```

브라우저에서 http://localhost:3000 을 엽니다. 기본 설정은 **이 컴퓨터에서만 접속(127.0.0.1)·로그인 없음**입니다. 스마트폰 등 다른 기기에서 쓰거나 인터넷에 공개하려면 [docs/deployment.md](docs/deployment.md)의 로그인 설정을 먼저 하세요.

### B. 개발 모드 (Docker 없이)

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate
```

```bash
pip install -r requirements-dev.txt
```

```bash
python scripts/fetch_assets.py models
```

```bash
uvicorn app.main:app --port 8000
```

다른 터미널에서:

```bash
cd frontend && npm install && npm run dev
```

http://localhost:3000 (Next.js가 `/api`를 FastAPI 8000번으로 프록시합니다)

## OCR 모드

| 모드 | 동작 | 비용 |
|---|---|---|
| **하이브리드 (추천)** | 로컬 검출기가 줄 위치를 찾고 → Claude가 페이지 전체를 읽으며 각 줄 상자(id)를 전사 → 의심되는 줄(길이 불일치·누락·불확실 등)만 잘라서 Claude로 다시 읽어 비교 | 쪽당 약 $0.04~0.11 (측정값, 아래 참고) |
| AI OCR | 위와 같으나 재확인 단계 없음 | 쪽당 약 $0.04~0.07 |
| 무료 OCR | RapidOCR만 사용, 외부 전송 없음 | $0 |

Claude는 정확한 글자 좌표를 보장하지 않으므로 **좌표는 항상 로컬 검출기에서** 가져옵니다. 어느 상자에도 속하지 않는 텍스트는 임의 위치에 두지 않고 "위치 없음"으로 표시해 PDF 텍스트 레이어에서는 빼고 TXT/MD/JSON에는 넣습니다.

## 검증 현황 (2026-11-04, 실제 실행 결과 기준)

| 항목 | 상태 | 근거 |
|---|---|---|
| Claude API 실제 OCR (한글 손글씨 사진) | ✅ 실측 | iPhone 사진 1장: 잠정 정답 대비 CER 0~3.8% (실행마다 다름), $0.063 |
| 테스트 1~4 (한글·영어·혼합 손글씨, 인쇄체) | ✅ 실측 (합성) | 손글씨 **폰트**로 만든 합성 이미지, CER 0% — 실제 손글씨 정확도의 근거는 아님 |
| 테스트 5 (10장 → PDF 1개, 순서 유지) | ✅ 자동 | `tests/test_pdf_layer.py` |
| 테스트 6 (PDF 한글 추출) | ✅ 자동 | PyMuPDF·pypdf·pdfminer 3종 모두 동일 추출 |
| 테스트 7 (드래그·복사) | ✅ PDF.js 자동 / ⚠️ Chrome·Acrobat 수동 | Playwright로 PDF.js에서 23/23줄 정확 선택, 여러 줄 순서 유지 |
| 테스트 8 (검색) | ✅ PDF.js·PyMuPDF 자동 / ⚠️ 뷰어 수동 | |
| 테스트 9 (수정 후 재생성) | ✅ 자동 | API·브라우저 E2E |
| 테스트 10 (일부 실패해도 다른 페이지 보존) | ✅ Mock | |
| 테스트 11 (200쪽 중단·재개) | ✅ Mock | 140쪽 처리 후 강제 중단 → 141쪽부터 재개, 중복 호출 0 |
| 테스트 12 (비용 상한 차단) | ✅ Mock | 상한 도달 시 요청 전에 차단·일시중지 |
| 원본 이미지 보존 | ✅ 자동 | JPEG(iPhone MPO 포함) 바이트 동일 임베딩 |
| 한국어 로컬 인식 모델 | ⚠️ 이 개발 환경에서 미검증 | 샌드박스에서 ModelScope 차단 → 다운로드 불가. 위치 검출(PP-OCRv6)은 검증 |
| Docker 이미지 빌드·compose 실행 | ✅ 실측 | backend·frontend 이미지 빌드, PostgreSQL+워커 분리 구성에서 업로드→로컬 OCR→PDF(위치 21/21) 확인. 컨테이너 안에서 Claude 호출은 샌드박스 네트워크 제약으로 미실행 |
| S3 저장소 | ⚠️ 미검증 | 코드만 구현 (실제 버킷 없음) |

자세한 테스트 방법·결과·수동 확인 절차: [docs/testing.md](docs/testing.md)

## 비용 (측정값, claude-opus-5-5, 2026-11-04 요금표 $4/$20 per MTok)

- 페이지 1회 인식: 입력 약 7,100~7,300 토큰(이미지 약 4,700 포함) + 출력 400~1,900 토큰 → **$0.037~0.067**
- 하이브리드 재확인(의심 줄만): 입력 1,500~2,400 토큰 → **$0.013~0.042**
- 200쪽 하이브리드 예상: **약 $10~20** (페이지 밀도·글씨 상태에 따라 다름). 시작 전 화면에서 추정치를 보여주고, 작업·하루 상한을 넘으면 자동으로 멈춥니다.
- 같은 이미지·설정은 캐시되어 다시 처리해도 API를 호출하지 않습니다.
- 크레딧 잔액은 API로 조회할 수 없어 앱에 표시하지 않습니다(Console > Billing 확인). Claude Pro/Max 구독 한도와 API 크레딧은 별개입니다.

## 문서

- [docs/architecture.md](docs/architecture.md) — 구조, OCR 파이프라인, PDF 텍스트 레이어, 작업 큐, 기술 선택 이유
- [docs/api.md](docs/api.md) — REST API
- [docs/huggingface.md](docs/huggingface.md) — Hugging Face Spaces 무료 배포 안내
- [docs/render.md](docs/render.md) — Render 배포 단계별 안내 (유료)
- [docs/deployment.md](docs/deployment.md) — 배포, 로그인, 보안, 환경변수
- [docs/testing.md](docs/testing.md) — 자동/수동 테스트, 뷰어 체크리스트, 정확도 평가 도구

## 라이선스 · 외부 리소스

- PDF 텍스트 레이어 폰트: Droid Sans Fallback (PyMuPDF 내장, Apache-2.0) — 사용자 PC에 폰트 설치 불필요
- 로컬 OCR 모델: PaddleOCR PP-OCR 계열 (Apache-2.0), RapidOCR (Apache-2.0), RapidOrientation (Apache-2.0)
- 테스트용 손글씨풍 폰트: Nanum Pen Script, Gaegu, Gamja Flower, Hi Melody (SIL OFL, 테스트에만 사용, 저장소 미포함)
- PyMuPDF는 AGPL-3.0입니다. 서비스로 공개 배포할 때는 AGPL 조건(소스 공개) 또는 Artifex 상용 라이선스를 검토하세요.
