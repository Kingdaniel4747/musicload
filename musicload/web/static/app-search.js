// Search functionality
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const resultsDiv = document.getElementById("songs-results");
const searchClearBtn = document.getElementById("search-clear-btn");
let playlistEventSource = null;

// Toggle clear button visibility based on input content
function updateClearButtonVisibility() {
  if (searchInput.value.trim()) {
    searchClearBtn.classList.add("visible");
  } else {
    searchClearBtn.classList.remove("visible");
  }
}

// Clear search input
function clearSearch() {
  closePlaylistSearch();
  searchInput.value = "";
  updateClearButtonVisibility();
  searchInput.focus();

  // In the local library, only reset the local filter.
  // The clear button must not leave the Library tab.
  if (currentTab === "library") {
    showStatus("");
    updateUrlParams("", "library");
    loadLibraryContent(true, "");
    return;
  }

  if (currentTab === "downloads") {
    showStatus("");
    updateQueueUI(queueJobs);
    return;
  }

  // Clear results and return to Explore.
  songsResults.innerHTML = "";
  albumsResults.innerHTML = "";
  showStatus("");
  setActiveTab("explore");
  updateUrlParams("", "explore");
}

// Event listeners for clear button
searchInput.addEventListener("input", () => {
  updateClearButtonVisibility();
  if (currentTab === "downloads") updateQueueUI(queueJobs);
});
searchClearBtn.addEventListener("click", clearSearch);

// Initialize clear button visibility
updateClearButtonVisibility();

// Tab management for switching between Songs and Albums during search.
const tabButtons = document.querySelectorAll(".tab-btn");
const songsResults = document.getElementById("songs-results");
const albumsResults = document.getElementById("albums-results");
const libraryResults = document.getElementById("library-results");
const logsResults = document.getElementById("logs-results");
const listenbrainzResults = document.getElementById("listenbrainz-results");
let libraryLoaded = false;
let libraryTracks = [];
let libraryOffset = 0;
let libraryTotal = 0;
let libraryDuplicateMode = false;
let libraryDuplicateData = null;
const LIBRARY_PAGE_SIZE = 30;
let currentTab = "explore";
let queueJobs = [];
let downloadAttention = false;
let viewingAlbum = null; // Tracks current album view mode { id, title, query }

function updateSearchContext() {
  const placeholders = {
    explore: "Search music, artists, albums, or paste a URL...",
    songs: "Search music, artists, albums, or paste a URL...",
    albums: "Search albums or paste a URL...",
    library: "Search local files...",
    downloads: "Search downloads...",
    listenbrainz: "Search music, artists, or albums...",
    logs: "Application logs",
  };
  searchInput.placeholder = placeholders[currentTab] || placeholders.explore;
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.tab;
    exitAlbumViewMode(); // Clear album view mode when switching tabs
    setActiveTab(tab);
    const query = searchInput.value.trim();
    updateUrlParams(query, tab);
    if (query) performSearch(query);
  });
});

document.getElementById("library-btn").addEventListener("click", () => {
  exitAlbumViewMode();
  setActiveTab("library");
  updateUrlParams("", "library");
});

document.getElementById("explore-btn").addEventListener("click", () => {
  exitAlbumViewMode();
  setActiveTab("explore");
  updateExploreUrlParams("home");
});

document.getElementById("downloads-btn").addEventListener("click", () => {
  exitAlbumViewMode();
  setActiveTab("downloads");
  updateUrlParams("", "downloads");
});

const listenbrainzButton = document.getElementById("listenbrainz-btn");
if (listenbrainzButton) {
  listenbrainzButton.addEventListener("click", () => {
    if (currentTab === "listenbrainz") return;
    exitAlbumViewMode();
    setActiveTab("listenbrainz");
    updateUrlParams("", "listenbrainz");
  });
}

const logsButton = document.getElementById("logs-btn");
let activeLogSource = "web";
const logOffsets = { web: 0 };
const logContents = { web: "" };
let logsLoading = false;
if (logsButton) {
  logsButton.addEventListener("click", () => {
    exitAlbumViewMode();
    setActiveTab("logs");
    updateUrlParams("", "logs");
  });
  document.querySelectorAll(".log-source-tab").forEach((button) => {
    button.addEventListener("click", () => {
      activeLogSource = button.dataset.logSource;
      document.querySelectorAll(".log-source-tab").forEach((tab) => {
        tab.classList.toggle("active", tab === button);
      });
      document.getElementById("logs-output").textContent =
        logContents[activeLogSource];
      loadLogs();
    });
  });
  window.setInterval(() => {
    if (currentTab === "logs") loadLogs(true);
  }, 1000);
}

document.querySelectorAll(".mobile-nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.mobileTab;
    document.getElementById(`${target}-btn`).click();
  });
});

// Exit album view mode and return to normal search
function exitAlbumViewMode() {
  if (viewingAlbum) {
    viewingAlbum = null;
    hideBackToAlbumsButton();
  }
}

// Show a "Back to Albums" button when viewing album tracks
function showBackToAlbumsButton(albumTitle) {
  let backBtn = document.getElementById("back-to-albums-btn");
  if (!backBtn) {
    backBtn = document.createElement("button");
    backBtn.id = "back-to-albums-btn";
    backBtn.className = "back-to-albums-btn";
    backBtn.addEventListener("click", handleBackToAlbums);
    // Insert after the search tabs
    const searchTabs = document.querySelector(".search-tabs");
    searchTabs.parentNode.insertBefore(backBtn, searchTabs.nextSibling);
  }
  backBtn.textContent = "Back to Albums";
  backBtn.style.display = "block";
}

function hideBackToAlbumsButton() {
  const backBtn = document.getElementById("back-to-albums-btn");
  if (backBtn) {
    backBtn.style.display = "none";
  }
}

function handleBackToAlbums() {
  if (viewingAlbum && viewingAlbum.query) {
    const query = viewingAlbum.query;
    exitAlbumViewMode();
    setActiveTab("albums");
    updateUrlParams(query, "albums");
    performSearch(query);
  } else {
    exitAlbumViewMode();
    setActiveTab("albums");
    updateUrlParams(searchInput.value.trim(), "albums");
  }
}

function showStatus(message, isError = false) {
  if (isError && message) console.error(message);
}

function closePlaylistSearch() {
  if (playlistEventSource) {
    playlistEventSource.close();
    playlistEventSource = null;
  }
}

function isPlaylistQuery(query) {
  const trimmed = query.trim();
  if (!trimmed) return false;
  if (!/^https?:\/\//i.test(trimmed)) return false;

  try {
    const url = new URL(trimmed);
    const host = url.hostname.replace(/^www\./, "").toLowerCase();

    if (host === "deezer.com") {
      return url.pathname.includes("/playlist/");
    }

    if (
      host === "music.youtube.com" ||
      host === "youtube.com" ||
      host === "youtu.be"
    ) {
      return url.searchParams.has("list");
    }
  } catch (error) {
    return false;
  }

  return false;
}

function formatPlaylistProgress(progress) {
  if (progress.message) {
    return progress.message;
  }

  if (progress.stage === "matching" && progress.total) {
    const matched =
      progress.matched !== undefined
        ? ` (${progress.matched} matched)`
        : "";
    return `Matching ${progress.processed || 0}/${progress.total} tracks${matched}...`;
  }

  if (progress.stage === "resolved" && progress.total) {
    return `Found ${progress.total} tracks`;
  }

  return "Fetching playlist tracks...";
}

async function performPlaylistSearch(query) {
  closePlaylistSearch();
  songsResults.innerHTML = "";
  showStatus("Fetching playlist tracks...");

  return new Promise((resolve) => {
    const url = `/api/search/playlist/stream?q=${encodeURIComponent(query)}`;
    playlistEventSource = new EventSource(url);

    playlistEventSource.addEventListener("progress", (event) => {
      const data = JSON.parse(event.data);
      showStatus(formatPlaylistProgress(data));
    });

    playlistEventSource.addEventListener("complete", (event) => {
      const data = JSON.parse(event.data);
      closePlaylistSearch();

      if (!data.results || data.results.length === 0) {
        showStatus("No results found.");
        resolve();
        return;
      }

      showStatus(`Found ${data.results.length} tracks.`);
      renderResults(data.results);
      resolve();
    });

    playlistEventSource.addEventListener("failure", (event) => {
      const data = JSON.parse(event.data);
      closePlaylistSearch();
      showStatus(data.message || "Playlist search failed.", true);
      resolve();
    });

    playlistEventSource.onerror = () => {
      if (!playlistEventSource) return;
      closePlaylistSearch();
      showStatus("Playlist search connection failed.", true);
      resolve();
    };
  });
}

// URL query parameter handling
function getQueryParam(name) {
  const urlParams = new URLSearchParams(window.location.search);
  return urlParams.get(name);
}

function updateUrlParams(query, tab, albumId = null) {
  const url = new URL(window.location);
  // Clear all explore-specific params when updating search/tab params
  url.searchParams.delete("view");
  url.searchParams.delete("country");
  url.searchParams.delete("params");
  url.searchParams.delete("title");
  url.searchParams.delete("playlistId");
  url.searchParams.delete("playlistTitle");
  url.searchParams.delete("moodParams");
  url.searchParams.delete("moodTitle");

  if (query) {
    url.searchParams.set("q", query);
  } else {
    url.searchParams.delete("q");
  }
  if (tab && tab !== "songs") {
    url.searchParams.set("tab", tab);
  } else {
    url.searchParams.delete("tab");
  }
  if (albumId) {
    url.searchParams.set("album", albumId);
  } else {
    url.searchParams.delete("album");
  }
  window.history.pushState({}, "", url);
}

// Update URL params for explore tab navigation states
// view: 'home' | 'charts' | 'mood' | 'playlist'
function updateExploreUrlParams(view, options = {}) {
  const url = new URL(window.location);
  // Clear search-specific params
  url.searchParams.delete("q");
  url.searchParams.delete("album");
  // Always set tab=explore
  url.searchParams.set("tab", "explore");
  // Clear all explore-specific params first
  url.searchParams.delete("view");
  url.searchParams.delete("country");
  url.searchParams.delete("params");
  url.searchParams.delete("title");
  url.searchParams.delete("playlistId");
  url.searchParams.delete("playlistTitle");
  url.searchParams.delete("moodParams");
  url.searchParams.delete("moodTitle");

  if (view && view !== "home") {
    url.searchParams.set("view", view);
  }
  if (view === "charts" && options.country && options.country !== "ZZ") {
    url.searchParams.set("country", options.country);
  }
  if (view === "mood") {
    if (options.params) url.searchParams.set("params", options.params);
    if (options.title) url.searchParams.set("title", options.title);
  }
  if (view === "playlist") {
    if (options.playlistId)
      url.searchParams.set("playlistId", options.playlistId);
    if (options.playlistTitle)
      url.searchParams.set("playlistTitle", options.playlistTitle);
    // Preserve parent mood context for breadcrumb navigation
    if (options.moodParams)
      url.searchParams.set("moodParams", options.moodParams);
    if (options.moodTitle)
      url.searchParams.set("moodTitle", options.moodTitle);
  }
  window.history.pushState({}, "", url);
}

// Initialize tab from URL
function initTabFromUrl() {
  const tab = getQueryParam("tab") || "explore";
  if (tab === "songs" || tab === "albums" || tab === "explore" || tab === "library" || tab === "downloads" || (tab === "listenbrainz" && listenbrainzResults)) {
    setActiveTab(tab);
  }
}

// Set active tab (updates UI and currentTab variable)
const exploreResults = document.getElementById("explore-results");
const downloadsResults = document.getElementById("downloads-results");
const resultTypeToggle = document.getElementById("result-type-toggle");

async function loadLogs(silent = false) {
  if (!logsResults || logsLoading) return;
  logsLoading = true;
  const source = activeLogSource;
  const status = document.getElementById("logs-status");
  const output = document.getElementById("logs-output");
  if (!silent) status.textContent = "Loading logs...";
  try {
    let eof = false;
    while (!eof) {
      const response = await fetch(
        `/api/logs/${source}?offset=${logOffsets[source]}`
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not load logs.");
      logContents[source] += data.content || "";
      logOffsets[source] = data.next_offset;
      eof = data.eof;
    }
    if (activeLogSource === source) {
      const followTail =
        output.scrollHeight - output.scrollTop - output.clientHeight < 80 ||
        !output.textContent;
      output.textContent = logContents[source];
      if (followTail) output.scrollTop = output.scrollHeight;
    }
    status.textContent = "";
    status.classList.remove("error");
  } catch (error) {
    status.textContent = error.message;
    status.classList.add("error");
  } finally {
    logsLoading = false;
  }
}

function setActiveTab(tab, skipExploreLoad = false) {
  if (tab !== "listenbrainz") closeListenBrainzStream();
  currentTab = tab;
  if (tab === "downloads") downloadAttention = false;
  updateSearchContext();
  const activeMobileTab = ["songs", "albums"].includes(tab) ? "explore" : tab;
  document.querySelectorAll(".mobile-nav-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.mobileTab === activeMobileTab);
  });
  tabButtons.forEach((b) => {
    if (b.dataset.tab === tab) {
      b.classList.add("active");
    } else {
      b.classList.remove("active");
    }
  });

  songsResults.classList.remove("active");
  albumsResults.classList.remove("active");
  exploreResults.classList.remove("active");
  libraryResults.classList.remove("active");
  downloadsResults.classList.remove("active");
  if (listenbrainzResults) listenbrainzResults.classList.remove("active");
  if (logsResults) logsResults.classList.remove("active");

  // Search remains available in every view, including Explore.
  searchInput.disabled = false;
  document.getElementById("search-clear-btn").disabled = false;
  document.querySelector("#search-form button[type='submit']").disabled = false;
  document.getElementById("search-form").classList.remove("search-disabled");
  document.getElementById("search-form").style.display =
    ["explore", "library", "songs", "albums"].includes(tab) ? "" : "none";

  if (tab === "songs") {
    songsResults.classList.add("active");
    resultTypeToggle.style.display = "";
  } else if (tab === "albums") {
    albumsResults.classList.add("active");
    resultTypeToggle.style.display = "";
  } else if (tab === "explore") {
    exploreResults.classList.add("active");
    resultTypeToggle.style.display = "none";
    if (!skipExploreLoad) {
      loadExploreContent();
    }
  } else if (tab === "library") {
    libraryResults.classList.add("active");
    resultTypeToggle.style.display = "none";
    if (!skipExploreLoad) {
      loadLibraryContent();
    }
  } else if (tab === "downloads") {
    downloadsResults.classList.add("active");
    resultTypeToggle.style.display = "none";
    updateQueueUI(queueJobs);
  } else if (tab === "listenbrainz" && listenbrainzResults) {
    listenbrainzResults.classList.add("active");
    resultTypeToggle.style.display = "none";
    loadListenBrainzContent();
  } else if (tab === "logs" && logsResults) {
    logsResults.classList.add("active");
    resultTypeToggle.style.display = "none";
    loadLogs();
  }
}

let listenbrainzEventSource = null;
let listenbrainzSchedule = null;

function updateListenBrainzCountdown() {
  const countdown = document.getElementById("listenbrainz-countdown");
  if (!countdown || !listenbrainzSchedule?.auto_download) {
    if (countdown) countdown.hidden = true;
    return;
  }
  const [hours, minutes] = listenbrainzSchedule.download_time.split(":").map(Number);
  const now = new Date();
  const next = new Date(now);
  const currentWeekday = (now.getDay() + 6) % 7;
  const targetWeekday = Number(listenbrainzSchedule.download_weekday ?? 0);
  const daysAhead = (targetWeekday - currentWeekday + 7) % 7;
  next.setDate(now.getDate() + daysAhead);
  next.setHours(hours, minutes, 0, 0);
  if (next <= now) next.setDate(next.getDate() + 7);
  const seconds = Math.max(0, Math.floor((next - now) / 1000));
  const hh = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const mm = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  countdown.textContent = `Automatic download in ${hh}:${mm}:${ss}`;
  countdown.hidden = false;
}

window.setInterval(updateListenBrainzCountdown, 1000);

function closeListenBrainzStream() {
  if (listenbrainzEventSource) {
    listenbrainzEventSource.close();
    listenbrainzEventSource = null;
  }
}

function renderListenBrainzTracks(tracks, title) {
  const content = document.getElementById("listenbrainz-content");
  content.innerHTML = `
    <div class="listenbrainz-playlist-heading">
      <div>
        <h3>${escapeHtml(title || "Weekly Exploration")}</h3>
        <span>${tracks.length} matched songs</span>
      </div>
      ${tracks.length ? '<button id="listenbrainz-download-all" class="download-all-btn">Download All</button>' : ""}
    </div>
    <div class="explore-tracks-list">
      ${tracks.map((track) => `
        <div class="track">
          <div class="track-main">
            <img class="track-cover" src="${proxyImageUrl(track.thumbnail_url)}" alt="" loading="lazy">
            <div class="track-info">
              <div class="track-title">${escapeHtml(track.title)}</div>
              <div class="track-artist">${escapeHtml(track.artist)}${track.album ? " - " + escapeHtml(track.album) : ""}</div>
              <div class="track-meta"><span class="track-duration">${track.duration || ""}</span></div>
            </div>
          </div>
          <div class="track-actions">
            <button class="play-btn" data-video-id="${track.video_id}" aria-label="Play preview">▶</button>
            <button class="download-btn" data-video-id="${track.video_id}" data-title="${escapeHtml(track.title)}" data-artist="${escapeHtml(track.artist)}" data-album="${escapeHtml(track.album || "")}">Download</button>
          </div>
        </div>
      `).join("")}
    </div>
  `;

  content.querySelectorAll(".download-btn").forEach((button) => {
    button.addEventListener("click", handleDownload);
  });
  const downloadAll = document.getElementById("listenbrainz-download-all");
  if (downloadAll) {
    downloadAll.addEventListener("click", () => queueMultipleTracks(tracks, downloadAll));
  }
  updateQueueUI(queueJobs);
}

async function loadListenBrainzContent() {
  if (!listenbrainzResults) return;
  closeListenBrainzStream();
  const input = document.getElementById("listenbrainz-username");
  const content = document.getElementById("listenbrainz-content");
  content.innerHTML = '<div class="listenbrainz-empty">Loading ListenBrainz…</div>';

  try {
    const response = await fetch("/api/listenbrainz/settings");
    if (!response.ok) throw new Error("ListenBrainz is unavailable");
    const settings = await response.json();
    input.value = settings.username || "";
    document.getElementById("listenbrainz-download-weekday").value =
      String(settings.download_weekday ?? 0);
    document.getElementById("listenbrainz-download-time").value =
      settings.download_time || "03:00";
    document.getElementById("listenbrainz-auto-download").checked =
      Boolean(settings.auto_download);
    listenbrainzSchedule = settings;
    updateListenBrainzCountdown();
    if (!settings.username) {
      content.innerHTML = '<div class="listenbrainz-empty">Enter your ListenBrainz username to see your personal playlist.</div>';
      return;
    }
  } catch (error) {
    content.innerHTML = `<div class="listenbrainz-empty">${escapeHtml(error.message)}</div>`;
    return;
  }

  content.innerHTML = `
    <div class="listenbrainz-progress" role="status" aria-live="polite">
      <strong>Loading Weekly Exploration</strong>
      <span id="listenbrainz-progress-label">Fetching up to 50 songs from ListenBrainz…</span>
      <div class="listenbrainz-progress-track" aria-hidden="true"><span style="width:0%"></span></div>
    </div>`;
  const source = new EventSource("/api/listenbrainz/recommendations/stream");
  listenbrainzEventSource = source;
  source.addEventListener("progress", (event) => {
    const progress = JSON.parse(event.data);
    const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
    const label = document.getElementById("listenbrainz-progress-label");
    const bar = content.querySelector(".listenbrainz-progress-track > span");
    if (label) label.textContent = `${progress.processed} of ${progress.total} songs · ${percent}% · ${progress.matched} matched`;
    if (bar) bar.style.width = `${percent}%`;
  });
  source.addEventListener("complete", (event) => {
    const data = JSON.parse(event.data);
    closeListenBrainzStream();
    if (!data.playlist_exists) {
      content.innerHTML = '<div class="listenbrainz-empty">ListenBrainz has not created a playlist for this account yet.</div>';
      return;
    }
    renderListenBrainzTracks(data.results || [], data.playlist_title);
  });
  source.addEventListener("failure", (event) => {
    const data = JSON.parse(event.data);
    closeListenBrainzStream();
    content.innerHTML = `<div class="listenbrainz-empty">${escapeHtml(data.message || "Could not load ListenBrainz")}</div>`;
  });
  source.onerror = () => {
    if (listenbrainzEventSource !== source) return;
    closeListenBrainzStream();
    content.innerHTML = '<div class="listenbrainz-empty">The ListenBrainz connection failed.</div>';
  };
}

const listenbrainzSettingsForm = document.getElementById("listenbrainz-settings-form");
if (listenbrainzSettingsForm) {
  listenbrainzSettingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("listenbrainz-username");
    const button = listenbrainzSettingsForm.querySelector('button[type="submit"]');
    const previousUsername = listenbrainzSchedule?.username || "";
    const originalLabel = button.textContent;
    button.disabled = true;
    button.classList.add("is-saving");
    button.textContent = "Savingâ€¦";
    try {
      const response = await fetch("/api/listenbrainz/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: input.value.trim(),
          auto_download: document.getElementById("listenbrainz-auto-download").checked,
          download_weekday: Number(document.getElementById("listenbrainz-download-weekday").value),
          download_time: document.getElementById("listenbrainz-download-time").value || "03:00",
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Could not save username");
      listenbrainzSchedule = { ...listenbrainzSchedule, ...data };
      updateListenBrainzCountdown();
      button.classList.remove("is-saving");
      button.classList.add("is-saved");
      button.textContent = "Saved âœ“";
      if (previousUsername !== data.username) await loadListenBrainzContent();
    } catch (error) {
      document.getElementById("listenbrainz-content").innerHTML =
        `<div class="listenbrainz-empty">${escapeHtml(error.message)}</div>`;
    } finally {
      window.setTimeout(() => {
        button.disabled = false;
        button.classList.remove("is-saving", "is-saved");
        button.textContent = originalLabel;
      }, 1400);
    }
  });
}

// Auto-search on page load
function getSharedSongQuery() {
  const params = new URLSearchParams(window.location.search);
  const candidates = [
    params.get("shared_title"),
    params.get("shared_text"),
  ];

  for (const candidate of candidates) {
    if (!candidate || candidate.trim().toLowerCase() === "google search") continue;
    const query = candidate
      .replace(/https?:\/\/\S+/gi, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (query) return query.slice(0, 200);
  }
  return "";
}

function removeSharedSearchParams() {
  const url = new URL(window.location.href);
  url.searchParams.delete("shared_title");
  url.searchParams.delete("shared_text");
  url.searchParams.delete("shared_url");
  return url;
}

function startSharedAudioSearch(query) {
  const url = removeSharedSearchParams();
  url.searchParams.set("q", query);
  url.searchParams.set("tab", "songs");
  window.history.replaceState({}, "", url);
  searchInput.value = query;
  updateClearButtonVisibility();
  setActiveTab("songs");
  showStatus("Looking for the audio track you shared...");
  performSearch(query);
}

async function resolveSharedGoogleLink(sharedUrl) {
  try {
    const response = await fetch(`/api/share/resolve?url=${encodeURIComponent(sharedUrl)}`);
    const data = await response.json();
    if (!response.ok || !data.query) throw new Error("No song title found");
    startSharedAudioSearch(data.query);
  } catch (error) {
    const url = removeSharedSearchParams();
    window.history.replaceState({}, "", url);
    setActiveTab("explore");
    showStatus("Google shared only a link, not a readable song title. Please share the song result again.", true);
  }
}

async function initAutoSearch() {
  const query = getQueryParam("q");
  const albumId = getQueryParam("album");
  const tab = getQueryParam("tab") || "explore";
  const sharedQuery = !query ? getSharedSongQuery() : "";
  const sharedUrl = !query ? getQueryParam("shared_url") : "";

  // Android shares can contain a Google or YouTube URL.  Never resolve
  // that URL directly: use only the readable song text for the normal
  // audio-track search, which avoids downloading a music-video version.
  if (sharedQuery) {
    startSharedAudioSearch(sharedQuery);
    return;
  }

  if (sharedUrl) {
    await resolveSharedGoogleLink(sharedUrl);
    return;
  }

  // If we have an album ID, load album tracks directly
  if (albumId) {
    if (query) {
      searchInput.value = query;
    }
    loadAlbumTracks(albumId, query);
    return;
  }

  if (tab === "library") {
    setActiveTab("library");
    return;
  }

  if (tab === "downloads") {
    setActiveTab("downloads");
    return;
  }

  // Explore is the default view unless a search query is present.
  if (tab === "explore" || !query) {
    const restored = restoreExploreFromUrl();
    if (restored) return;
    setActiveTab("explore");
    return;
  }

  initTabFromUrl();
  if (query) {
    searchInput.value = query;
    performSearch(query);
  }
}

// Restore explore navigation state from URL params on page load
// Returns true if a specific explore view was restored, false for default explore home
function restoreExploreFromUrl() {
  const view = getQueryParam("view");
  const country = getQueryParam("country");
  const moodParams = getQueryParam("params");
  const moodTitle = getQueryParam("title");
  const playlistId = getQueryParam("playlistId");
  const playlistTitle = getQueryParam("playlistTitle");
  const parentMoodParams = getQueryParam("moodParams");
  const parentMoodTitle = getQueryParam("moodTitle");

  if (view === "charts") {
    // Restore charts view with specific country (skipUrlUpdate since we're restoring from URL)
    setActiveTab("explore", true);
    loadExploreContentWithCountry(country || "ZZ", true);
    return true;
  } else if (view === "mood" && moodParams) {
    // Restore mood playlists view (skipUrlUpdate since we're restoring from URL)
    setActiveTab("explore", true);
    loadMoodPlaylists(moodParams, moodTitle || "Category", true);
    return true;
  } else if (view === "playlist" && playlistId) {
    // Restore playlist tracks view (with parent mood context if available)
    setActiveTab("explore", true);
    if (parentMoodParams) {
      exploreBreadcrumb = [
        {
          type: "category",
          title: parentMoodTitle || "Category",
          params: parentMoodParams,
        },
      ];
    }
    loadPlaylistTracks(playlistId, playlistTitle || "Playlist", true);
    return true;
  }

  // No specific explore view to restore; default explore home will load via setActiveTab
  return false;
}

// Load explore home with a specific country for charts (used by URL restore and country selector)
// skipUrlUpdate: true when restoring from URL to avoid redundant pushState
async function loadExploreContentWithCountry(
  country,
  skipUrlUpdate = false,
) {
  exploreLoaded = true;
  exploreBreadcrumb = [];
  exploreResults.innerHTML =
    '<div class="explore-loading">Loading explore content...</div>';

  const [moodsResult, chartsResult, newReleasesResult] = await Promise.allSettled([
    fetch("/api/explore/moods").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
    fetch(
      `/api/explore/charts?country=${encodeURIComponent(country)}`,
    ).then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
    fetch("/api/explore/new-releases").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
  ]);

  let moods =
    moodsResult.status === "fulfilled" ? moodsResult.value : null;
  let charts =
    chartsResult.status === "fulfilled" ? chartsResult.value : null;
  let newReleases =
    newReleasesResult.status === "fulfilled" ? newReleasesResult.value : null;

  if (!moods && !charts && !newReleases) {
    exploreResults.innerHTML =
      '<div class="explore-error">Failed to load explore content. Please try again later.</div>';
    exploreLoaded = false;
    return;
  }

  renderExploreHome(moods, charts, skipUrlUpdate, newReleases);
}

// Extract search logic into reusable function
async function performSearch(query) {
  closePlaylistSearch();
  showStatus("Searching...");
  if (currentTab === "songs") {
    songsResults.innerHTML = "";
    await performSongSearch(query);
  } else if (currentTab === "albums") {
    albumsResults.innerHTML = "";
    await performAlbumSearch(query);
  }
  // Explore tab doesn't use search
}

async function performSongSearch(query) {
  if (isPlaylistQuery(query)) {
    await performPlaylistSearch(query);
    return;
  }

  try {
    const response = await fetch(
      `/api/search?q=${encodeURIComponent(query)}`,
    );

    if (!response.ok) {
      const errorData = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();

    if (data.results.length === 0) {
      showStatus("No results found.");
      return;
    }

    showStatus("");
    renderResults(data.results);
  } catch (error) {
    showStatus("Search failed: " + error.message, true);
  }
}

async function performAlbumSearch(query) {
  try {
    const response = await fetch(
      `/api/search/albums?q=${encodeURIComponent(query)}`,
    );

    if (!response.ok) {
      const errorData = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    if (data.results.length === 0) {
      showStatus("No albums found.");
      return;
    }
    showStatus("");
    renderAlbums(data.results);
  } catch (error) {
    showStatus("Album search failed: " + error.message, true);
  }
}

searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = searchInput.value.trim();
  if (!query) return;

  if (currentTab === "library") {
    updateUrlParams(query, "library");
    loadLibraryContent(true, query);
    return;
  }

  if (currentTab === "downloads") {
    updateQueueUI(queueJobs);
    return;
  }

  // Search from Explore in Songs; keep Albums selected when already active.
  const targetTab = (currentTab === "explore" || currentTab === "downloads") ? "songs" : currentTab;
  setActiveTab(targetTab, true);
  updateUrlParams(query, targetTab);
  performSearch(query);
});

// Handle browser back/forward
window.addEventListener("popstate", () => {
  const query = getQueryParam("q");
  const tab = getQueryParam("tab") || "songs";
  const albumId = getQueryParam("album");

  // Handle album view state
  if (albumId) {
    // Restore album view mode
    viewingAlbum = { id: albumId, query: query };
    loadAlbumTracks(albumId, query);
    return;
  } else {
    exitAlbumViewMode();
  }

  // Handle explore tab state restoration from URL params
  if (tab === "explore") {
    const view = getQueryParam("view");
    if (view) {
      // Reset explore state before restoring
      exploreLoaded = false;
      exploreBreadcrumb = [];
      restoreExploreFromUrl();
      return;
    }
    // No view param means explore home - reset and reload
    exploreLoaded = false;
    exploreBreadcrumb = [];
    setActiveTab("explore");
    return;
  }

  // Restore tab state
  if (tab === "songs" || tab === "albums" || tab === "library" || tab === "downloads") {
    setActiveTab(tab);
  }

  if (query) {
    searchInput.value = query;
    if (tab === "library") {
      loadLibraryContent(true, query);
    } else {
      performSearch(query);
    }
  } else {
    searchInput.value = "";
    songsResults.innerHTML = "";
    albumsResults.innerHTML = "";
    showStatus("");
  }
});

// Helper function to load album tracks (used by popstate handler)
async function loadAlbumTracks(browseId, originalQuery) {
  try {
    showStatus("Loading album tracks...");
    const response = await fetch(`/api/album/${browseId}/tracks`);

    if (!response.ok) {
      const errorData = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    showStatus("");
    viewingAlbum = {
      id: browseId,
      title: data.album_title,
      query: originalQuery,
    };

    setActiveTab("songs");
    showBackToAlbumsButton(data.album_title);
    if (originalQuery) {
      searchInput.value = originalQuery;
    }

    renderResults(data.tracks);
    showStatus(
      `Showing ${data.tracks.length} tracks from "${data.album_title}"`,
    );
  } catch (error) {
    showStatus("Failed to load album: " + error.message, true);
    exitAlbumViewMode();
  }
}

function renderAlbums(albums) {
  albumsResults.innerHTML = albums
    .map(
      (album) => `
          <div class="album-card">
              <img class="album-cover" src="${proxyImageUrl(album.thumbnail_url)}" alt="${escapeHtml(album.title)}" onerror="this.style.display='none'">
              <div class="album-info">
                  <div class="album-title">${escapeHtml(album.title)}</div>
                  <div class="album-artist">${escapeHtml(album.artist)}</div>
                  <div class="album-meta">
                      ${album.year ? `<span>${album.year}</span>` : ""}
                      ${album.track_count ? `<span>${album.track_count} tracks</span>` : ""}
                  </div>
              </div>
              <div class="album-actions">
                  <button class="view-album-btn" data-browse-id="${album.browse_id}">View Tracks</button>
                  <button class="download-album-btn"
                          data-browse-id="${album.browse_id}"
                          data-title="${escapeHtml(album.title)}"
                          data-artist="${escapeHtml(album.artist)}"
                          data-year="${album.year || ""}">
                      Download Album
                  </button>
              </div>
          </div>
      `,
    )
    .join("");

  document.querySelectorAll(".view-album-btn").forEach((btn) => {
    btn.addEventListener("click", handleViewAlbum);
  });
  document.querySelectorAll(".download-album-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownloadAlbum);
  });
}

async function handleViewAlbum(e) {
  const browseId = e.target.dataset.browseId;
  try {
    showStatus("Loading album tracks...");
    const response = await fetch(`/api/album/${browseId}/tracks`);

    if (!response.ok) {
      const errorData = await response
        .json()
        .catch(() => ({ detail: "Unknown error" }));
      throw new Error(errorData.detail || `HTTP ${response.status}`);
    }

    const data = await response.json();
    showStatus("");

    // Store album view state
    viewingAlbum = {
      id: browseId,
      title: data.album_title,
      query: searchInput.value.trim(),
    };

    // Update URL to reflect we're viewing an album's tracks
    updateUrlParams(searchInput.value.trim(), "songs", browseId);

    setActiveTab("songs");
    showBackToAlbumsButton(data.album_title);

    renderResults(data.tracks);
    showStatus(
      `Showing ${data.tracks.length} tracks from "${data.album_title}"`,
    );
  } catch (error) {
    showStatus("Failed to load album: " + error.message, true);
  }
}

async function handleDownloadAlbum(e) {
  const btn = e.target;
  const browseId = btn.dataset.browseId;
  const title = btn.dataset.title;
  const artist = btn.dataset.artist;
  const albumYear = btn.dataset.year ? Number(btn.dataset.year) : null;
  const format = formatSelector.value;

  btn.disabled = true;
  btn.textContent = "Queuing...";

  try {
    const response = await fetch("/api/queue/add-album", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        browse_id: browseId,
        album_title: title,
        artist: artist,
        album_year: albumYear,
        audio_format: format,
      }),
    });
    const data = await response.json();

    if (response.ok) {
      if (data.track_count === 0) {
        btn.textContent = "Download Album";
        btn.disabled = false;
        return;
      }
      if (data.track_count > 0) downloadAttention = true;
      btn.textContent = `Queued (${data.track_count})`;
      await fetchQueue();
      setTimeout(() => {
        btn.textContent = "Download Album";
        btn.disabled = false;
      }, 3000);
    } else {
      throw new Error(data.detail || "Unknown error");
    }
  } catch (error) {
    btn.textContent = "Failed";
    showStatus("Failed: " + error.message, true);
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Download Album";
    }, 3000);
  }
}

function renderResults(tracks) {
  // Stop any playing audio when re-rendering results
  stopCurrentAudio();

  songsResults.innerHTML = tracks
    .map(
      (track) => `
          <div class="track">
              <div class="track-main">
                  <img
                      class="track-cover"
                      src="${proxyImageUrl(track.thumbnail_url)}"
                      alt=""
                      loading="lazy"
                  >
                  <div class="track-info">
                      <div class="track-title">${escapeHtml(track.title)}${isUgcVideoType(track.video_type) ? '<span class="ugc-badge">UGC</span>' : ""}</div>
                      <div class="track-artist">${escapeHtml(track.artist)}${track.album ? " - " + escapeHtml(track.album) : ""}</div>
                      <div class="track-meta">
                          <span class="track-duration">${track.duration}</span>
                          ${track.view_count ? `<span class="track-views">${track.view_count} views</span>` : ""}
                      </div>
                  </div>
              </div>
              <div class="track-actions">
                  <button
                      class="play-btn"
                      data-video-id="${track.video_id}"
                      aria-label="Play preview"
                  >
                      ▶
                  </button>
                  <button
                      class="download-btn"
                      data-video-id="${track.video_id}"
                      data-title="${escapeHtml(track.title)}"
                      data-artist="${escapeHtml(track.artist)}"
                      data-album="${escapeHtml(track.album || "")}"
                  >
                      Download
                  </button>
              </div>
          </div>
      `,
    )
    .join("");

  // Add download button handlers
  document.querySelectorAll(".download-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownload);
  });
}
async function handleDownload(e) {
  e.stopPropagation();
  const btn = e.currentTarget;
  if (btn.dataset.queueJobId) {
    await cancelInlineDownload(btn);
    return;
  }
  const videoId = btn.dataset.videoId;
  const title = btn.dataset.title;
  const artist = btn.dataset.artist;
  const album = btn.dataset.album || null;
  const format = formatSelector.value;

  btn.disabled = true;
  const originalText = btn.dataset.defaultLabel || btn.textContent;
  btn.dataset.defaultLabel = originalText;
  btn.textContent = "Adding...";

  try {
    const response = await fetch("/api/queue/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        video_id: videoId,
        title,
        artist,
        album,
        audio_format: format,
      }),
    });
    const data = await response.json();

    if (response.ok) {
      if (data.status === "existing") {
        showExistingDownloadFeedback(btn);
        return;
      }
      downloadAttention = true;
      btn.dataset.queueJobId = data.job_id;
      renderInlineDownload(btn, {
        id: data.job_id,
        status: "queued",
        progress: 0,
        speed: "Waiting",
        eta: "",
      });
      await fetchQueue();
    } else {
      btn.textContent = "Failed";
      showStatus("Failed to add to queue", true);
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = originalText;
      }, 3000);
    }
  } catch (error) {
    btn.textContent = "Error";
    showStatus("Failed to add to queue: " + error.message, true);
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = originalText;
    }, 3000);
  }
}

function resetInlineDownloadButton(btn) {
  delete btn.dataset.queueJobId;
  delete btn.dataset.progressDetails;
  btn.classList.remove("inline-download-progress");
  btn.disabled = false;
  btn.textContent = btn.dataset.defaultLabel || "Download";
}

function showExistingDownloadFeedback(btn) {
  resetInlineDownloadButton(btn);
  btn.disabled = true;
  btn.classList.add("already-downloaded");
  btn.textContent = "Already downloaded";
  window.setTimeout(() => {
    btn.classList.remove("already-downloaded");
    resetInlineDownloadButton(btn);
  }, 2200);
}

function progressPercent(job) {
  return Math.max(0, Math.min(100, Number(job.progress) || 0));
}

function progressDetails(job) {
  const details = [`${progressPercent(job).toFixed(1)}%`];
  if (job.speed) details.push(job.speed);
  if (job.eta) details.push(`ETA ${job.eta}`);
  return details.join(" · ");
}

function progressActionMarkup(job, label) {
  return `
    <span class="button-progress-fill" style="width: ${progressPercent(job)}%"></span>
    <span class="button-progress-label">${label}</span>
  `;
}

function renderInlineDownload(btn, job) {
  btn.dataset.queueJobId = job.id;
  btn.dataset.progressDetails = progressDetails(job);
  btn.classList.add("inline-download-progress");
  btn.disabled = false;
  btn.innerHTML = progressActionMarkup(job, "Cancel");
}

async function cancelInlineDownload(btn) {
  const jobId = btn.dataset.queueJobId;
  if (!jobId) return;
  btn.disabled = true;
  btn.textContent = "Cancelling...";
  try {
    const response = await fetch(`/api/queue/${jobId}`, { method: "DELETE" });
    if (!response.ok) throw new Error("Cancellation failed");
    resetInlineDownloadButton(btn);
    showStatus("Download cancelled");
  } catch (error) {
    btn.disabled = false;
    btn.textContent = "Cancel";
    showStatus("Could not cancel the download", true);
  }
}
