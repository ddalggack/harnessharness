# Ddalggack CTF Harness

Ddalggack은 CTFd의 미해결 문제를 가져와 Codex Worker에 분배하는 Python 하네스다.

- `1 Challenge = 1 Worker = 1 Codex Thread = 1 model`
- 최대 3개 Worker 병렬 실행
- 초과 문제는 FIFO queue에서 대기
- CLI와 로컬 웹 Dashboard 제공
- CTFd 첨부 파일 다운로드 및 접속 정보 전달
- 선택적 Flag 자동 제출
- 실행 이벤트와 최신 상태를 `runs/`에 기록

> 허가받은 CTF 및 보안 실습 환경에서만 사용한다. Worker는 로컬 시스템에서 명령을 실행하므로 신뢰할 수 없는 Challenge를 실행할 때는 별도 VM 또는 컨테이너 격리를 권장한다.

## 1. 요구 사항

- Python 3.11 이상
- Codex를 사용할 수 있는 ChatGPT 계정 또는 OpenAI API key
- 실제 CTF 실행 시 접근 가능한 CTFd 서버와 Access Token
- Challenge 설명에 명시된 Web/Pwn 서비스가 실행 중이며 로컬 머신에서 도달 가능해야 함

기본 Worker 모델은 `gpt-5.4`다.

## 2. 설치

반드시 **이 저장소의 루트**, 즉 `pyproject.toml`이 있는 디렉터리에서 설치한다.

### Windows PowerShell: venv 권장

```powershell
cd D:\path\to\Team_Ddalggack

py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Windows PowerShell: Conda

```powershell
cd D:\path\to\Team_Ddalggack
conda env create -f environment.yml
conda activate ddalggack
```

이미 환경이 존재하면 다음 명령으로 현재 저장소를 다시 연결한다.

```powershell
conda activate ddalggack
cd D:\path\to\Team_Ddalggack
python -m pip install -e ".[dev]"
```

### Linux/macOS

```bash
cd /path/to/Team_Ddalggack
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

설치 결과를 확인한다.

```powershell
python -c "import sys, ctf_harness, openai_codex; print(sys.executable); print(ctf_harness.__file__); print(openai_codex.__file__)"
ddalggack --help
```

`ctf_harness.__file__`은 현재 복제한 `Team_Ddalggack` 저장소를 가리켜야 한다. 다른 checkout을 가리킨다면 현재 저장소에서 `python -m pip install -e ".[dev]"`를 다시 실행한다.

## 3. Codex 연결

### ChatGPT 계정 로그인

```powershell
codex login
codex login status
```

브라우저에서 로그인 및 권한 승인을 완료한다. 정상 상태는 다음과 유사하다.

```text
Logged in using ChatGPT
```

### OpenAI API key 로그인

PowerShell:

```powershell
$env:OPENAI_API_KEY = "<OpenAI API key>"
$env:OPENAI_API_KEY | codex login --with-api-key
```

Linux/macOS:

```bash
export OPENAI_API_KEY='<OpenAI API key>'
printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key
```

### 실제 SDK 연결 확인

다음 명령은 실제 Codex Worker 하나를 호출하므로 모델 사용량이 발생할 수 있다.

```powershell
ddalggack codex-demo --model gpt-5.4
```

`ModuleNotFoundError: No module named 'openai_codex'`가 발생하면 프로젝트를 설치한 환경이 아닌 다른 Python으로 실행한 것이다. 다음 두 명령이 같은 가상환경을 가리키는지 확인한다.

```powershell
python -c "import sys; print(sys.executable)"
python -m pip show openai-codex ddalggack-ctf-harness
```

## 4. 빠른 동작 확인

Codex나 CTFd를 호출하지 않는 오프라인 smoke test:

```powershell
ddalggack smoke --workers 3
```

정상 실행 시 3개 Demo Worker가 완료되고 `active_workers_after_cleanup`이 `0`으로 출력된다.

## 5. CLI로 CTFd 실행

### CTFd 준비 조건

하네스를 시작하기 전에 다음을 확인한다.

1. CTFd API가 접근 가능하다.
2. CTFd에서 발급한 Access Token이 있다.
3. 각 Challenge의 `connection_info`가 Worker 머신에서 도달 가능한 주소를 가리킨다.
4. Web/Pwn Challenge 서비스가 실제로 실행 중이다.
5. Rev 등 첨부 파일 기반 문제는 CTFd에서 파일을 다운로드할 수 있다.

이 저장소는 CTFd 서버나 문제별 Docker 서비스를 자동으로 시작하지 않는다. 서버 기동 방법은 사용하는 CTFd/Challenge 배포 저장소를 따른다.

### PowerShell

```powershell
cd D:\path\to\Team_Ddalggack
.\.venv\Scripts\Activate.ps1

$env:CTFD_TOKEN = "<CTFd Access Token>"

ddalggack ctfd-run `
  --url http://127.0.0.1:8000 `
  --model gpt-5.4 `
  --max-swarms 3 `
  --runs-root runs `
  --run-id competition-001 `
  --timeout 30 `
  --progress
```

Conda를 사용하면 활성화 부분만 바꾼다.

```powershell
conda activate ddalggack
```

### Linux/macOS

```bash
source .venv/bin/activate
export CTFD_TOKEN='<CTFd Access Token>'

ddalggack ctfd-run \
  --url http://127.0.0.1:8000 \
  --model gpt-5.4 \
  --max-swarms 3 \
  --runs-root runs \
  --run-id competition-001 \
  --timeout 30 \
  --progress
```

`ctfd-run`은 시작 시점의 미해결 Challenge snapshot을 한 번 처리한다. 이미 `solved_by_me`인 문제는 제외한다. 최대 3개 Worker가 병렬 실행되고 나머지는 FIFO 순서로 대기한다.

### Flag 자동 제출

기본값은 자동 제출 비활성화다. 활성화하려면 `--submit-flags`를 추가한다.

```powershell
$env:CTFD_TOKEN = "<CTFd Access Token>"

ddalggack ctfd-run `
  --url http://127.0.0.1:8000 `
  --model gpt-5.4 `
  --max-swarms 3 `
  --run-id competition-submit `
  --submit-flags `
  --max-wrong-submissions 3 `
  --progress
```

자동 제출 흐름:

1. Worker가 `flag_candidate`를 보고한다.
2. Coordinator의 `SubmissionBroker`가 CTFd에 제출한다.
3. 정답이면 Worker를 완료 처리한다.
4. 오답이면 같은 Codex Thread에 결과를 피드백하고 분석을 계속한다.
5. 같은 후보는 중복 제출하지 않으며, 설정한 오답 제한에 도달하면 해당 Worker를 실패 처리한다.

Worker에는 CTFd Token을 전달하지 않는다. Token은 Platform Adapter와 Submission Broker 경계에서만 사용한다. Token을 `--token`으로 직접 전달할 수도 있지만 shell history에 남으므로 `CTFD_TOKEN` 환경 변수를 권장한다.

### 주요 `ctfd-run` 옵션

| 옵션 | 기본값 | 설명 |
|---|---:|---|
| `--url` | 필수 | CTFd base URL |
| `--token` | `CTFD_TOKEN` | CTFd Access Token |
| `--model` | `gpt-5.4` | 모든 Worker가 사용할 단일 모델 |
| `--max-swarms` | `3` | 동시 Worker 수, 1~3 |
| `--runs-root` | `runs` | workspace와 이벤트 저장 루트 |
| `--run-id` | `ctfd-run` | Run 식별자 |
| `--timeout` | `30` | CTFd HTTP timeout(초) |
| `--submit-flags` | 꺼짐 | Flag 후보 자동 제출 |
| `--max-wrong-submissions` | `3` | 문제별 오답 제출 제한 |
| `--heartbeat-interval` | `15` | 장시간 Codex turn 생존 이벤트 간격(초) |
| `--progress` | 기본 | 사람이 읽는 간결한 진행 출력 |
| `--progress-json` | - | 진행 이벤트를 JSONL로 출력 |
| `--quiet` | - | 콘솔 진행 출력 비활성화 |

전체 옵션은 다음 명령으로 확인한다.

```powershell
ddalggack ctfd-run --help
```

## 6. Worker 상세 로그 확인

기본 진행 화면의 `[1]`, `[2]`, `[3]`은 누적 Worker ID가 아니라 재사용되는 실행 슬롯 번호다.

다른 터미널에서 Worker 1 슬롯의 상세 활동을 실시간으로 본다.

```powershell
ddalggack seek worker 1 --run-id competition-001
```

현재 기록만 출력하고 종료:

```powershell
ddalggack seek worker 1 --run-id competition-001 --no-follow
```

원본 JSONL 이벤트 출력:

```powershell
ddalggack seek worker 1 --run-id competition-001 --raw
```

## 7. Dashboard 사용

Dashboard를 실행한 Python 환경에도 `openai-codex`가 설치되어 있어야 한다.

```powershell
cd D:\path\to\Team_Ddalggack
.\.venv\Scripts\Activate.ps1
ddalggack dashboard --host 127.0.0.1 --port 8788 --runs-root runs
```

브라우저에서 다음 주소를 연다.

```text
http://127.0.0.1:8788
```

### Demo 모드

1. `안전 Demo` 선택
2. 분야 및 Worker 수 선택
3. `문제 불러오기` 클릭
4. `자동 풀이 시작` 클릭

Demo는 `DemoWorkerRunner`를 사용하며 Codex나 외부 CTFd를 호출하지 않는다.

### 로컬 CTFd 모드

1. `로컬 CTFd` 선택
2. CTFd URL과 Access Token 입력
3. 문제 분야와 동시 Worker 수 선택
4. 허가된 대상 확인 체크
5. 필요하면 `플래그 자동 제출` 활성화
6. `문제 불러오기` 클릭
7. 문제 목록과 연결 상태 확인
8. `자동 풀이 시작` 클릭

Dashboard API는 localhost 및 사설망 CTFd 주소만 허용한다. CTFd 연결과 Worker 실행은 별도 단계이므로 `문제 불러오기`만 눌러서는 Worker가 시작되지 않는다.

현재 Dashboard CLI에는 모델 변경 옵션이 없으며 CTFd Worker는 코드의 기본 모델 `gpt-5.4`를 사용한다.

## 8. 실행 산출물

```text
runs/<run-id>/
├── events.jsonl
├── status.json
└── challenges/
    └── <challenge-id>/
        ├── challenge.json
        └── <CTFd 첨부 파일 및 Worker 생성물>
```

- `events.jsonl`: Worker 시작, reasoning summary, 도구 실행, 보고, 제출 결과 등 append-only 이벤트
- `status.json`: 가장 최근 Worker 상태 snapshot
- `challenge.json`: CTFd에서 받은 제목, 카테고리, 설명, host, port
- 최종 CLI stdout: Worker 상태와 전체 structured report JSON

Flag 후보와 명령 출력이 이벤트에 포함될 수 있으므로 `runs/` 접근 권한을 제한한다. `runs/*`는 Git ignore 대상이다.

## 9. 명령 요약

```text
ddalggack smoke [--workers {1,2,3}]
ddalggack codex-demo [--model MODEL] [--runs-root PATH]
ddalggack ctfd-run --url URL [options]
ddalggack seek worker NUMBER [options]
ddalggack dashboard [--host HOST] [--port PORT] [--runs-root PATH]
```

모듈로 직접 실행할 수도 있다.

```powershell
python -m ctf_harness.cli --help
```

정상적인 editable install 이후에는 별도 `PYTHONPATH` 설정이 필요 없다.

## 10. 문제 해결

### `ctfd-run`이 없고 `smoke`만 표시됨

다른 checkout이 editable install로 연결된 상태다.

```powershell
cd D:\path\to\Team_Ddalggack
python -m pip install -e ".[dev]"
python -c "import ctf_harness; print(ctf_harness.__file__)"
python -m ctf_harness.cli --help
```

### `No module named 'openai_codex'`

프로젝트를 설치한 가상환경과 실행 중인 Python이 다르다.

```powershell
python -c "import sys; print(sys.executable)"
python -m pip install -e ".[dev]"
python -c "import openai_codex; print(openai_codex.__file__)"
```

### Codex 인증 실패

```powershell
codex login status
codex login
```

ChatGPT 계정에서 사용할 수 없는 모델을 지정하면 Worker가 실패한다. 이 프로젝트의 기본값은 `gpt-5.4`다.

### 타깃 서비스 도달 실패

CTFd의 `connection_info`와 실제 서비스 주소/포트가 일치하는지 확인한다. CTFd 서버가 정상이어도 문제별 Docker 서비스가 내려가 있으면 Worker는 타깃에 도달할 수 없다.

### CTFd HTTP 500

하네스 오류로 단정하지 말고 CTFd 애플리케이션과 DB 로그를 먼저 확인한다. Docker Compose 환경이라면 예를 들어 다음을 확인한다.

```powershell
docker compose ps -a
docker compose logs --tail=200 ctfd db
```

MariaDB가 내려갔거나 Windows bind mount에서 I/O 오류가 발생하면 CTFd가 `Internal Server Error`를 반환할 수 있다. 데이터 삭제 명령인 `docker compose down -v`는 백업 없이 실행하지 않는다.

### Dashboard 수정이 반영되지 않음

Dashboard를 재시작하고 브라우저에서 `Ctrl+F5`로 강력 새로고침한다.

## 11. 테스트

```powershell
pytest -q
```

## 12. 현재 제한 사항

- `ctfd-run`은 시작 시점 snapshot을 한 번 처리한다. `run_live()`는 내부에 있지만 장기 실행 CLI로 노출되지 않았다.
- Repository는 인메모리이므로 프로세스 재시작 후 객체 상태는 복구되지 않는다. 단, 실행 중 기록한 `events.jsonl`, `status.json`, workspace는 남는다.
- Worker는 로컬 workspace와 Codex `workspace_write` sandbox에서 실행된다. Docker/Kubernetes 격리는 제공하지 않는다.
- Dashboard 상태는 로컬 프로세스 메모리에 있으므로 Dashboard 재시작 시 초기화된다.
- PostgreSQL, MinIO, Temporal 기반 영속 실행은 아직 연결되지 않았다.

## 13. 아키텍처 요약

```text
CTFdPlatformAdapter
        │
        ▼
PlatformPoller ── 미해결 snapshot
        │
        ▼
CtfRunWorkflow ── FIFO pending queue / 최대 3개
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
 Codex Worker 1   Codex Worker 2   Codex Worker 3
 1 Challenge      1 Challenge      1 Challenge
 1 Thread         1 Thread         1 Thread
        │
        ▼
WorkerReport(flag_candidate)
        │
        ▼ (옵션: --submit-flags)
SubmissionBroker ── 중복 방지 / 오답 제한 ── CTFd submit
```

Coordinator는 풀이 전략을 모델로 결정하지 않는다. Challenge 배정, FIFO scheduling, report 저장, 제출 정책과 lifecycle만 결정론적 Python 코드로 관리한다.
