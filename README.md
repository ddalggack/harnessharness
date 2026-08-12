# Ddalggack CTF Harness

Ddalggack은 여러 CTF 문제를 Codex Agent에게 나누어 맡기고 실행 상태를 관리하는 Python 하네스다.

코드 기반 Run Coordinator는 대회 전체 흐름을 관리하고, 각 Swarm은 할당된 문제 하나를 독립적으로 풀이한다. Coordinator는 Worker의 명령, 도구 사용, 풀이 전략을 결정하지 않는다. Challenge 정보를 그대로 WorkerAssignment에 담고 Worker report와 수명주기만 기록한다.

> 현재 버전은 일반 Python 코드 기반 Coordinator, Codex Python SDK 기반 Worker, 5초 Poller, FIFO pending queue, 최대 3개 Swarm 동시 실행을 구현한다. CTFd 연동과 Docker 격리, 영구 저장소는 아직 구현하지 않았다.

## 핵심 원칙

- 하나의 Challenge에는 하나의 Swarm을 배정한다.
- 하나의 Swarm은 하나의 Codex Thread와 하나의 모델만 사용한다.
- Coordinator는 모델을 사용하지 않고 scheduling과 lifecycle을 일반 코드로 처리한다.
- 동시에 실행하는 Swarm은 최대 3개다.
- 실행 슬롯이 없으면 Challenge를 FIFO pending queue에 보관한다.
- Worker는 Challenge 정보를 해석하고 자체 풀이 전략을 세운 뒤 의미 있는 상태 변화만 보고한다.
- Worker 실행 리소스를 정리해도 report와 workspace는 보존한다.
- Flag는 Worker가 플랫폼에 직접 제출하지 않고 Submission Broker 경계를 거친다.

## 아키텍처

```text
CTF Platform Adapter
        │
        ▼
Platform Poller (기본 5초)
        │
        ├─ new_challenge
        └─ challenge_solved
        │
        ▼
Deterministic Run Coordinator
        │
        ▼
FIFO Pending Queue
        │
        ├───────────────┬───────────────┐
        ▼               ▼               ▼
    Swarm #1        Swarm #2        Swarm #3
    Challenge A     Challenge B     Challenge C
    단일 모델        단일 모델        단일 모델
    Codex Thread     Codex Thread     Codex Thread
    Workspace A      Workspace B      Workspace C
```

### Platform Poller

`PlatformPoller`는 CTF 플랫폼의 상태를 주기적으로 조회한다.

- 기본 조회 간격은 5초다.
- 시작 시 현재 Challenge와 solved 상태를 조용히 seed한다.
- 이후 새 Challenge를 `new_challenge` 이벤트로 만든다.
- 새 solve를 `challenge_solved` 이벤트로 만든다.
- 같은 상태를 다시 조회해도 중복 이벤트를 만들지 않는다.
- 조회에 실패하면 마지막 정상 상태를 유지한다.

Poller는 모델 선택이나 Worker 생성 정책을 결정하지 않는다. 플랫폼 상태를 내부 이벤트로 바꾸는 역할만 담당한다.

### 코드 기반 Run Coordinator

`MainAgentRuntime`과 `CtfRunWorkflow`가 모델 없이 Run을 조정한다.

- Challenge 정보를 `WorkerAssignment`에 그대로 복사한다.
- FIFO pending queue와 활성 Swarm 수를 관리한다.
- 중복 Worker 생성을 방지한다.
- Worker report와 상태를 Repository와 EventBus에 기록한다.
- 외부 solve에 따라 pending Challenge를 제거하거나 활성 Swarm을 취소한다.
- Run 종료 시 Worker task와 Poller runtime을 정리한다.

Coordinator에는 `AsyncCodex` client나 공유 Coordinator Thread가 없다. 따라서 병렬 report를 모델 turn으로 직렬화하거나 Coordinator SDK runtime을 정리할 필요도 없다.

### Swarm과 Worker

하나의 Swarm은 다음 실행 단위를 의미한다.

```text
1 Swarm = 1 Challenge = 1 Worker = 1 Model = 1 Codex Thread
```

`CodexWorkerRunner`는 전달받은 Challenge 제목, 카테고리, 설명, 접속 정보와 workspace를 해석한다. Worker가 자체 분석 계획과 풀이 전략을 정한 뒤 다음 report 중 하나를 구조화된 JSON으로 반환한다.

- `checkpoint`: 의미 있는 중간 상태
- `completed`: 풀이와 검증을 마친 상태
- `failed`: Worker가 자율적으로 해결할 수 없는 장애를 포함한 종료 실패 상태

`checkpoint` 이후에는 Worker가 같은 Thread에서 자율적으로 계속한다. 해결 경로가 없으면 `failed`를 반환한다. `completed`나 `failed`가 발생하면 Worker SDK client와 Thread runtime을 정리한다.

### Pending queue와 동시 실행 제한

`CtfRunWorkflow`는 명시적인 FIFO pending queue를 관리한다.

1. 미해결 Challenge를 pending queue에 넣는다.
2. 실행 슬롯이 있으면 queue 앞에서 Challenge를 꺼낸다.
3. Worker record와 Codex runtime을 생성한다.
4. 활성 Swarm이 3개면 추가 Challenge는 queue에서 기다린다.
5. Swarm 하나가 끝나면 다음 Challenge를 시작한다.
6. pending Challenge가 외부에서 solved되면 queue에서 제거한다.
7. 실행 중인 Challenge가 외부에서 solved되면 해당 Swarm을 취소한다.

`LocalWorkerScheduler`도 `asyncio.Semaphore`로 동시 실행 수를 한 번 더 제한한다. 설정 가능한 범위는 1개부터 3개까지다.

## Worker 수명주기

```text
CREATED
   │
   ▼
RUNNING
   │
   ├─ CHECKPOINT → 같은 Thread에서 계속 실행
   │
   ├─ COMPLETED / FAILED → 최종 결과 상태 보존
   │
   └─ 외부 solve 또는 Run 취소 → TERMINATED
```

정상적으로 끝난 Worker는 Scheduler task와 SDK runtime을 정리한 뒤에도 `COMPLETED` 또는 `FAILED` 상태를 유지한다. `TERMINATED`는 외부 solve나 Run 취소로 실행이 중단된 경우에만 사용한다. 인메모리 Repository는 전체 `WorkerReport`를 보존하므로 `kind`, `summary`, `artifacts`, `flag_candidate`를 runtime 정리 후에도 조회할 수 있다.

## Main–Worker 메시지

메시지 모델은 `src/ctf_harness/protocol/messages.py`에 정의되어 있다.

### WorkerAssignment

```json
{
  "type": "WorkerAssignment",
  "run_id": "run-001",
  "worker_id": "run-001-pwn-001",
  "challenge_id": "pwn-001",
  "challenge_title": "baby-bof",
  "challenge_category": "pwn",
  "challenge_description": "제공된 ELF의 취약점을 분석하고 flag를 획득한다.",
  "workspace_uri": "runs/run-001/challenges/pwn-001",
  "model": "gpt-5.4",
  "host": "ctf.example",
  "port": 31337
}
```

### WorkerReport

```json
{
  "type": "WorkerReport",
  "run_id": "run-001",
  "worker_id": "run-001-pwn-001",
  "challenge_id": "pwn-001",
  "kind": "failed",
  "summary": "제공된 libc와 실행 환경이 일치하지 않고 자율적으로 복구할 경로가 없다.",
  "artifacts": ["probe.py", "notes.md"],
  "flag_candidate": null
}
```

## 프로젝트 구조

```text
.
├── src/ctf_harness/
│   ├── app.py                 # 실제 Codex 하네스 조립
│   ├── cli.py                 # 로컬 smoke 명령
│   ├── poller.py              # 플랫폼 상태 감시
│   ├── domain/                # Challenge와 Worker 상태 모델
│   ├── events/                # 인프로세스 EventBus
│   ├── main_agent/
│   │   └── runtime.py         # 코드 기반 assignment, report, 수명주기 관리
│   ├── platforms/
│   │   ├── base.py            # PlatformAdapter Protocol
│   │   ├── ctfd.py            # 미구현 CTFd 경계
│   │   └── memory.py          # 테스트용 인메모리 Adapter
│   ├── protocol/              # Main–Worker 메시지
│   ├── scheduler/             # 최대 3개 로컬 Scheduler
│   ├── storage/               # 인메모리 Repository와 로컬 Object Store
│   ├── submissions/           # Flag 제출 경계
│   ├── worker/
│   │   ├── codex.py           # 단일 모델 Codex Worker
│   │   └── runner.py          # WorkerRunner Protocol과 Demo Worker
│   └── workflows/             # snapshot/live CTF workflow
├── tests/
├── runs/
├── environment.yml
├── pyproject.toml
└── README.md
```

## 현재 구현 범위

| 컴포넌트 | 현재 구현 | 남은 작업 |
|---|---|---|
| Coordinator | 일반 Python 코드 기반 scheduling과 lifecycle | 재시작 후 Run 복구 |
| Worker | Challenge당 단일 모델 Codex Thread | Docker/Pod 내부 실행 |
| Poller | 5초 snapshot 비교와 이벤트 생성 | 플랫폼별 cursor 또는 webhook |
| Queue | FIFO pending queue | 우선순위 정책 |
| Scheduler | 최대 3개 로컬 비동기 실행 | Docker/Kubernetes Scheduler |
| Platform | Protocol, Memory Adapter, CTFd placeholder | 실제 CTFd API 연결 |
| Repository | 전체 WorkerReport를 보존하는 인메모리 Worker record | PostgreSQL 저장소 |
| Object Store | 로컬 workspace | S3/MinIO 연결 |
| Events | 인프로세스 EventBus | Event API와 SSE |
| Flag 제출 | Submission Broker 경계 | 검증, 중복 방지, rate limit |

## 설치

### 요구 사항

- Miniconda 또는 Python 3.11 이상
- Codex 로그인 세션 또는 Codex API 인증

### Conda 환경 생성

프로젝트 루트에서 다음 명령을 실행한다.

```bash
cd Team_Ddalggack
conda env create -f environment.yml
conda activate ddalggack
```

`environment.yml`은 프로젝트를 editable mode와 개발 의존성(`pytest`, `build`)까지 함께 설치한다. 환경 생성 후 `ddalggack` 명령을 바로 사용할 수 있다.

환경 파일이 변경된 경우 다음 명령으로 동기화한다.

```bash
conda env update -f environment.yml --prune
conda activate ddalggack
```

`pyproject.toml`에는 공식 Python SDK인 `openai-codex`가 포함되어 있다.

Conda를 사용하지 않는 경우 Python 3.11 이상의 가상 환경에서 다음과 같이 설치할 수 있다.

```bash
python -m pip install -e ".[dev]"
```

## 실행

### 오프라인 smoke

다음 명령은 `MemoryPlatformAdapter`와 `DemoWorkerRunner`를 사용한다. 실제 CTF를 풀거나 Codex API를 호출하지 않고 Worker 생성, 보고, 종료 흐름만 검사한다.

```bash
conda activate ddalggack
ddalggack smoke --workers 3
```

`--workers`에는 1부터 3까지 지정할 수 있다. 생략하면 3개를 사용한다.

모듈을 직접 실행할 수도 있다.

```bash
python -m ctf_harness.cli smoke --workers 3
```

정상 실행 시 `completed_workers`가 3이고 `active_workers_after_cleanup`이 0으로 나온다.

```json
{
  "run_id": "smoke-run",
  "challenges": 3,
  "completed_workers": 3,
  "active_workers_after_cleanup": 0,
  "events": 11,
  "worker_states": {
    "smoke-run-pwn-1": "completed",
    "smoke-run-rev-1": "completed",
    "smoke-run-web-1": "completed"
  }
}
```

### Codex API 호출 데모

다음 명령은 `DemoWorkerRunner`가 아니라 실제 `CodexWorkerRunner`를 실행한다. 로컬 Codex 로그인 세션을 사용해 Codex Thread 하나를 생성하고, API 연결 확인용 Challenge 하나를 전달한 뒤 structured `WorkerReport`를 인메모리에 저장한다.

먼저 인증 상태를 확인한다.

```bash
codex login status
```

로그인되어 있지 않으면 ChatGPT 계정으로 로그인한다.

```bash
codex login
```

OpenAI API key를 사용하려면 환경 변수의 값을 stdin으로 전달한다.

```bash
export OPENAI_API_KEY="..."
printf '%s' "$OPENAI_API_KEY" | codex login --with-api-key
```

인증 후 실제 Codex Worker 데모를 실행한다.

```bash
conda activate ddalggack
ddalggack codex-demo --model gpt-5.4
```

모듈로 직접 실행할 수도 있다.

```bash
python -m ctf_harness.cli codex-demo --model gpt-5.4
```

workspace 위치를 바꾸려면 `--runs-root`를 지정한다.

```bash
ddalggack codex-demo --model gpt-5.4 --runs-root runs
```

정상 실행 시 프로세스는 exit code `0`을 반환하고 Worker 상태와 전체 report를 JSON으로 출력한다. 인증, 모델 또는 SDK 호출에 실패하면 exit code `1`을 반환하며 원인은 `failed` report의 `summary`에 기록된다. 이 명령은 실제 모델 사용량이 발생할 수 있다.

### 실제 Codex Worker 실행

`CTFdPlatformAdapter`는 CTFd v1 API를 실제로 호출한다. 다음 항목을 지원한다.

- `GET /api/v1/challenges`: Challenge 목록과 solve 상태 조회
- `GET /api/v1/challenges/{id}`: 설명, 접속 정보, 첨부 파일 URL 조회
- Challenge 상세 응답의 `files` URL: workspace로 첨부 파일 다운로드
- `POST /api/v1/challenges/attempt`: Flag 제출 및 `correct` 결과 확인

CTFd에서 Access Token을 발급한 뒤 환경 변수로 전달한다. Token을 명령행 인자나 저장소 파일에 직접 기록하지 않는 방식을 권장한다.

```bash
conda activate ddalggack
export CTFD_TOKEN="..."
ddalggack ctfd-run \
  --url https://ctf.example.com \
  --model gpt-5.4 \
  --max-swarms 3 \
  --run-id competition-001 \
  --runs-root runs
```

`--token`으로 Token을 직접 전달할 수도 있지만 shell history에 남을 수 있다.

```bash
ddalggack ctfd-run --url https://ctf.example.com --token "..."
```

`ctfd-run`은 시작 시점의 Challenge snapshot을 가져오고 이미 solve된 문제를 제외한 각 Challenge를 독립 Codex Worker에 배정한다. Challenge 첨부 파일과 `challenge.json`은 다음 경로에 저장한다.

```text
runs/<run-id>/challenges/<challenge-id>/
```

최대 3개 Worker를 병렬로 실행하며 초과 Challenge는 FIFO queue에서 기다린다. 실행이 끝나면 Worker 상태와 전체 structured report를 JSON으로 출력한다. 하나 이상의 Worker가 `failed` 또는 `terminated`이면 exit code `1`을 반환한다.

현재 `ctfd-run`은 **한 번의 snapshot 실행**이다. 대회 중 새 문제와 외부 solve를 계속 감시하는 `run_live()` workflow는 코드에 있지만 이를 노출하는 장기 실행 CLI 명령은 아직 추가하지 않았다.

`CTFdPlatformAdapter`는 `PlatformAdapter`의 다음 계약을 구현한다.

```python
async def list_challenges() -> list[Challenge]: ...
async def list_solved_challenge_ids() -> set[str]: ...
async def download_challenge(challenge, destination) -> Path: ...
async def submit_flag(challenge_id, flag) -> bool: ...
```

Worker의 `flag_candidate`는 report에 보존되지만 현재 workflow가 자동으로 `submit_flag()`를 호출하지는 않는다. 모델의 주장만으로 Flag를 제출하지 않기 위한 의도적인 경계다.

## 테스트와 빌드

전체 테스트를 실행한다.

```bash
pytest -q
```

패키지를 빌드한다.

```bash
python -m build
```

테스트는 다음 동작을 검증한다.

- Main–Worker 메시지 직렬화
- Poller seed와 new/solved 이벤트
- 중복 Poll 이벤트 방지
- 최대 3개 Swarm 동시 실행
- 4번째 이후 Challenge의 pending 처리
- 슬롯 반환 후 다음 Challenge 실행
- 외부 solve에 따른 pending 제거와 Swarm 취소
- LLM 없이 Challenge 데이터로 WorkerAssignment 생성
- Main runtime의 report 및 상태 기록
- CTFd Challenge 목록·상세·solve 상태 조회
- CTFd Token 인증, 첨부 파일 다운로드, Flag 제출 응답 처리
- Worker의 단일 모델 사용
- Worker의 Challenge 해석과 자체 전략 수립
- SDK runtime 오류의 `failed` report 변환
- 완료 후 Scheduler runtime 제거

## 제한 사항

- `ctfd-run`은 시작 시점의 CTFd snapshot만 처리하며 장기 polling CLI는 아직 제공하지 않는다.
- 현재 Repository와 EventBus는 인메모리 구현이라 프로세스를 재시작하면 사라진다.
- 현재 Worker는 로컬 workspace와 Codex `workspace_write` sandbox를 사용한다.
- Docker 또는 Kubernetes 수준의 격리는 아직 제공하지 않는다.
- Flag 자동 검증과 제출 정책은 아직 구현하지 않았다.
- Temporal, PostgreSQL, MinIO, Event API는 아직 연결하지 않았다.

신뢰할 수 없는 Challenge 파일을 실전에서 실행하기 전에는 Docker 기반 Worker Scheduler를 연결해야 한다.

## 로드맵

1. `run_live()`를 사용하는 장기 실행 CTFd polling CLI를 추가한다.
2. Docker 기반 Worker Scheduler와 카테고리별 Tool Image를 구현한다.
3. report와 transcript를 PostgreSQL에 영구 저장한다.
4. workspace와 artifact를 S3 또는 MinIO에 저장한다.
5. Submission Broker에 중복 방지와 rate limit을 추가한다.
6. Temporal을 사용해 장기 실행과 재시작 복구를 구현한다.
7. Event API와 Dashboard를 구현한다.

## 안전 원칙

- 명시적으로 허가된 CTF와 보안 실습 환경에만 사용한다.
- Worker에게 호스트 Docker socket을 직접 노출하지 않는다.
- 원본 Challenge 파일과 Worker 생성물을 분리한다.
- Worker가 플랫폼에 Flag를 직접 제출하지 않게 한다.
- 모델의 성공 주장만으로 solve를 인정하지 않는다.
- exploit 실행 결과와 artifact를 통해 실제 재현 여부를 확인한다.
- runtime을 종료해도 재현에 필요한 report와 artifact는 보존한다.
