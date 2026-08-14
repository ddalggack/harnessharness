(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  let state = null;
  let selectedMode = "demo";
  let workerLimit = 3;
  let workerModel = "gpt-5.4";
  let autoSubmitFlags = false;
  let challengeFilter = "all";
  let polling = false;
  let toastTimer = null;
  let selectedWorkerIndex = null;
  let observedRunId;
  let eventRunId = null;
  let eventCursor = 0;
  let renderedWorkersSignature = null;
  let confirmResolver = null;
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

  function renderInlineMarkdown(value) {
    const codeSpans = [];
    const links = [];
    let text = escapeHtml(value).replace(/`([^`]+)`/g, (_, code) => {
      const token = `\u0000CODE${codeSpans.length}\u0000`;
      codeSpans.push(`<code>${code}</code>`);
      return token;
    });
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, label, url) => {
      const token = `\u0000LINK${links.length}\u0000`;
      links.push(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
      return token;
    });
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    text = text.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return text
      .replace(/\u0000CODE(\d+)\u0000/g, (_, index) => codeSpans[Number(index)])
      .replace(/\u0000LINK(\d+)\u0000/g, (_, index) => links[Number(index)]);
  }

  function markdownCells(line) {
    return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
  }

  function renderMarkdown(value) {
    const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let paragraph = [];
    let listType = null;
    let codeLanguage = null;
    let codeLines = null;

    function flushParagraph() {
      if (!paragraph.length) return;
      output.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }

    function flushList() {
      if (!listType) return;
      output.push(`</${listType}>`);
      listType = null;
    }

    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (codeLines) {
        if (/^\s*```/.test(line)) {
          const languageClass = codeLanguage ? ` class="language-${codeLanguage}"` : "";
          output.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
          codeLines = null;
          codeLanguage = null;
        } else {
          codeLines.push(line);
        }
        continue;
      }

      const fence = line.match(/^\s*```([A-Za-z0-9_+-]*)\s*$/);
      if (fence) {
        flushParagraph();
        flushList();
        codeLanguage = fence[1];
        codeLines = [];
        continue;
      }
      if (!line.trim()) {
        flushParagraph();
        flushList();
        continue;
      }

      const nextLine = lines[index + 1] || "";
      if (line.includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(nextLine)) {
        flushParagraph();
        flushList();
        const headers = markdownCells(line);
        output.push(`<table><thead><tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>`);
        index += 2;
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          const cells = markdownCells(lines[index]);
          output.push(`<tr>${headers.map((_, cellIndex) => `<td>${renderInlineMarkdown(cells[cellIndex] || "")}</td>`).join("")}</tr>`);
          index += 1;
        }
        output.push("</tbody></table>");
        index -= 1;
        continue;
      }

      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        flushList();
        const level = heading[1].length;
        output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }
      if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        flushParagraph();
        flushList();
        output.push("<hr>");
        continue;
      }
      const quote = line.match(/^\s{0,3}>\s?(.*)$/);
      if (quote) {
        flushParagraph();
        flushList();
        output.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
        continue;
      }
      const unordered = line.match(/^\s{0,3}[-+*]\s+(.+)$/);
      const ordered = line.match(/^\s{0,3}\d+\.\s+(.+)$/);
      if (unordered || ordered) {
        flushParagraph();
        const nextType = unordered ? "ul" : "ol";
        if (listType !== nextType) {
          flushList();
          listType = nextType;
          output.push(`<${listType}>`);
        }
        output.push(`<li>${renderInlineMarkdown((unordered || ordered)[1])}</li>`);
        continue;
      }
      flushList();
      paragraph.push(line.trim());
    }

    flushParagraph();
    flushList();
    if (codeLines) {
      const languageClass = codeLanguage ? ` class="language-${codeLanguage}"` : "";
      output.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    }
    return output.join("\n");
  }

  function formatClock(value) {
    return new Date(value).toLocaleTimeString("ko-KR", { hour12: false });
  }

  function formatDuration(startedAt, finishedAt) {
    if (!startedAt) return "00:00";
    const started = typeof startedAt === "number" ? startedAt : Date.parse(startedAt);
    const finished = finishedAt
      ? (typeof finishedAt === "number" ? finishedAt : Date.parse(finishedAt))
      : Date.now();
    if (!Number.isFinite(started) || !Number.isFinite(finished)) return "00:00";
    const totalSeconds = Math.max(0, Math.floor((finished - started) / 1000));
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
      const nestedTotal = usage.total && typeof usage.total === "object" ? usage.total : null;
      const total = usage.total_tokens
        ?? usage.totalTokens
        ?? (nestedTotal && (nestedTotal.total_tokens ?? nestedTotal.totalTokens))
        ?? (typeof usage.total === "number" ? usage.total : null);
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

  function compactLogValue(value, limit) {
    if (value == null || value === "") return "";
    let text;
    if (typeof value === "string") text = value;
    else {
      try { text = JSON.stringify(value); }
      catch { text = String(value); }
    }
    text = text.replace(/\s+/g, " ").trim();
    const max = limit || 360;
    return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
  }

  function eventWorkerIndex(payload) {
    const number = Number(payload && (payload.worker_number ?? payload.workerNumber));
    if (Number.isInteger(number) && number >= 1 && number <= 3) return number - 1;
    if (!state || !payload || !payload.worker_id) return -1;
    return state.workers.findIndex((worker) => worker.workerId === payload.worker_id);
  }

  function eventLogEntry(event) {
    const payload = event.payload || {};
    const status = payload.status || "";
    const challenge = payload.challenge_title || payload.challenge_id || "";
    const elapsed = Number(payload.elapsed_s);
    const elapsedText = Number.isFinite(elapsed) ? ` · ${elapsed.toFixed(1)}초` : "";
    const tool = compactLogValue(payload.tool) || "도구";
    const finished = status === "completed";
    const detail = compactLogValue(
      payload.command
      ?? payload.query
      ?? payload.arguments
      ?? payload.output
      ?? payload.result
      ?? payload.changes
      ?? payload.content
      ?? payload.summary
      ?? payload.intent
    );

    switch (event.type) {
      case "worker.started":
        return { type: "시작", message: `${challenge || "문제"} · Worker 시작`, level: "info" };
      case "worker.turn_started":
        return { type: "분석", message: `Turn ${payload.turn || 1} 시작`, level: "info" };
      case "worker.turn_completed":
        return { type: "분석", message: `Turn ${payload.turn || 1} 완료${elapsedText}`, level: "success" };
      case "worker.heartbeat":
        return { type: "작업 중", message: detail || `Worker 실행 중${elapsedText}`, level: "info" };
      case "worker.reasoning":
        return { type: "추론", message: detail || "추론 진행 중", level: "info" };
      case "worker.tool": {
        const exitCode = payload.exit_code ?? payload.exitCode;
        const exitText = exitCode == null ? "" : ` · exit ${exitCode}`;
        return {
          type: tool,
          message: `${finished ? "완료" : "실행"}${exitText}${detail ? ` · ${detail}` : ""}`,
          level: payload.error || (exitCode != null && Number(exitCode) !== 0) ? "error" : (finished ? "success" : "info"),
        };
      }
      case "worker.file_change":
        return { type: "파일 변경", message: detail || "Worker가 파일을 수정했습니다.", level: "info" };
      case "worker.message":
        return { type: "메시지", message: detail || `${payload.role || "assistant"} 메시지`, level: "info" };
      case "worker.token_usage":
        return { type: "토큰", message: tokensForWorker({ tokenUsage: payload.usage }), level: "info" };
      case "worker.reported":
        return {
          type: String(payload.kind || "보고"),
          message: compactLogValue(payload.summary) || "Worker 보고 수신",
          level: logLevelForStatus(payload.kind),
        };
      case "worker.terminated":
        return { type: "중지", message: "Worker 실행 중지", level: "warning" };
      default:
        if (event.type.startsWith("submission.")) {
          const result = event.type.split(".", 2)[1];
          const failed = ["rejected", "error", "wrong_limit"].includes(result);
          return {
            type: "플래그 제출",
            message: compactLogValue(payload.summary) || result,
            level: result === "accepted" ? "success" : (failed ? "error" : "warning"),
          };
        }
        return null;
    }
  }

  function ingestWorkerEvents(events) {
    (events || []).forEach((event) => {
      const index = eventWorkerIndex(event.payload || {});
      const entry = eventLogEntry(event);
      if (index < 0 || !entry) return;
      const key = String(index);
      const entries = workerLogs.get(key) || [];
      entries.push({ ...entry, time: event.occurredAt || Date.now() });
      workerLogs.set(key, entries.slice(-160));
    });
  }

  async function pollWorkerEvents() {
    if (!state || !state.run.id) {
      if (eventRunId !== null) {
        eventRunId = null;
        eventCursor = 0;
        workerLogs.clear();
      }
      return;
    }
    if (state.run.id !== eventRunId) {
      eventRunId = state.run.id;
      eventCursor = 0;
      workerLogs.clear();
    }
    let hasMore = true;
    while (hasMore) {
      const batch = await api(`/api/events?cursor=${eventCursor}`);
      if (batch.runId !== state.run.id) {
        eventRunId = batch.runId || null;
        eventCursor = 0;
        workerLogs.clear();
        return;
      }
      ingestWorkerEvents(batch.events);
      eventCursor = Number(batch.nextCursor) || eventCursor;
      hasMore = Boolean(batch.hasMore);
    }
  }

  function observeWorkers() {
    if (!state) return;
    const runId = state.run.id || null;
    if (runId !== observedRunId) {
      observedRunId = runId;
      renderedWorkersSignature = null;
      workerObservations.clear();
    }

    state.workers.forEach((worker, index) => {
      const key = String(index);
      const observation = workerObservations.get(key) || {
        startedAt: null,
        finishedAt: null,
      };
      observation.startedAt = worker.startedAt || observation.startedAt;
      observation.finishedAt = worker.finishedAt || null;
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
    $("#worker-model").disabled = running;
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
    renderSolvedDb();
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
      worker.model,
      worker.phase,
      worker.progress,
      worker.completed,
      worker.tokenUsage,
      worker.startedAt,
      worker.finishedAt,
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
    output.innerHTML = entries.length ? entries.slice(-40).map((entry) => `
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

  function renderSolvedDb() {
    const records = state.solvedDb || [];
    $("#empty-solved-db").classList.toggle("hidden", records.length > 0);
    $("#solved-db-list").innerHTML = records.map((record) => {
      const generating = record.writeupStatus === "generating";
      const writeupLabel = record.hasWriteup ? "Write-up 재생성" : "Gen Write-up";
      return `
        <article class="solved-db-card">
          <div class="solved-db-copy">
            <div class="solved-db-title-row">
              <span class="category-tag ${escapeHtml(record.category)}">${escapeHtml(record.category)}</span>
              <strong>${escapeHtml(record.name)}</strong>
              <span class="solved-db-verification ${escapeHtml(record.verification)}">${escapeHtml(record.verification)}</span>
            </div>
            <p>${escapeHtml(record.solverFile)} · ${escapeHtml(record.model)} · event.json · status.json</p>
          </div>
          <div class="solved-db-actions">
            <button type="button" class="button writeup-button" data-writeup-id="${escapeHtml(record.id)}" ${generating ? "disabled" : ""}>${generating ? "생성 중…" : writeupLabel}</button>
            <button type="button" class="button preview-button${record.hasWriteup ? "" : " disabled"}" data-preview-id="${escapeHtml(record.id)}" ${record.hasWriteup ? "" : "disabled"}>미리보기</button>
            <a class="button download-button${record.hasWriteup ? "" : " disabled"}" ${record.hasWriteup ? `href="/api/solved/download?id=${encodeURIComponent(record.id)}&file=write-up.md"` : "aria-disabled=\"true\""}>다운로드</a>
            <button type="button" class="button solved-delete-button" data-delete-solved-id="${escapeHtml(record.id)}" data-delete-solved-name="${escapeHtml(record.name)}" ${generating ? "disabled" : ""}>레코드 삭제</button>
          </div>
        </article>`;
    }).join("");
  }

  async function generateWriteup(recordId, button) {
    setBusy(button, true, "생성 중…");
    try {
      state = await api("/api/writeup", {
        method: "POST",
        body: { recordId },
      });
      render();
      showToast("독립 Write-up 에이전트가 write-up.md를 생성했습니다.");
    } catch (error) {
      showToast(error.message, true);
      await poll();
    } finally {
      setBusy(button, false);
    }
  }

  function confirmAction(title, message, acceptLabel) {
    const dialog = $("#confirm-dialog");
    $("#confirm-dialog-title").textContent = title;
    $("#confirm-dialog-message").textContent = message;
    $("#confirm-dialog-accept").textContent = acceptLabel || "확인";
    if (!dialog.open) dialog.showModal();
    return new Promise((resolve) => { confirmResolver = resolve; });
  }

  function finishConfirm(accepted) {
    const resolver = confirmResolver;
    confirmResolver = null;
    if ($("#confirm-dialog").open) $("#confirm-dialog").close();
    if (resolver) resolver(accepted);
  }

  async function openWriteupPreview(recordId) {
    const dialog = $("#writeup-preview-dialog");
    $("#writeup-preview-title").textContent = "Write-up 미리보기";
    $("#writeup-preview-content").textContent = "불러오는 중…";
    if (!dialog.open) dialog.showModal();
    try {
      const preview = await api(`/api/solved/preview?id=${encodeURIComponent(recordId)}`);
      const record = (state.solvedDb || []).find((item) => item.id === recordId);
      $("#writeup-preview-title").textContent = record ? `${record.name} Write-up` : "Write-up 미리보기";
      $("#writeup-preview-content").innerHTML = renderMarkdown(preview.content);
    } catch (error) {
      dialog.close();
      showToast(error.message, true);
    }
  }

  async function deleteSolvedRecord(recordId, name) {
    const accepted = await confirmAction(
      "Solved DB 레코드 삭제",
      `'${name}'의 solver, event.json, status.json, write-up.md를 모두 삭제합니다. 이 작업은 되돌릴 수 없습니다.`,
      "레코드 삭제"
    );
    if (!accepted) return;
    try {
      state = await api("/api/solved/delete", {
        method: "POST",
        body: { recordId },
      });
      if ($("#writeup-preview-dialog").open) $("#writeup-preview-dialog").close();
      render();
      showToast(`${name} Solved DB 레코드를 삭제했습니다.`);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  async function resetDashboard() {
    const accepted = await confirmAction(
      "Dashboard 전체 초기화",
      "현재 실행을 중지하고 Solved DB의 모든 solver, JSON, Write-up 파일을 삭제합니다. 이 작업은 되돌릴 수 없습니다.",
      "전체 초기화"
    );
    if (!accepted) return;
    try {
      state = await api("/api/reset", {
        method: "POST",
        body: { clearSolvedDb: true },
      });
      if ($("#writeup-preview-dialog").open) $("#writeup-preview-dialog").close();
      render();
      showToast("실행 상태와 Solved DB를 초기화했습니다.");
    } catch (error) {
      showToast(error.message, true);
    }
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
      workerModel,
      categories,
      baseUrl: $("#base-url").value.trim(),
      apiToken: $("#api-token").value,
      authorized: $("#authorized").checked,
      autoSubmitFlags: selectedMode === "ctfd" && autoSubmitFlags,
    };
    setBusy($("#connect-button"), true, "확인 중…");
    try {
      state = await api("/api/connect", { method: "POST", body: payload });
      workerModel = state.config.workerModel;
      $("#worker-model").value = workerModel;
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
        body: {
          workerModel,
          autoSubmitFlags: selectedMode === "ctfd" && autoSubmitFlags,
        },
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
      await pollWorkerEvents();
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
  $("#worker-model").addEventListener("input", (event) => {
    workerModel = event.target.value.trim();
  });
  $("#worker-list").addEventListener("click", (event) => {
    const card = event.target.closest("[data-worker-index]");
    if (!card || card.disabled) return;
    openWorkerDialog(Number(card.dataset.workerIndex));
  });
  $("#solved-db-list").addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-delete-solved-id]");
    if (deleteButton && !deleteButton.disabled) {
      deleteSolvedRecord(deleteButton.dataset.deleteSolvedId, deleteButton.dataset.deleteSolvedName);
      return;
    }
    const preview = event.target.closest("[data-preview-id]");
    if (preview && !preview.disabled) {
      openWriteupPreview(preview.dataset.previewId);
      return;
    }
    const button = event.target.closest("[data-writeup-id]");
    if (!button || button.disabled) return;
    generateWriteup(button.dataset.writeupId, button);
  });
  $("#worker-dialog-close").addEventListener("click", () => $("#worker-dialog").close());
  $("#worker-log-button").addEventListener("click", () => {
    renderWorkerLogs(true);
    if (!$("#worker-log-dialog").open) $("#worker-log-dialog").showModal();
  });
  $("#worker-log-close").addEventListener("click", () => $("#worker-log-dialog").close());
  $("#writeup-preview-close").addEventListener("click", () => $("#writeup-preview-dialog").close());
  $("#confirm-dialog-cancel").addEventListener("click", () => finishConfirm(false));
  $("#confirm-dialog-accept").addEventListener("click", () => finishConfirm(true));
  $("#confirm-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    finishConfirm(false);
  });
  $("#confirm-dialog").addEventListener("click", (event) => {
    if (event.target === $("#confirm-dialog")) finishConfirm(false);
  });
  [$("#worker-dialog"), $("#worker-log-dialog"), $("#writeup-preview-dialog")].forEach((dialog) => {
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
  $("#reset-button").addEventListener("click", resetDashboard);
  $("#export-button").addEventListener("click", exportState);

  updateMode("demo");
  poll();
  setInterval(poll, 800);
})();
