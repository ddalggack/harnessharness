(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  let state = null;
  let selectedMode = "demo";
  let workerLimit = 3;
  let autoSubmitFlags = false;
  let challengeFilter = "all";
  let polling = false;
  let toastTimer = null;
  let selectedWorkerIndex = null;
  let observedRunId;
  let renderedWorkersSignature = null;
  const workerObservations = new Map();
  const workerLogs = new Map();

  const statusLabels = {
    queued: "대기",
    running: "실행 중",
    solved: "검증 완료",
    created: "생성",
    completed: "완료",
    terminated: "중지",
    needs_tooling: "도구 필요",
    failed: "실패",
    idle: "대기",
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatClock(value) {
    return new Date(value).toLocaleTimeString("ko-KR", { hour12: false });
  }

  function formatDuration(startedAt, finishedAt) {
    if (!startedAt) return "00:00";
    const totalSeconds = Math.max(0, Math.floor(((finishedAt || Date.now()) - startedAt) / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const short = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    return hours > 0 ? `${String(hours).padStart(2, "0")}:${short}` : short;
  }

  function challengeForWorker(worker) {
    if (!state || !worker || !worker.challengeName) return null;
    return state.challenges.find((challenge) => challenge.name === worker.challengeName) || null;
  }

  function modelForWorker(worker) {
    if (worker && worker.model) return String(worker.model);
    if (state && state.config && state.config.workerModel) return String(state.config.workerModel);
    return selectedMode === "demo" ? "Demo Worker" : "gpt-5.4";
  }

  function tokensForWorker(worker) {
    const usage = worker && (worker.tokenUsage ?? worker.tokens ?? worker.usage);
    if (typeof usage === "number" && Number.isFinite(usage)) {
      return `${Math.max(0, usage).toLocaleString("ko-KR")} tokens`;
    }
    if (usage && typeof usage === "object") {
      const total = usage.total_tokens ?? usage.totalTokens ?? usage.total;
      if (typeof total === "number" && Number.isFinite(total)) {
        return `${Math.max(0, total).toLocaleString("ko-KR")} tokens`;
      }
    }
    if (typeof usage === "string" && usage.trim()) return usage.trim();
    return "수집 안 됨";
  }

  function logLevelForStatus(status) {
    if (status === "completed" || status === "solved") return "success";
    if (status === "failed") return "error";
    if (status === "terminated" || status === "needs_tooling") return "warning";
    return "info";
  }

  function observeWorkers() {
    if (!state) return;
    const runId = state.run.id || null;
    if (runId !== observedRunId) {
      observedRunId = runId;
      renderedWorkersSignature = null;
      workerObservations.clear();
      workerLogs.clear();
    }

    state.workers.forEach((worker, index) => {
      const key = String(index);
      const challenge = challengeForWorker(worker);
      const phase = challenge && challenge.phase ? challenge.phase : worker.phase;
      const observation = workerObservations.get(key) || {
        signature: null,
        startedAt: null,
        finishedAt: null,
      };
      const active = worker.status === "running" || worker.status === "created";
      const terminal = ["completed", "failed", "terminated", "solved"].includes(worker.status);

      if (active && !observation.startedAt) observation.startedAt = Date.now();
      if (terminal && !observation.startedAt) observation.startedAt = Date.now();
      if (terminal && !observation.finishedAt) observation.finishedAt = Date.now();

      const signature = [worker.status, worker.challengeName, phase, worker.progress].join("|");
      if (signature !== observation.signature) {
        const entries = workerLogs.get(key) || [];
        const task = worker.challengeName || "할당된 문제 없음";
        entries.push({
          time: Date.now(),
          type: statusLabels[worker.status] || worker.status || "상태",
          message: `${task} · ${phase || "상태 갱신"}`,
          level: logLevelForStatus(worker.status),
        });
        workerLogs.set(key, entries.slice(-160));
        observation.signature = signature;
      }
      workerObservations.set(key, observation);
    });
  }

  async function api(path, options) {
    const response = await fetch(path, {
      method: options && options.method ? options.method : "GET",
      headers: { "Content-Type": "application/json" },
      body: options && options.body ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "요청에 실패했습니다.");
    return payload;
  }

  function showToast(message, error) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.className = `toast show${error ? " error" : ""}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.className = "toast"; }, 3200);
  }

  function render() {
    if (!state) return;
    observeWorkers();
    const running = state.run.status === "running";
    const connected = state.connection.connected;

    $("#system-pill").className = "system-pill online";
    $("#system-pill").innerHTML = `<span class="signal"></span><span>LOCAL API · PYTHON ${escapeHtml(state.environment.pythonVersion)}</span>`;
    $("#connection-badge").textContent = connected ? state.connection.label : "연결 전";
    $("#connection-badge").className = `connection-badge${connected ? " connected" : ""}`;
    $("#run-message").textContent = connected
      ? state.run.message
      : "좌측 사이드바에서 대상 CTF를 먼저 연결합니다.";
    $("#run-kicker").textContent = state.run.status.toUpperCase();
    $("#run-progress").style.width = `${state.run.progress}%`;
    $("#run-button").disabled = !connected || running;
    $("#stop-button").disabled = !running;
    $("#connect-button").disabled = running;
    const connectLabel = $("#connect-button").querySelector("span");
    if (connectLabel) connectLabel.textContent = connected ? "문제 다시 불러오기" : "문제 불러오기";
    $("#auto-submit-flags").checked = autoSubmitFlags;
    $("#auto-submit-flags").disabled = selectedMode !== "ctfd" || running;

    $("#metric-total").textContent = state.run.total || state.challenges.length;
    $("#metric-solved").textContent = state.run.solved;
    $("#metric-progress").textContent = `${state.run.progress}%`;
    $("#metric-status").textContent = state.run.status.toUpperCase();

    renderWorkers();
    renderChallenges();
    if ($("#worker-dialog").open) renderWorkerDialog();
    if ($("#worker-log-dialog").open) renderWorkerLogs(false);
  }

  function renderWorkers() {
    const signature = JSON.stringify(state.workers.map((worker) => [
      worker.name,
      worker.enabled,
      worker.status,
      worker.challengeName,
      worker.profile,
      worker.phase,
      worker.progress,
      worker.completed,
    ]));
    if (signature === renderedWorkersSignature) return;
    renderedWorkersSignature = signature;
    $("#worker-list").innerHTML = state.workers.map((worker, index) => {
      const profile = worker.profile ? worker.profile.toUpperCase() : "NONE";
      return `
        <button type="button" class="worker-card ${escapeHtml(worker.status)}${worker.enabled ? "" : " disabled"}" data-worker-index="${index}" aria-label="${escapeHtml(worker.name)} 진행상황 보기" ${worker.enabled ? "" : "disabled"}>
          <div class="worker-head">
            <div class="worker-name"><i class="worker-dot"></i>${escapeHtml(worker.name)}</div>
            <span class="worker-status">${escapeHtml(worker.status)}</span>
          </div>
          <div class="worker-task"><strong>${escapeHtml(worker.challengeName || (worker.enabled ? "Job 대기 중" : "Concurrency 제한"))}</strong><span>${escapeHtml(worker.phase)}</span></div>
          <div class="worker-divider" aria-hidden="true"></div>
          <div class="worker-meta"><span class="profile-badge">${profile}</span><span>DONE ${Number(worker.completed) || 0} · 진행 보기 ↗</span></div>
        </button>`;
    }).join("");
  }

  function selectedWorker() {
    if (!state || selectedWorkerIndex == null) return null;
    return state.workers[selectedWorkerIndex] || null;
  }

  function renderWorkerDialog() {
    const worker = selectedWorker();
    if (!worker) return;
    const challenge = challengeForWorker(worker);
    const observation = workerObservations.get(String(selectedWorkerIndex)) || {};
    const tokens = tokensForWorker(worker);
    const phase = challenge && challenge.phase ? challenge.phase : (worker.phase || "대기 중");

    $("#worker-dialog-title").textContent = worker.name;
    $("#worker-dialog-subtitle").textContent = worker.challengeName || "아직 배정된 문제가 없습니다.";
    $("#worker-detail-model").textContent = modelForWorker(worker);
    $("#worker-detail-challenge").textContent = worker.challengeName || "대기 중";
    $("#worker-detail-tokens").textContent = tokens;
    $("#worker-detail-duration").textContent = formatDuration(observation.startedAt, observation.finishedAt);
    $("#worker-detail-phase").textContent = phase;
    $("#worker-detail-status").textContent = statusLabels[worker.status] || worker.status || "대기";
    $("#worker-detail-status").className = `worker-detail-status ${escapeHtml(worker.status || "idle")}`;

    $("#worker-log-button").classList.add("hidden");
    $("#worker-inline-log").classList.remove("hidden");
    renderInlineWorkerLogs();
  }

  function renderInlineWorkerLogs() {
    const entries = workerLogs.get(String(selectedWorkerIndex)) || [];
    const output = $("#worker-inline-log-output");
    output.innerHTML = entries.length ? entries.slice(-4).map((entry) => `
      <div class="worker-inline-log-entry ${escapeHtml(entry.level)}">
        <div><time>${escapeHtml(formatClock(entry.time))}</time><strong>${escapeHtml(entry.type)}</strong></div>
        <p>${escapeHtml(entry.message)}</p>
      </div>`).join("") : '<div class="worker-inline-log-empty">아직 Worker 로그가 없습니다.</div>';
    output.scrollTop = output.scrollHeight;
  }

  function renderWorkerLogs(scrollToEnd) {
    const worker = selectedWorker();
    if (!worker) return;
    const entries = workerLogs.get(String(selectedWorkerIndex)) || [];
    $("#worker-log-title").textContent = `${worker.name} 로그`;
    $("#worker-log-path").textContent = `worker://${worker.name.toLowerCase().replace(/\s+/g, "-")}/${worker.challengeName || "idle"}`;
    $("#worker-log-output").innerHTML = entries.length ? entries.map((entry) => `
      <div class="worker-log-row ${escapeHtml(entry.level)}">
        <time class="worker-log-time">${escapeHtml(formatClock(entry.time))}</time>
        <span class="worker-log-type">${escapeHtml(entry.type)}</span>
        <p class="worker-log-message">${escapeHtml(entry.message)}</p>
      </div>`).join("") : '<div class="worker-log-empty">아직 관찰된 Worker 로그가 없습니다.</div>';
    if (scrollToEnd) $("#worker-log-output").scrollTop = $("#worker-log-output").scrollHeight;
  }

  function openWorkerDialog(index) {
    selectedWorkerIndex = index;
    renderWorkerDialog();
    if (!$("#worker-dialog").open) $("#worker-dialog").showModal();
  }

  function renderChallenges() {
    const rows = state.challenges.filter((challenge) => challengeFilter === "all" || challenge.category === challengeFilter);
    $("#empty-challenges").classList.toggle("hidden", state.challenges.length > 0);
    $("#challenge-table").innerHTML = rows.map((challenge) => `
      <tr>
        <td>${escapeHtml(challenge.name)}<span class="challenge-sub">#${escapeHtml(challenge.id)} · ${Number(challenge.points) || 0} pts</span></td>
        <td><span class="category-tag ${escapeHtml(challenge.category)}">${escapeHtml(challenge.category)}</span></td>
        <td>${escapeHtml(challenge.workerId || "—")}</td>
        <td><span class="status-tag ${escapeHtml(challenge.status)}">${escapeHtml(statusLabels[challenge.status] || challenge.status)}</span><span class="challenge-sub">${escapeHtml(challenge.phase)}</span></td>
      </tr>`).join("");
  }

  function updateMode(mode) {
    selectedMode = mode;
    if (mode !== "ctfd") autoSubmitFlags = false;
    $$("[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
    $("#ctfd-fields").classList.toggle("hidden", mode !== "ctfd");
    $("#ctfd-options").classList.toggle("hidden", mode !== "ctfd");
    $("#auto-submit-flags").checked = autoSubmitFlags;
    $("#auto-submit-flags").disabled = mode !== "ctfd";
  }

  async function connect() {
    const categories = $$('input[name="category"]:checked').map((input) => input.value);
    const payload = {
      mode: selectedMode,
      workerLimit,
      categories,
      baseUrl: $("#base-url").value.trim(),
      apiToken: $("#api-token").value,
      authorized: $("#authorized").checked,
      autoSubmitFlags: selectedMode === "ctfd" && autoSubmitFlags,
    };
    setBusy($("#connect-button"), true, "확인 중…");
    try {
      state = await api("/api/connect", { method: "POST", body: payload });
      autoSubmitFlags = Boolean(state.config.autoSubmitFlags);
      $("#api-token").value = "";
      render();
      showToast(`${state.connection.label}에서 문제 ${state.challenges.length}개를 불러왔습니다.`);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      setBusy($("#connect-button"), false);
    }
  }

  async function startRun() {
    setBusy($("#run-button"), true, "시작 중…");
    try {
      state = await api("/api/run", {
        method: "POST",
        body: { autoSubmitFlags: selectedMode === "ctfd" && autoSubmitFlags },
      });
      render();
      showToast("Ddalggack Worker 실행을 시작했습니다.");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      setBusy($("#run-button"), false);
    }
  }

  async function simpleAction(path, successMessage) {
    try {
      state = await api(path, { method: "POST" });
      render();
      showToast(successMessage);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function setBusy(button, busy, text) {
    if (!button.dataset.originalText) button.dataset.originalText = button.textContent.trim();
    button.disabled = busy;
    if (text) {
      const label = button.querySelector("span:last-child");
      if (label) label.textContent = text;
      else button.textContent = text;
    }
    if (!busy && button.dataset.originalText) {
      if (button.id === "connect-button") button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h11m0 0-4-4m4 4-4 4M20 5v14"></path></svg><span>문제 불러오기</span>';
      else if (button.id === "run-button") button.innerHTML = '<span class="play-icon" aria-hidden="true"></span><span>자동 풀이 시작</span>';
      else button.textContent = button.dataset.originalText;
    }
  }

  function exportState() {
    if (!state) return;
    const blob = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `harness-report-${state.run.id || "idle"}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    showToast("현재 공개 상태를 JSON으로 저장했습니다.");
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      state = await api("/api/state");
      render();
    } catch {
      $("#system-pill").className = "system-pill";
      $("#system-pill").innerHTML = '<span class="signal"></span><span>LOCAL API OFFLINE</span>';
    } finally {
      polling = false;
    }
  }

  $$("[data-mode]").forEach((button) => button.addEventListener("click", () => updateMode(button.dataset.mode)));
  $$("[data-worker-limit]").forEach((button) => button.addEventListener("click", () => {
    workerLimit = Number(button.dataset.workerLimit);
    $$("[data-worker-limit]").forEach((item) => item.classList.toggle("active", item === button));
  }));
  $("#auto-submit-flags").addEventListener("change", (event) => {
    autoSubmitFlags = selectedMode === "ctfd" && event.target.checked;
  });
  $("#worker-list").addEventListener("click", (event) => {
    const card = event.target.closest("[data-worker-index]");
    if (!card || card.disabled) return;
    openWorkerDialog(Number(card.dataset.workerIndex));
  });
  $("#worker-dialog-close").addEventListener("click", () => $("#worker-dialog").close());
  $("#worker-log-button").addEventListener("click", () => {
    renderWorkerLogs(true);
    if (!$("#worker-log-dialog").open) $("#worker-log-dialog").showModal();
  });
  $("#worker-log-close").addEventListener("click", () => $("#worker-log-dialog").close());
  [$("#worker-dialog"), $("#worker-log-dialog")].forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  $$("[data-filter]").forEach((button) => button.addEventListener("click", () => {
    challengeFilter = button.dataset.filter;
    $$("[data-filter]").forEach((item) => item.classList.toggle("active", item === button));
    renderChallenges();
  }));
  $("#connect-button").addEventListener("click", connect);
  $("#run-button").addEventListener("click", startRun);
  $("#stop-button").addEventListener("click", () => simpleAction("/api/stop", "실행을 중지했습니다."));
  $("#reset-button").addEventListener("click", () => simpleAction("/api/reset", "초기 상태로 되돌렸습니다."));
  $("#export-button").addEventListener("click", exportState);

  updateMode("demo");
  poll();
  setInterval(poll, 800);
})();
