// Queue Management
let eventSource = null;
let queueReconnectTimer = null;
let queuePollTimer = null;
let queueFetchInFlight = null;

function clearQueuePoll() {
  if (!queuePollTimer) return;
  clearTimeout(queuePollTimer);
  queuePollTimer = null;
}

function scheduleQueuePoll(delay = 2000) {
  clearQueuePoll();
  queuePollTimer = setTimeout(() => {
    queuePollTimer = null;
    fetchQueue();
  }, delay);
}

function initQueue() {
  if (eventSource) eventSource.close();
  if (queueReconnectTimer) clearTimeout(queueReconnectTimer);
  clearQueuePoll();

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

  source.onopen = () => {
    if (queueReconnectTimer) clearTimeout(queueReconnectTimer);
  };

  source.onerror = (error) => {
    if (eventSource !== source) return;
    console.error("SSE error:", error);
    source.close();
    eventSource = null;
    fetchQueue();
    scheduleQueuePoll();
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
      scheduleQueuePoll(5000);
    } finally {
      queueFetchInFlight = null;
    }
  })();
  return queueFetchInFlight;
}

function updateQueueUI(jobs) {
  const queueList = document.getElementById("queue-list");
  const queueSection = document.getElementById("queue-section");
  jobs = [...jobs].sort(
    (left, right) => new Date(right.created_at) - new Date(left.created_at),
  );
  queueJobs = jobs;
  const activeJobsByVideo = new Map(jobs.filter((job) => ["queued", "downloading"].includes(job.status)).map((job) => [job.video_id, job]));
  const jobsById = new Map(jobs.map((job) => [job.id, job]));
  // Some reverse proxies keep the SSE connection open but buffer its
  // events. Poll while work is active so queued/completed cards still
  // appear without a page refresh; SSE remains the fast primary path.
  if (jobs.some((job) => ["queued", "downloading"].includes(job.status))) {
    scheduleQueuePoll(1500);
  } else if (!eventSource) {
    scheduleQueuePoll(5000);
  } else {
    clearQueuePoll();
  }
  const shouldGlow = currentTab !== "downloads" && downloadAttention;
  document.getElementById("downloads-btn").classList.toggle(
    "download-active",
    shouldGlow,
  );
  document
    .querySelector('[data-mobile-tab="downloads"]')
    .classList.toggle("download-active", shouldGlow);

  document.querySelectorAll(".download-btn[data-video-id]").forEach((button) => {
    const job = jobsById.get(button.dataset.queueJobId) || activeJobsByVideo.get(button.dataset.videoId);
    if (job && ["queued", "downloading"].includes(job.status)) {
      renderInlineDownload(button, job);
    } else if (button.dataset.queueJobId) {
      resetInlineDownloadButton(button);
    }
  });

  queueSection.style.display = "block";
  const query = currentTab === "downloads" ? searchInput.value.trim().toLowerCase() : "";
  const signature = JSON.stringify([jobs, query]);
  if (queueList.renderSignature === signature) return;
  queueList.renderSignature = signature;
  if (jobs.length === 0) {
    queueList.innerHTML = `
      <div class="downloads-empty">
        <strong>No downloads yet</strong>
        <span>Download a song or album from Explore or Search to see it here.</span>
      </div>`;
    return;
  }

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
  } catch (error) {
    showStatus("Failed to cancel downloads", true);
  } finally {
    button.disabled = false;
  }
}

document.getElementById("cancel-all-btn").addEventListener("click", cancelAllDownloads);
