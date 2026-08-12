# Gui

HarneHarness의 Python 기반 로컬 CTF 오케스트레이션 대시보드입니다.

## 대시보드 실행

Python 3.11 이상이 필요합니다. PowerShell에서 다음 명령을 실행합니다.

```powershell
cd "C:\Users\gyqls\Desktop\Gui-dev"
$env:PYTHONPATH = "$PWD\src"
& "C:\Users\gyqls\miniconda3\python.exe" -m ctf_harness.cli dashboard --host 127.0.0.1 --port 18788
```

브라우저에서 <http://127.0.0.1:18788>을 엽니다.

## 플래그 자동 제출

- 토글은 `문제 다시 불러오기` 버튼 위에 있습니다.
- 안전모드에서는 항상 꺼지고 비활성화됩니다.
- 로컬 CTFd 모드에서만 켤 수 있습니다.
- 켜면 완료된 Worker가 보고한 플래그 후보를 Submission Broker가 CTFd에 제출합니다.
- 플래그 원문은 공개 이벤트 로그에 기록하지 않습니다.

자세한 구조와 CLI 사용법은 [README_DASHBOARD.md](README_DASHBOARD.md)를 참고하세요.
