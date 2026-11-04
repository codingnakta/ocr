# Windows 프로그램 (설치형 exe)

인터넷 서버 없이 **내 PC에서 실행하는 프로그램**입니다. 기능은 웹 버전과 같습니다(업로드, AI·하이브리드·무료 OCR, 검수·수정, 검색 가능한 PDF, 200쪽 이어하기, 비용 상한). 가입, 결제 수단, Docker가 모두 필요 없습니다.

## 1. 내려받기

https://github.com/codingnakta/ocr/releases/latest 에서 둘 중 하나를 받습니다.

| 파일 | 용도 |
|---|---|
| **`Pilgibot-Setup.exe`** (권장) | 설치 프로그램. 관리자 권한 없이 설치되고, 시작 메뉴와 바탕화면 바로가기를 만듭니다 |
| `Pilgibot-Windows-portable.zip` | 설치 없이 쓰는 방식. 압축을 풀고 폴더 안의 `Pilgibot.exe`를 실행합니다 (USB 등에 넣어 다녀도 됨) |

## 2. 설치·실행

1. `Pilgibot-Setup.exe`를 실행합니다.
   - **"Windows의 PC 보호" 파란 창이 나오면:** **추가 정보 → 실행**을 누르세요. 코드 서명 인증서(유료)가 없는 프로그램이라 나오는 경고입니다.
2. 설치가 끝나면 **필기봇 OCR Studio**가 실행됩니다.
   - 검은 창(콘솔)이 하나 뜨고, 잠시 뒤 브라우저에 앱이 열립니다.
   - **검은 창을 닫으면 프로그램이 종료됩니다.** 쓰는 동안에는 최소화해 두세요.
3. 처음 한 번 오른쪽 위 **설정**에서 **Claude API 키**를 입력하고 **저장**합니다.
   - 키는 이 PC에만 저장됩니다.
   - 키 없이도 **무료 OCR** 모드는 쓸 수 있습니다.
4. 다음부터는 바탕화면의 **필기봇 OCR Studio**를 더블클릭하면 됩니다.

브라우저 주소는 `http://127.0.0.1:8765/` 입니다. 이 PC에서만 열리고, 다른 기기에서는 접속할 수 없습니다.

## 3. 다른 컴퓨터에서 쓰기

같은 파일을 받아 그 컴퓨터에도 설치하면 됩니다. 단, **문서와 API 키는 컴퓨터마다 따로 저장**되며 서로 공유되지 않습니다.

## 4. 데이터 위치

| 내용 | 위치 |
|---|---|
| 문서·사진·결과 PDF·DB | `%LOCALAPPDATA%\Pilgibot` (탐색기 주소창에 그대로 붙여넣기) |
| API 키 | 같은 폴더의 `config.json` |
| 프로그램 | `%LOCALAPPDATA%\Programs\Pilgibot` |

- **삭제:** 설정 → 앱 → "필기봇 OCR Studio" → 제거. 데이터 폴더는 남으므로, 완전히 지우려면 `%LOCALAPPDATA%\Pilgibot`도 삭제하세요.
- **백업·이전:** `%LOCALAPPDATA%\Pilgibot` 폴더를 복사하면 됩니다.

## 5. 문제 해결

| 증상 | 해결 |
|---|---|
| 브라우저가 열리지 않음 | 검은 창에 나온 주소(`http://127.0.0.1:8765/`)를 브라우저에 직접 입력 |
| "이미 실행 중입니다" | 다른 검은 창이 이미 떠 있습니다. 브라우저만 다시 열면 됩니다 |
| 백신이 차단 | 서명되지 않은 새 프로그램이라 그럴 수 있습니다. 공식 Releases에서 받은 파일인지 확인하고 허용하세요 |
| AI·하이브리드 모드 버튼이 비활성 | 설정에서 API 키를 저장하세요 |
| 설정의 "한글 인식"이 "미지원" | 무료 OCR에서 한글을 못 읽는 상태입니다(빌드 때 한국어 모델을 받지 못함). AI·하이브리드 모드는 정상입니다 |

## 6. 만들어지는 과정 (개발자용)

GitHub Actions(`.github/workflows/build-windows.yml`)가 브랜치에 푸시할 때마다 Windows 서버에서 다음을 자동으로 수행합니다.

1. 화면을 정적 파일로 빌드합니다.
2. OCR 모델(한국어 인식 포함)을 받습니다.
3. PyInstaller로 exe를 만듭니다.
4. **만들어진 exe를 실제로 실행해 업로드 → OCR → PDF 생성까지 시험합니다.**
5. 설치 파일과 zip을 Releases에 올립니다.

로컬에서 직접 만들 때는 아래 순서로 실행합니다(Windows).

```bash
cd frontend && npm ci && STATIC_EXPORT=1 npm run build
```

```bash
cd backend && pip install -r requirements.txt pyinstaller==6.22.3 && python scripts/fetch_assets.py models
```

```bash
pyinstaller pilgibot.spec --noconfirm
```
