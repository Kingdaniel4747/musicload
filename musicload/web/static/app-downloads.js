// Queue Management
let eventSource = null;
let queueReconnectTimer = null;
let queuePollTimer = null;
let queueFetchInFlight = null;

function queuePollDelay() {
  if (document.hidden) return 15000;
  const active = queueJobs.some((job) => ["queued", "downloading"].includes(job.status));
  return currentTab === "downloads" || active ? 2000 : 5000;
}

function scheduleQueuePoll(delay = queuePollDelay()) {
  if (queuePollTimer) window.clearTimeout(queuePollTimer);
  queuePollTimer = window.setTimeout(() => {
    queuePollTimer = null;
    fetchQueue();
  }, delay);
}

function initQueue() {
  if (eventSource) eventSource.close();
  if (queueReconnectTimer) clearTimeout(queueReconnectTimer);

  // Connect to SSE endpoint for real-time updates
  const source = new EventSource("/api/queue/stream");
  eventSource = source;

  source.onmessage = (event) => {
    try {
      const jobs = JSON.parse(event.data);
      updateQueueUI(jobs);
    } catch (error) {
      console.error("Invalid queue update:", error);
    }
  };

  source.onopen = () => fetchQueue();

  source.onerror = (error) => {
    if (eventSource !== source) return;
    console.error("SSE error:", error);
    source.close();
    eventSource = null;
    queueReconnectTimer = setTimeout(() => {
      queueReconnectTimer = null;
      initQueue();
    }, 5000);
  };

  // Load initial queue
  fetchQueue();
}

async function fetchQueue() {
  if (queueFetchInFlight) return queueFetchInFlight;
  queueFetchInFlight = (async () => {
    try {
      const response = await fetch("/api/queue/jobs", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !Array.isArray(data.jobs)) {
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      updateQueueUI(data.jobs);
    } catch (error) {
      console.error("Failed to fetch queue:", error);
    } finally {
      queueFetchInFlight = null;
      scheduleQueuePoll();
    }
  })();
  return queueFetchInFlight;
}

function addOptimisticQueueJob(job) {
  queueJobs = [job, ...queueJobs.filter((item) => item.id !== job.id)];
  updateQueueUI(queueJobs);
  scheduleQueuePoll(500);
}

function updateQueueUI(jobs) {
  const queueList = document.getElementById("queue-list");
  const queueSection = document.getElementById("queue-section");
  jobs = [...jobs].sort(
    (left, right) => new Date(right.created_at) - new Date(left.created_at),
  );
  queueJobs = jobs;
  const shouldGlow = currentTab !== "downloads" && downloadAttention;
  document.getElementById("downloads-btn").classList.toggle(
    "download-active",
    shouldGlow,
  );
  document
    .querySelector('[data-mobile-tab="downloads"]')
    .classList.toggle("download-active", shouldGlow);

  document.querySelectorAll(".download-btn[data-video-id]").forEach((button) => {
    const job = jobs.find(
      (item) =>
        item.id === button.dataset.queueJobId ||
        (item.video_id === button.dataset.videoId &&
          ["queued", "downloading"].includes(item.status)),
    );
    if (job && ["queued", "downloading"].includes(job.status)) {
      renderInlineDownload(button, job);
    } else if (button.dataset.queueJobId) {
      resetInlineDownloadButton(button);
    }
  });

  queueSection.style.display = "block";
  if (jobs.length === 0) {
    queueList.innerHTML = `
      <div class="downloads-empty">
        <strong>No downloads yet</strong>
        <span>Download a song or album from Explore or Search to see it here.</span>
      </div>`;
    return;
  }

  const query = currentTab === "downloads" ? searchInput.value.trim().toLowerCase() : "";
  const visibleJobs = query
    ? jobs.filter((job) => `${job.title} ${job.artist}`.toLowerCase().includes(query))
    : jobs;

  if (visibleJobs.length === 0) {
    queueList.innerHTML = `
      <div class="downloads-empty">
        <strong>No matching downloads</strong>
        <span>Try a different title or artist.</span>
      </div>`;
    return;
  }

  queueList.innerHTML = visibleJobs
    .map((job) => {
      const statusClass = job.status.toLowerCase();
      let jobMetricsHtml = "";
      let actionsHtml = "";

      if (job.status === "queued" || job.status === "downloading") {
        jobMetricsHtml = `<span class="job-progress-details">${escapeHtml(progressDetails(job))}</span>`;
      }

      if (job.status === "completed" && job.file_path) {
        actionsHtml = `
                  <a href="/api/download-file/${encodeURIComponent(job.file_path)}" class="download-file-btn" download>
                      💾 Save to Computer
                  </a>
              `;
      }

      if (isAdmin && (job.status === "completed" || job.status === "failed")) {
        actionsHtml += `
                  <button class="clear-job-btn" data-job-id="${job.id}" data-delete-file="${Boolean(job.file_path)}">${job.file_path ? "Delete File" : "Clear"}</button>
              `;
      }

      if (job.status === "queued" || job.status === "downloading") {
        actionsHtml += `
                  <button class="cancel-job-btn progress-action-btn" data-job-id="${job.id}">${progressActionMarkup(job, "Cancel")}</button>
              `;
      }

      const errorHtml = job.error
        ? `<div class="job-error">${escapeHtml(job.error)}</div>`
        : "";

      return `
              <div class="job-card ${statusClass}">
                  <div class="job-header">
                      <div class="job-info">
                          <span class="job-title">${escapeHtml(job.title)}</span>
                          <span class="job-artist">${escapeHtml(job.artist)}</span>
                      </div>
                      <span class="job-state">
                        <span class="job-status status-${statusClass}">${job.status.toUpperCase()}</span>
                        ${jobMetricsHtml}
                      </span>
                  </div>
                  ${errorHtml}
                  ${actionsHtml}
              </div>
          `;
    })
    .join("");

}

async function removeJob(jobId, deleteFile = false) {
  try {
    const response = await fetch(
      `/api/queue/${jobId}?delete_file=${deleteFile}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Request failed");
    }
    queueJobs = queueJobs.filter((job) => job.id !== jobId);
    updateQueueUI(queueJobs);
    scheduleQueuePoll(500);
  } catch (error) {
    console.error("Failed to remove job:", error);
    throw error;
  }
}

async function cancelAllDownloads() {
  const button = document.getElementById("cancel-all-btn");
  button.disabled = true;
  try {
    const response = await fetch("/api/queue/cancel-all", { method: "POST" });
    if (!response.ok) throw new Error("Cancellation failed");
    await fetchQueue();
  } catch (error) {
    showStatus("Failed to cancel downloads", true);
  } finally {
    button.disabled = false;
  }
}

document.getElementById("cancel-all-btn").addEventListener("click", cancelAllDownloads);

// Initialize queue on page load
initQueue();

window.addEventListener("pageshow", () => fetchQueue());
window.addEventListener("focus", () => fetchQueue());
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    scheduleQueuePoll();
  } else {
    fetchQueue();
  }
});

// Reusable in-app confirmation dialog for destructive actions.
const confirmModal = document.getElementById("confirm-modal");
const confirmTitle = document.getElementById("confirm-title");
const confirmMessage = document.getElementById("confirm-message");
const confirmAction = document.getElementById("confirm-action");
const confirmCancel = document.getElementById("confirm-cancel");
const confirmClose = document.getElementById("confirm-close");
let confirmResolver = null;

function closeConfirmation(confirmed) {
  confirmModal.classList.remove("active");
  if (confirmResolver) {
    confirmResolver(confirmed);
    confirmResolver = null;
  }
}

function askForConfirmation({ title, message, actionLabel = "Delete" }) {
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  confirmAction.textContent = actionLabel;
  confirmModal.classList.add("active");
  confirmAction.focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

async function runConfirmedAction({
  button,
  title,
  message,
  actionLabel = "Delete",
  pendingLabel = "Deleting...",
  errorLabel = "Action failed",
  action,
}) {
  const confirmed = await askForConfirmation({ title, message, actionLabel });
  if (!confirmed) return false;

  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = pendingLabel;
  try {
    await action();
    return true;
  } catch (error) {
    showStatus(`${errorLabel}: ${error.message}`, true);
    return false;
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

confirmAction.addEventListener("click", () => closeConfirmation(true));
confirmCancel.addEventListener("click", () => closeConfirmation(false));
confirmClose.addEventListener("click", () => closeConfirmation(false));
confirmModal.addEventListener("click", (event) => {
  if (event.target === confirmModal) closeConfirmation(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && confirmModal.classList.contains("active")) {
    closeConfirmation(false);
  }
});
document.getElementById("queue-list").addEventListener("click", async (event) => {
  const clearButton = event.target.closest(".clear-job-btn");
  if (clearButton) {
    const deleteFile = clearButton.dataset.deleteFile === "true";
    const title = queueJobs.find((job) => job.id === clearButton.dataset.jobId)?.title || "this download";
    const removed = await runConfirmedAction({
      button: clearButton,
      title: deleteFile ? "Delete downloaded file?" : "Clear download entry?",
      message: deleteFile
        ? `Delete "${title}", its lyrics, and any folder left without another song? This cannot be undone.`
        : `Remove "${title}" from the download history?`,
      actionLabel: deleteFile ? "Delete File" : "Clear Entry",
      pendingLabel: deleteFile ? "Deleting..." : "Clearing...",
      errorLabel: deleteFile ? "Delete failed" : "Clear failed",
      action: () => removeJob(clearButton.dataset.jobId, deleteFile),
    });
    if (removed) showStatus(deleteFile ? `Deleted: ${title}` : `Cleared: ${title}`);
    return;
  }

  const cancelButton = event.target.closest(".cancel-job-btn");
  if (cancelButton) {
    cancelButton.disabled = true;
    cancelButton.textContent = "Cancelling...";
    try {
      await removeJob(cancelButton.dataset.jobId);
    } catch (error) {
      cancelButton.disabled = false;
      cancelButton.textContent = "Cancel";
    }
  }
});
