# Render 배포 안내

저장소에 들어 있는 `render.yaml`(Blueprint)을 Render가 읽어 필요한 설정을 자동으로 만듭니다. 직접 하실 일은 **계정 만들기 → 저장소 선택 → 비밀값 2개 입력**입니다.

## 만들어지는 것

| 항목 | 내용 |
|---|---|
| 웹 서비스 `pilgibot-ocr` | 화면(Next.js), 서버(FastAPI), OCR 작업 처리기를 컨테이너 하나로 실행 (`Dockerfile`) |
| 디스크 `pilgibot-data` 5GB | `/data`에 업로드 파일과 SQLite DB 저장. 재배포·재시작해도 유지 |
| 지역 | Singapore (한국에서 가장 가까운 Render 지역) |
| 요금제 | **Standard** (메모리 2GB). OCR에 메모리가 많이 필요해 Free·Starter로는 동작하지 않습니다 |

> 요금은 Render 가입·생성 화면에 표시되는 금액이 기준입니다. 이 안내서를 쓸 때 Render 사이트에 접속할 수 없어 가격을 직접 확인하지 못했습니다. 대략 월 $25 안팎(Standard)에 디스크 요금이 조금 더해집니다. Claude API 비용은 별도이며 Anthropic에 청구됩니다.

## 1. 준비물

- GitHub 계정 (`codingnakta/ocr` 저장소)
- Anthropic API 키 — 채팅 등에 노출된 적 없는 **새 키**를 권장합니다 ([platform.claude.com](https://platform.claude.com) → API Keys)
- 로그인 비밀번호로 쓸 문자열 (12자 이상)

## 2. 배포

1. https://render.com 에서 **Sign up → GitHub로 로그인**합니다.
2. 결제 수단을 등록합니다(Standard 요금제 사용에 필요).
3. 대시보드 오른쪽 위 **New +** → **Blueprint**를 누릅니다.
4. GitHub 연결 권한을 묻는 창이 나오면 `codingnakta/ocr` 저장소 접근을 허용합니다.
5. 저장소 목록에서 **codingnakta/ocr**을 고르고 **Connect**를 누릅니다.
   - 브랜치 선택이 나오면 **`pilgibot-ocr-studio`**를 고릅니다.
   - 브랜치를 고르는 칸이 없거나 `render.yaml`을 찾지 못한다는 메시지가 나오면, 코드를 `main`에 합친 뒤 다시 시도하세요(Pull Request를 만들어 드릴 수 있습니다). 이때 `render.yaml`의 `branch:` 줄도 `main`으로 바꿔야 합니다.
6. Blueprint 이름은 아무거나 입력합니다(예: `pilgibot`).
7. 값을 묻는 칸 두 개를 채웁니다.
   - `ANTHROPIC_API_KEY`: API 키 (`sk-ant-...`)
   - `ADMIN_PASSWORD`: 로그인 비밀번호
8. **Apply**를 누릅니다. 첫 빌드는 10~20분쯤 걸립니다. 서비스 화면의 **Logs** 탭에서 진행 상황을 볼 수 있습니다.
9. 상태가 **Live**가 되면 화면 위쪽의 주소(`https://pilgibot-ocr-xxxx.onrender.com`)를 엽니다.
10. 아이디 **`admin`**, 7번에서 정한 비밀번호로 로그인합니다.

## 3. 배포 후 확인

1. 오른쪽 위 **설정**에서 "API 키 설정됨"과 모델 `claude-opus-5-5`가 보이는지 확인합니다.
2. 사진 1장을 올려 **하이브리드** OCR을 실행합니다(약 $0.05). PDF를 만들어 다운로드한 뒤 드래그·검색이 되는지 확인합니다.
3. 휴대폰 브라우저로 같은 주소에 접속해 **홈 화면에 추가**하면 앱처럼 쓸 수 있습니다.

## 4. 운영

| 하고 싶은 일 | 방법 |
|---|---|
| 코드 업데이트 | 브랜치에 푸시하면 자동으로 다시 배포됩니다 (`autoDeploy`) |
| API 키 교체 | 서비스 → **Environment** → `ANTHROPIC_API_KEY` 수정 → **Save** (자동 재시작) |
| 비밀번호 변경 | 위와 같은 방법으로 `ADMIN_PASSWORD`를 바꿉니다. 단, 이미 만들어진 관리자 계정의 비밀번호는 바뀌지 않습니다. 이때는 Shell에서 아래 명령을 실행하세요 |
| 비용 상한 변경 | `DAILY_COST_LIMIT_USD`(하루, 기본 $10), `DEFAULT_JOB_COST_LIMIT_USD`(작업당, 기본 $5) |
| 문서 자동 삭제 기간 | `RETENTION_DAYS` (기본 14일, 0이면 계속 보관) |
| 로그 보기 | 서비스 → **Logs** (API 키와 문서 내용은 로그에 남지 않습니다) |

관리자 비밀번호 변경 명령 (서비스 → **Shell**):

```bash
cd /app/backend && python -c "from app.core.db import session_scope; from app.core.security import hash_password; from app.models import User; from sqlalchemy import select; s=session_scope().__enter__(); u=s.scalar(select(User).where(User.username=='admin')); u.password_hash=hash_password('새비밀번호'); s.commit(); print('ok')"
```

## 5. 알아둘 점

- **디스크 제약:** 디스크가 붙은 서비스는 인스턴스를 1개만 쓸 수 있고, 재배포할 때 수십 초 동안 접속이 끊길 수 있습니다. 처리 중이던 OCR 작업은 재시작 후 이어서 처리됩니다(완료된 페이지는 다시 호출하지 않음).
- **백업:** 데이터는 디스크 한 곳에만 있습니다. 중요한 결과 PDF는 내려받아 따로 보관하세요.
- **로컬 OCR 한글 인식:** 빌드 중에 한국어 모델을 내려받습니다. 설정 화면의 "한글 인식"이 "미지원"으로 나오면 다운로드가 실패한 것입니다. 이 경우에도 AI·하이브리드 모드는 정상 동작합니다.
- **사용자 추가:** 기본은 관리자 1명입니다. 추가 방법은 [deployment.md](deployment.md)에 있습니다.
- **라이선스:** PyMuPDF는 AGPL-3.0입니다. 다른 사람에게 서비스로 공개한다면 소스 공개 의무를 확인하세요. 개인용이라면 해당하지 않습니다.

## 이 구성의 검증 범위

- **확인한 것:** 이 저장소의 `Dockerfile`로 이미지를 빌드해 Render와 같은 조건(production, 로그인 필수, `/data` 디스크, 포트 10000)으로 실행했습니다. 로그인, 업로드, OCR, PDF 생성, 재시작 후 데이터 유지를 확인했습니다.
- **확인하지 못한 것:** Render 계정에서의 실제 배포는 하지 못했습니다. 개발 환경에서 render.com 접속이 막혀 있었기 때문입니다. `render.yaml` 문법 등에서 오류가 나면 오류 메시지를 알려주세요.
