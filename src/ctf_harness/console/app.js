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
    const running = state.run.status === "running";
    const connected = state.connection.connected;
    const activeWorkers = state.workers.filter((worker) => worker.status === "running").length;

    $("#system-pill").className = "system-pill online";
    $("#system-pill").innerHTML = `<span class="signal"></span><span>LOCAL API · PYTHON ${escapeHtml(state.environment.pythonVersion)}</span>`;
    $("#connection-badge").textContent = connected ? state.connection.label : "연결 전";
    $("#connection-badge").className = `connection-badge${connected ? " connected" : ""}`;
    $("#run-message").textContent = state.run.message;
    $("#run-kicker").textContent = state.run.status.toUpperCase();
    $("#run-progress").style.width = `${state.run.progress}%`;
    $("#run-button").disabled = !connected || running;
    $("#stop-button").disabled = !running;
    $("#connect-button").disabled = running;
    $("#connect-button").querySelector("span").textContent = connected ? "문제 다시 불러오기" : "문제 불러오기";
    $("#auto-submit-flags").checked = autoSubmitFlags;
    $("#auto-submit-flags").disabled = selectedMode !== "ctfd" || running;

    $("#metric-total").textContent = state.run.total || state.challenges.length;
    $("#metric-solved").textContent = state.run.solved;
    $("#metric-workers").textContent = `${activeWorkers}/${state.config.workerLimit}`;
    $("#metric-progress").textContent = `${state.run.progress}%`;
    $("#metric-status").textContent = state.run.status.toUpperCase();

    renderWorkers();
    renderChallenges();
  }

  function renderWorkers() {
    $("#worker-list").innerHTML = state.workers.map((worker) => {
      const profile = worker.profile ? worker.profile.toUpperCase() : "NONE";
      return `
        <article class="worker-card ${escapeHtml(worker.status)}${worker.enabled ? "" : " disabled"}">
          <div class="worker-head">
            <div class="worker-name"><i class="worker-dot"></i>${escapeHtml(worker.name)}</div>
            <span class="worker-status">${escapeHtml(worker.status)}</span>
          </div>
          <div class="worker-task"><strong>${escapeHtml(worker.challengeName || (worker.enabled ? "Job 대기 중" : "Concurrency 제한"))}</strong><span>${escapeHtml(worker.phase)}</span></div>
          <div class="mini-track"><i style="width:${Number(worker.progress) || 0}%"></i></div>
          <div class="worker-meta"><span class="profile-badge">${profile}</span><span>DONE ${Number(worker.completed) || 0}</span></div>
        </article>`;
    }).join("");
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
    if (text) button.textContent = text;
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
