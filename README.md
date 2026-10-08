# Live Interpreter

영어와 한국어 발표나 회의를 실시간 자막으로 보여 주는 애플리케이션입니다. PDF의 글자와 이미지를 분석해 통역 맥락을 만들고, 종료 후 기록과 요약 문서를 저장할 수 있습니다.

- 마이크, USB 오디오, 브라우저 탭 소리와 녹음 파일 입력
- 발표 화면, 별도 자막 창, 참석자 휴대폰 화면과 OBS 연결
- 발표 PDF 1개와 참고 PDF 최대 4개의 맥락 분석과 근거별 보정
- 비공개 통역 테스트, 일시정지와 재개, 질의응답
- 전체 기록: HTML, Word, CSV, Markdown
- 발표 요약과 회의록: HTML, Word, Markdown, 별도 검토 내역

사용자별 AWS 계정과 자격 증명이 필요합니다. 소스를 내려받는 것만으로 AWS에 배포되거나 다른 사람의 실행 환경에 연결되지는 않습니다.

프로젝트 소스는 [Apache 2.0](LICENSE) 라이선스입니다. 외부 라이브러리는 각자의 라이선스를 따릅니다. [의존성 고지](THIRD_PARTY_NOTICES.md)를 참고하세요.

## 로컬 실행

Python 3.12, [uv](https://docs.astral.sh/uv/), Pandoc과 AWS 자격 증명을 준비합니다. 브라우저는 마이크 권한을 허용해야 합니다.

Pandoc은 기본 서식이 실행 파일에 포함된 공식 배포본을 사용합니다. Linux 배포판의 일부 패키지는 Word 보안 변환을 지원하지 않습니다. 설치 스크립트가 이를 확인하며, 컨테이너는 Pandoc 3.12 공식 배포본을 사용합니다.

```bash
tools/bootstrap.sh
aws login
.venv/bin/python setup_event.py --dry-run
.venv/bin/python setup_event.py
./start.sh --port 8083
```

`http://localhost:8083`을 엽니다. `setup_event.py`는 기본 세션의 Transcribe vocabulary와 Translate terminology를 생성합니다. 이를 생략하고 시험하려면 `./start.sh --port 8083 --no-vocabulary`를 사용합니다.

로컬 서버는 기본적으로 루프백 주소에서 실행됩니다. AWS 호출은 로컬 Python 프로세스의 자격 증명을 사용합니다. AWS에 배포하면 ECS 태스크 역할을 사용합니다.

다른 출처의 브라우저 요청은 차단합니다. `--host 0.0.0.0`처럼 외부에서 접근할 수 있는 주소를 쓰려면 `OPERATOR_PASSWORD`가 필요합니다. 비밀번호와 운영자 링크를 공유 파일에 넣지 마세요.

주요 옵션은 `--event`, `--profile`, `--region`, `--model`, `--engine translate`, `--no-vocabulary`입니다. 기본 모델 ID는 `settings.py`에 있으며 사용하는 계정과 리전에서 해당 모델을 사용할 수 있어야 합니다.

## 사용 흐름

1. 필요하면 PDF를 올립니다. 분석 중에도 시작할 수 있으며 완료된 맥락은 다음 발화부터 반영됩니다.
2. 음성 입력을 고르고 **통역 테스트**로 실제 입력과 번역을 확인합니다. 이 테스트는 청중 화면과 본 세션 기록에 남지 않습니다.
3. 자막 모양을 확인하고 **통역 시작**을 누릅니다.
4. 공유 메뉴에서 자막 창이나 참석자 링크를 엽니다. 운영자 링크는 세션을 제어할 수 있으므로 참석자에게 전달하지 않습니다.
5. 종료 후 왼쪽에서 전체 기록을 받습니다. 오른쪽에서 발표 요약 또는 회의록과 언어를 선택해 **정리하기**를 누릅니다.
6. **문서 열기** 또는 **HTML 받기**로 서식이 포함된 문서를 엽니다. HTML은 외부 글꼴이나 스크립트 없이 동작하며 모바일과 인쇄를 지원합니다.
7. 필요한 기록을 저장한 뒤 **새 세션 준비**를 누릅니다.

정리 문서는 요약과 주제별 핵심 내용을 담습니다. 결정 사항, 후속 작업, 남은 논의는 실제 내용이 있을 때만 표시합니다. 인사, 반복, 오인식 교정 과정은 본문에서 제외합니다. 조치 대상이나 담당자처럼 결과에 영향을 주는 불확실성은 남깁니다. 원문 근거와 인식 오류 검토는 **검토 내역**에서 별도로 받을 수 있습니다.

## Codex로 문서 정리

저장소에는 [session-writeup 스킬](.agents/skills/session-writeup/SKILL.md)이 포함되어 있습니다. 이 폴더에서 Codex에 다음처럼 요청할 수 있습니다.

```text
$session-writeup으로 이 세션 기록을 읽고 핵심 내용만 담은 회의록과 HTML을 만들어줘.
```

실제 기록 파일을 함께 지정합니다. 앱과 스킬은 같은 [작성 규칙](postprocess/writeup-policy.md)을 참조합니다. 스킬은 기존 Python 생성기와 HTML 서식을 사용하며 별도 MCP 설정이 필요하지 않습니다. 앱의 정리하기 기능은 Codex 없이도 동작합니다.

## 자료와 기록

PDF는 파일당 40MiB, 300쪽, 추출 텍스트 30만 자, 분석 15분 한도입니다. PowerPoint는 PDF로 저장해서 올립니다. 참고 자료는 통역 맥락에만 쓰며 참석자 화면에는 나오지 않습니다.

전체 기록은 AI가 다시 쓰지 않습니다. 받아쓴 발화는 자막이 나가지 않았더라도 모두 남고, 번역 실패나 건너뜀 같은 상태가 함께 적힙니다. 자료에 있는 사실을 실제 발언으로 간주하거나, 자료에 나온 인물을 현재 발표자로 자동 등록하지 않습니다. 필요한 표기 보정은 분석 결과 또는 고급 설정에서 할 수 있습니다.

자료와 기록은 실행 중인 서버의 디스크와 메모리에 보관됩니다. 새 세션 준비나 서버 재시작으로 사라질 수 있습니다. 데이터 처리와 운영상의 제한은 [운영 안내](docs/operations.md)에 정리했습니다.

## 행사 설정

`events/example.toml`을 새 이름으로 복사해 사람, 질문과 용어를 설정합니다. 이 예시는 가상의 행사입니다. `events/_base.toml`과 `glossary/`의 공통 용어는 모든 세션에 적용됩니다.

```bash
.venv/bin/python setup_event.py example --dry-run
./start.sh --event example --no-vocabulary --port 8083
```

## AWS 배포

[아키텍처](docs/architecture.md)와 [배포 및 운영 안내](docs/operations.md)를 참고합니다. CloudFront와 WAF, 내부 ALB, ECS Fargate 및 VPC endpoint를 생성합니다. 실행하지 않는 시간에도 인프라 비용이 발생합니다.

실시간 통역은 Transcribe, Bedrock과 Translate를 사용하고, 자료 분석과 정리는 Bedrock을 추가 호출합니다. 실제 비용은 리전, 모델, 자료 크기와 사용 시간에 따라 달라집니다.

## 개발과 공유

```bash
tools/bootstrap.sh --dev
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python tests/test_commit.py
.venv/bin/python tests/test_studio_browser.py
.venv/bin/python tests/test_writeup_browser.py
.venv/bin/python tests/test_studio_controls_browser.py
.venv/bin/python tests/test_login_browser.py
```

브라우저 검사는 시스템 Chrome과 별도 테스트 서버를 사용합니다. AWS 실제 호출을 포함하지 않습니다. 화면 규칙은 [DESIGN.md](DESIGN.md), 변경 기록은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.

공개용 소스는 다음 명령으로 만듭니다. 실행에 필요한 소스, 일반화한 설정 예제, 문서와 테스트만 포함합니다. 로컬 Git 이력, 개인 설정, 행사 자료, 사용자 기록과 번들 글꼴은 포함하지 않습니다.

```bash
python3 tools/prepare_release.py
```

결과는 `out/releases/`에 생성됩니다. 새 공개 Git 이력을 만들려면 `--init-git --git-name <공개이름> --git-email <GitHub-noreply주소>`를 추가합니다. 이메일은 GitHub 이메일 설정에서 확인합니다. 전역 Git 설정의 개인 또는 회사 이메일을 자동으로 사용하지 않습니다.

게시 전에는 `python3 tools/prepare_release.py --verify-public-git <공개저장소폴더>`로 과거 파일과 커밋 작성자까지 검사합니다. GitHub 게시나 Slack 전송은 이 명령에서 수행하지 않습니다. 버전 관리와 기여 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고합니다.
