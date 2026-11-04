# Hugging Face Spaces 무료 배포 안내 (결제 수단 불필요)

Hugging Face Spaces의 **무료 CPU 공간**에서 Docker로 앱 전체를 실행합니다. GitHub 저장소에 넣어 둔 자동화(`.github/workflows/deploy-hf-space.yml`)가 코드를 Space로 올려줍니다.

## 먼저 알아둘 점

| 항목 | 내용 |
|---|---|
| 비용 | Space는 무료(무료 CPU 하드웨어). **Claude API 사용료는 별도**로 Anthropic에 청구됩니다(사진 1장 약 $0.05) |
| 저장 | **임시 저장입니다.** Space가 재시작되면(코드 업데이트, 오랫동안 사용하지 않아 잠든 뒤 다시 깨어날 때 등) 올린 문서와 결과가 **사라집니다**. 결과 PDF는 바로 내려받아 보관하세요 |
| 잠자기 | 일정 기간 쓰지 않으면 Space가 잠듭니다. 다시 접속하면 깨어나는 데 몇 분 걸릴 수 있습니다 |
| 공개 범위 | Space(코드 파일)는 공개되지만, 앱은 **로그인해야만** 쓸 수 있습니다. API 키와 비밀번호는 비밀값으로 저장되어 공개되지 않습니다 |
| 접속 주소 | Hugging Face 페이지 안의 미리보기 창이 아니라 **`https://<아이디>-<space이름>.hf.space`** 주소로 직접 여세요. 보안 설정상 다른 사이트 안에 끼워 넣어 표시되지 않습니다 |

> 무료 사양·잠자기 시간 같은 정책은 Hugging Face가 바꿀 수 있습니다. 이 안내서를 쓸 때 huggingface.co에 접속할 수 없어 최신 정책을 확인하지 못했습니다.

## 1. Hugging Face 가입과 Space 만들기

1. https://huggingface.co/join 에서 가입하고 이메일 인증을 마칩니다. 카드 등록은 없습니다.
2. 오른쪽 위 **+ New** → **Space**를 누릅니다.
   - **Space name:** `pilgibot`
   - **SDK:** **Docker** → **Blank**
   - **Space hardware:** **CPU basic (Free)**
   - **Public**으로 둡니다. Private으로 하면 본인도 앱 주소로 바로 접속하기 어려워집니다.
   - **Create Space**를 누릅니다.
3. 만든 Space의 **Settings** → **Variables and secrets** → **New secret**에서 두 개를 추가합니다.

   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | Anthropic API 키 (`sk-ant-...`) |
   | `ADMIN_PASSWORD` | 앱 로그인 비밀번호 (12자 이상) |

   (선택) `SESSION_SECRET`에 임의의 긴 문자열을 넣으면 Space가 재시작돼도 로그인이 유지됩니다.

## 2. Hugging Face 토큰 만들기

1. 오른쪽 위 프로필 → **Settings** → **Access Tokens** → **Create new token**을 누릅니다.
2. **Token type**은 **Write**를 고르고, 이름은 `github-deploy`처럼 정합니다. **Create**를 누릅니다.
3. 표시된 토큰(`hf_...`)을 복사합니다. 채팅 등 다른 곳에는 붙여넣지 마세요.

## 3. GitHub에 토큰과 Space 이름 등록

`https://github.com/codingnakta/ocr` → **Settings** → **Secrets and variables** → **Actions**로 갑니다.

1. **Secrets** 탭 → **New repository secret**
   - Name: `HF_TOKEN`
   - Secret: 2단계에서 복사한 `hf_...` 토큰
2. **Variables** 탭 → **New repository variable**
   - Name: `HF_SPACE`
   - Value: `<Hugging Face 아이디>/pilgibot` (예: `codingnakta/pilgibot`)

## 4. 배포 실행

1. GitHub 저장소 → **Actions** 탭을 엽니다.
2. **Deploy to Hugging Face Space** 항목의 가장 최근 실행을 엽니다. 설정 전에 실행돼 빨간 X로 실패해 있는 것이 정상입니다.
3. 오른쪽 위 **Re-run jobs** → **Re-run all jobs**를 누릅니다.
4. 초록색 체크가 뜨면 Hugging Face Space 페이지로 가서 **Building** → **Running**이 될 때까지 기다립니다(10~20분).
5. **`https://<아이디>-pilgibot.hf.space`** 를 엽니다(예: `https://codingnakta-pilgibot.hf.space`).
6. 아이디 **`admin`**과 1단계에서 정한 `ADMIN_PASSWORD`로 로그인합니다.

이후에는 GitHub 브랜치(`pilgibot-ocr-studio` 또는 `main`)에 코드가 올라갈 때마다 자동으로 다시 배포됩니다.

## 5. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| Actions가 "HF_TOKEN … must be set"으로 실패 | 3단계 이름(`HF_TOKEN`, `HF_SPACE`) 철자와, Secrets/Variables 탭을 바꿔 넣지 않았는지 |
| Actions의 push 단계가 401/403으로 실패 | 토큰 권한이 **Write**인지, `HF_SPACE`의 아이디·Space 이름이 맞는지 |
| Space가 **Runtime error**, 로그에 `ADMIN_PASSWORD is not set` | 1-3단계 Space 비밀값에 `ADMIN_PASSWORD`를 추가한 뒤 **Restart** |
| Hugging Face 페이지 안의 화면이 빈칸 | 정상입니다. `https://<아이디>-pilgibot.hf.space`로 직접 여세요 |
| 로그인은 되는데 바로 다시 로그인 화면이 나옴 | 직접 주소(`.hf.space`)로 들어왔는지 확인하세요. HF 페이지 안의 창에서는 로그인 쿠키가 막힙니다 |
| 설정 화면에 "API 키 없음" | Space 비밀값 `ANTHROPIC_API_KEY` 추가 후 **Restart** |
| 예전에 올린 문서가 사라짐 | 무료 Space는 저장 공간이 임시라 재시작 시 지워집니다 |

## 이 구성의 검증 범위

- **확인한 것:** 같은 `Dockerfile`로 만든 이미지를 Hugging Face와 같은 조건으로 실행했습니다(일반 사용자 uid 1000, 영구 디스크 없음, 비밀값 2개만 지정). 로그인, 업로드, OCR, PDF 생성이 동작했습니다.
- **확인하지 못한 것:** 실제 Hugging Face 업로드·빌드와 GitHub Actions 실행은 개발 환경에서 huggingface.co 접속이 막혀 있어 해보지 못했습니다. 오류가 나면 Actions 로그나 Space 로그의 마지막 몇 줄을 알려주세요.
