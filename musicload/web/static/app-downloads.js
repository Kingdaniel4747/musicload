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
  eventSource = null;
  if (queueReconnectTimer) clearTimeout(queueReconnectTimer);
  clearQueuePoll();

  // Connect to SSE endpoint for real-time updates
  let source;
  try {
    source = new EventSource("/api/queue/stream");
  } catch {
    // Browsers may block streaming; polling must not prevent the page starting.
    fetchQueue();
    scheduleQueuePoll();
    return;
  }
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

// Downloads stay on the song buttons; no separate Downloads view is needed.
function updateQueueUI(jobs) {
  queueJobs = [...jobs].sort(
    (left, right) => new Date(right.created_at) - new Date(left.created_at),
  );
  const activeJobsByVideo = new Map(
    queueJobs.filter((job) => ["queued", "downloading"].includes(job.status))
      .map((job) => [job.video_id, job]),
  );
  const jobsById = new Map(queueJobs.map((job) => [job.id, job]));
  // Poll while work is active because some proxies buffer SSE events.
  if (activeJobsByVideo.size) scheduleQueuePoll(1500);
  else if (!eventSource) scheduleQueuePoll(5000);
  else clearQueuePoll();

  document.querySelectorAll(".download-btn[data-video-id]").forEach((button) => {
    const job = jobsById.get(button.dataset.queueJobId) || activeJobsByVideo.get(button.dataset.videoId);
    if (job && ["queued", "downloading"].includes(job.status)) {
      renderInlineDownload(button, job);
    } else if (button.dataset.queueJobId) {
      resetInlineDownloadButton(button);
    }
  });
}
