# Ddalggack CTF Harness

Ddalggack은 여러 CTF 문제를 Codex Agent에게 나누어 맡기고 실행 상태를 관리하는 Python 하네스다.

Coordinator는 대회 전체 흐름을 관리하고, 각 Swarm은 할당된 문제 하나를 독립적으로 풀이한다. Coordinator가 Worker의 명령과 도구 사용을 단계별로 통제하지는 않는다. 최초 작업을 할당하고 Worker가 보내는 checkpoint, blocker, 완료 보고를 바탕으로 상위 수준의 피드백만 제공한다.

> 현재 버전은 Codex Python SDK 기반 Coordinator와 Worker, 5초 Poller, FIFO pending queue, 최대 3개 Swarm 동시 실행을 구현한다. CTFd 연동과 Docker 격리, 영구 저장소는 아직 구현하지 않았다.

## 핵심 원칙

- 하나의 Challenge에는 하나의 Swarm을 배정한다.
- 하나의 Swarm은 하나의 Codex Thread와 하나의 모델만 사용한다.
- Coordinator는 Codex 모델을 사용하며 Run 전체에서 같은 Thread를 유지한다.
- 동시에 실행하는 Swarm은 최대 3개다.
- 실행 슬롯이 없으면 Challenge를 FIFO pending queue에 보관한다.
- Worker는 의미 있는 상태 변화만 Coordinator에 보고한다.
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
Long-lived Codex Coordinator
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

### Codex Coordinator

`CodexCoordinatorBackend`는 공식 `openai-codex` Python SDK를 사용한다.

- Run 시작 시 `AsyncCodex` client와 Coordinator Thread를 한 번 생성한다.
- Challenge 정보로 `WorkerAssignment`를 작성한다.
- Worker report를 읽고 필요한 경우 `Feedback`을 반환한다.
- 병렬 Worker report가 같은 Thread에 동시에 들어가지 않도록 turn을 직렬화한다.
- Run이 끝나면 SDK client와 Thread runtime을 정리한다.

Coordinator는 Worker의 shell 명령이나 tool action을 매 단계 승인하지 않는다.

### Swarm과 Worker

하나의 Swarm은 다음 실행 단위를 의미한다.

```text
1 Swarm = 1 Challenge = 1 Worker = 1 Model = 1 Codex Thread
```

`CodexWorkerRunner`는 할당된 workspace에서 자율적으로 문제를 분석한다. Worker는 다음 report 중 하나를 구조화된 JSON으로 반환한다.

- `checkpoint`: 의미 있는 중간 상태
- `blocked`: 외부 판단이나 추가 정보가 필요한 상태
- `completed`: 풀이와 검증을 마친 상태
- `failed`: 복구할 수 없는 실패 상태

`checkpoint`나 `blocked` 보고에 Coordinator feedback이 있으면 같은 Worker Thread의 다음 turn에 전달한다. `completed`나 `failed`가 발생하면 Worker runtime을 정리한다.

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
   ├───────────────┐
   ▼               │
WAITING_FOR_FEEDBACK
   │               │
   ▼               │
FEEDBACK_SENT ─────┘
   │
   ▼
COMPLETED / FAILED
   │
   ▼
TERMINATED
```

`TERMINATED`는 실행 중인 task와 SDK runtime을 정리했다는 의미다. report, workspace, exploit, writeup 같은 결과물을 삭제했다는 의미는 아니다.

## Main–Worker 메시지

메시지 모델은 `src/ctf_harness/protocol/messages.py`에 정의되어 있다.

### WorkerAssignment

```json
{
  "type": "WorkerAssignment",
  "run_id": "run-001",
  "worker_id": "run-001-pwn-001",
  "challenge_id": "pwn-001",
  "objective": "문제를 분석하고 재현 가능한 exploit을 작성한다.",
  "workspace_uri": "runs/run-001/challenges/pwn-001",
  "profile": "pwn",
  "model": "gpt-5.4"
}
```

### WorkerReport

```json
{
  "type": "WorkerReport",
  "run_id": "run-001",
  "worker_id": "run-001-pwn-001",
  "challenge_id": "pwn-001",
  "kind": "blocked",
  "summary": "제공된 libc와 실행 환경의 libc가 일치하지 않는다.",
  "artifacts": ["probe.py", "notes.md"],
  "flag_candidate": null
}
```

### Feedback

```json
{
  "type": "Feedback",
  "run_id": "run-001",
  "worker_id": "run-001-pwn-001",
  "directive": "Dockerfile과 실제 프로세스의 loader와 libc를 비교한 뒤 계속한다."
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
│   │   ├── codex.py           # Codex Coordinator
│   │   └── runtime.py         # report와 feedback 수명주기
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
| Coordinator | 장기 `AsyncCodex` Thread | 재시작 후 Thread 복구 |
| Worker | Challenge당 단일 모델 Codex Thread | Docker/Pod 내부 실행 |
| Poller | 5초 snapshot 비교와 이벤트 생성 | 플랫폼별 cursor 또는 webhook |
| Queue | FIFO pending queue | 우선순위 정책 |
| Scheduler | 최대 3개 로컬 비동기 실행 | Docker/Kubernetes Scheduler |
| Platform | Protocol, Memory Adapter, CTFd placeholder | 실제 CTFd API 연결 |
| Repository | 인메모리 Worker record | PostgreSQL 저장소 |
| Object Store | 로컬 workspace | S3/MinIO 연결 |
| Events | 인프로세스 EventBus | Event API와 SSE |
| Flag 제출 | Submission Broker 경계 | 검증, 중복 방지, rate limit |

## 설치

### 요구 사항

- Miniconda 또는 Python 3.11 이상
- Codex 로그인 세션 또는 Codex API 인증

### Conda 환경 생성

```bash
conda env create -f environment.yml
conda activate ddalggack
```

환경 파일이 변경된 경우 다음 명령으로 동기화한다.

```bash
conda env update -f environment.yml --prune
conda activate ddalggack
```

`pyproject.toml`에는 공식 Python SDK인 `openai-codex`가 포함되어 있다.

## 실행

### 오프라인 smoke

다음 명령은 `MemoryPlatformAdapter`와 `DemoWorkerRunner`를 사용한다. 실제 CTF를 풀거나 Codex API를 호출하지 않고 Worker 생성, 보고, 종료 흐름만 검사한다.

```bash
ddalggack smoke --workers 3
```

모듈을 직접 실행할 수도 있다.

```bash
PYTHONPATH=src python -m ctf_harness.cli smoke --workers 3
```

정상 실행 시 `completed_workers`가 3이고 `active_workers_after_cleanup`이 0으로 나온다.

```json
{
  "run_id": "smoke-run",
  "challenges": 3,
  "completed_workers": 3,
  "active_workers_after_cleanup": 0,
  "events": 14,
  "worker_states": {
    "smoke-run-pwn-1": "terminated",
    "smoke-run-rev-1": "terminated",
    "smoke-run-web-1": "terminated"
  }
}
```

### 실제 Codex 하네스 조립

실제 실행은 `build_codex_harness()`에 `PlatformAdapter` 구현을 주입한다.

```python
import asyncio
from pathlib import Path

from ctf_harness.app import build_codex_harness

platform = MyPlatformAdapter(...)
harness = build_codex_harness(
    platform=platform,
    runs_root=Path("runs"),
    coordinator_model="gpt-5.4",
    worker_model="gpt-5.4",
    max_swarms=3,
    poll_interval_s=5.0,
)

stop = asyncio.Event()
asyncio.run(harness.workflow.run_live("competition-001", stop))
```

`PlatformAdapter`는 다음 메서드를 구현한다.

```python
async def list_challenges() -> list[Challenge]: ...
async def list_solved_challenge_ids() -> set[str]: ...
async def download_challenge(challenge, destination) -> Path: ...
async def submit_flag(challenge_id, flag) -> bool: ...
```

현재 `CTFdPlatformAdapter`는 모든 메서드에서 `NotImplementedError`를 발생시킨다. 별도로 개발하는 CTFd client를 이 경계에 연결해야 한다.

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
- 장기 Coordinator Thread 재사용
- Coordinator turn 직렬화
- Worker의 단일 모델 사용
- Coordinator feedback을 이용한 Worker 후속 turn
- SDK runtime 오류의 `failed` report 변환
- 완료 후 Scheduler runtime 제거

## 제한 사항

- 실제 CTFd API는 아직 구현하지 않았다.
- 현재 Repository와 EventBus는 인메모리 구현이라 프로세스를 재시작하면 사라진다.
- 현재 Worker는 로컬 workspace와 Codex `workspace_write` sandbox를 사용한다.
- Docker 또는 Kubernetes 수준의 격리는 아직 제공하지 않는다.
- Flag 자동 검증과 제출 정책은 아직 구현하지 않았다.
- Temporal, PostgreSQL, MinIO, Event API는 아직 연결하지 않았다.

신뢰할 수 없는 Challenge 파일을 실전에서 실행하기 전에는 Docker 기반 Worker Scheduler를 연결해야 한다.

## 로드맵

1. 실제 CTFd Platform Adapter를 연결한다.
2. Docker 기반 Worker Scheduler와 카테고리별 Tool Image를 구현한다.
3. report, feedback, transcript를 PostgreSQL에 영구 저장한다.
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
