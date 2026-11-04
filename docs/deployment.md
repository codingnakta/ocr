# 배포 가이드

## 1. 개인 PC (기본값)

`docker compose up -d --build`로 실행합니다. 프런트엔드는 `127.0.0.1:3000`에만 열리고 로그인은 없습니다(`APP_ENV=development`, `AUTH_MODE=none`). 다른 기기에서는 접속할 수 없습니다.

## 2. 집 안 네트워크(스마트폰 사진 업로드) 또는 인터넷 공개

인증 없이 공개하면 누구나 내 API 키로 OCR을 돌릴 수 있습니다. 공개 전에 반드시 다음을 설정하세요.

```
APP_ENV=production            # AUTH_MODE=none 이면 서버가 시작을 거부합니다
AUTH_MODE=password
SESSION_SECRET=<openssl rand -hex 32 결과>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<긴 비밀번호>
COOKIE_SECURE=true            # HTTPS로 서비스할 때
PUBLISH_ADDR=0.0.0.0
DAILY_COST_LIMIT_USD=10
DEFAULT_JOB_COST_LIMIT_USD=5
RETENTION_DAYS=14
```

- **HTTPS 필수:** Caddy, Nginx, Cloudflare Tunnel 등 리버스 프록시 뒤에 두세요. 휴대폰 카메라 업로드와 PWA 설치도 HTTPS에서 동작합니다.
- **사용자 추가:** 관리자 계정은 시작할 때 자동으로 만들어집니다. 다른 사용자는 아래처럼 추가합니다.

  ```bash
  docker compose exec backend python -c "from app.core.db import session_scope; from app.core.security import hash_password; from app.models import User; s=session_scope().__enter__(); s.add(User(username='name', password_hash=hash_password('password'))); s.commit()"
  ```

- **데이터 분리:** 사용자별 문서는 소유자만 볼 수 있습니다. 다른 사용자의 id로 요청하면 404가 반환됩니다.

## 3. 구성 요소

| 서비스 | 역할 | 외부 공개 |
|---|---|---|
| frontend | Next.js (`/api` 프록시) | 예 (3000) |
| backend | FastAPI | 아니오 (compose 내부 네트워크) |
| worker | OCR·내보내기 처리 | 아니오 |
| db | PostgreSQL 16 | 아니오 |

- **워커 확장:** `docker compose up -d --scale worker=2`로 늘릴 수 있습니다. 워커가 여러 개이면 `.env`에 `WORKER_RECOVER_ALL=false`를 설정하세요. 이 경우 오래된 잠금(`TASK_LOCK_TIMEOUT_S`, 기본 15분)만 회수합니다.
- **파일 저장:** 기본값은 `files` 볼륨입니다. S3 호환 저장소를 쓰려면 `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`를 설정합니다. 파일은 공개 URL 없이 백엔드를 거쳐 권한을 확인한 뒤 내려줍니다. (S3 구현은 이 개발 환경에서 실제 버킷으로 검증하지 못했습니다.)
- **보관 기간:** `RETENTION_DAYS` 동안 수정이 없는 문서는 워커가 1시간마다 정리합니다. production 기본값은 14일입니다.

## 4. 로컬 OCR 모델

이미지를 빌드할 때 `scripts/fetch_assets.py models`가 실행되어 다음을 받습니다.

- **한국어 인식 모델**(`korean_PP-OCRv5_rec_mobile`): RapidOCR 공식 레지스트리(ModelScope)에서 받습니다. 접속이 안 되면 빌드는 계속되고, 설정 화면에 "한글 미지원"으로 표시됩니다.
- **검출 모델·방향 모델:** 검출은 RapidOCR 휠에 내장된 PP-OCRv6를 씁니다. 방향 모델은 PyPI `onnxocr` 휠에서 추출합니다(Apache-2.0).

직접 지정하려면 `LOCAL_OCR_DET_MODEL`, `LOCAL_OCR_REC_MODEL`, `LOCAL_OCR_REC_KEYS`, `ORIENTATION_MODEL`에 경로를 설정합니다.

## 5. 보안 체크리스트

- [x] API 키는 백엔드 환경변수에만 둡니다. 프런트엔드 번들에는 없습니다(`NEXT_PUBLIC_*` 미사용). `/api/settings` 응답에도 포함되지 않습니다.
- [x] `.env`는 `.gitignore`에 들어 있습니다. 저장소에는 `.env.example`만 있습니다.
- [x] 업로드는 MIME이 아니라 매직 바이트로 형식을 판별합니다. 크기, 페이지 수, 해상도(압축 폭탄) 제한이 있고 암호화·손상 PDF는 거부합니다.
- [x] 파일명은 표시용으로만 쓰고(경로 제거), 저장 키는 서버가 생성합니다. 로컬 저장소는 루트 밖 경로를 거부합니다.
- [x] 세션 쿠키는 HttpOnly, SameSite=Lax, HTTPS에서는 Secure입니다. 상태 변경 요청에는 CSRF 헤더가 필요합니다.
- [x] 로그인, 업로드, OCR 시작, 재인식, 평가 요청에 횟수 제한이 있습니다(프로세스 내부 기준이므로 다중 인스턴스라면 프록시에서도 제한하세요).
- [x] 로그에서 API 키 패턴을 가리고, SDK·httpx 디버그 로그(요청 본문 포함)는 끕니다. 문서 텍스트는 로그에 남기지 않습니다.
- [x] 작업별·하루 비용 상한을 요청 전에 확인합니다.
- [ ] 의존성 보안 업데이트: `pip list --outdated`, `npm outdated`로 주기적으로 확인하세요. 특히 PyMuPDF, Pillow, pdfjs-dist는 파서 취약점 패치가 잦습니다.
- [ ] **PyMuPDF 라이선스(AGPL-3.0):** 인터넷 서비스로 공개하려면 소스 공개 의무를 지키거나 상용 라이선스를 검토하세요.
