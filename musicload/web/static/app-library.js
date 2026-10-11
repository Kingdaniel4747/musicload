let maintenanceTab = "duplicates";
let emptyFolderData = null;
let maintenanceScanId = 0;
const trashIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></svg>';

function maintenanceHeader(summary = "") {
  return `<div class="explore-playlist-header library-header">
    <div><h2>Library</h2><span class="explore-track-count">${summary}</span></div>
    <div class="explore-section-actions"><button id="duplicates-back-btn" class="pagination-btn">All files</button><button id="duplicates-rescan-btn" class="btn btn-primary">Start scan</button></div>
  </div><div class="maintenance-tabs" role="tablist" aria-label="Library maintenance">
    <button role="tab" tabindex="${maintenanceTab === 'duplicates' ? 0 : -1}" aria-selected="${maintenanceTab === 'duplicates'}" data-maintenance="duplicates">Duplicates</button>
    <button role="tab" tabindex="${maintenanceTab === 'folders' ? 0 : -1}" aria-selected="${maintenanceTab === 'folders'}" data-maintenance="folders">Empty folders</button>
  </div>`;
}

function bindMaintenanceControls() {
  document.getElementById("duplicates-back-btn").onclick = () => loadLibraryContent();
  document.getElementById("duplicates-rescan-btn").onclick = () => maintenanceTab === "folders" ? scanEmptyFolders() : loadLibraryDuplicates();
  libraryResults.querySelectorAll("[data-maintenance]").forEach(button => {
    button.onclick = () => openLibraryMaintenance(button.dataset.maintenance);
    button.onkeydown = event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "duplicates" : event.key === "End" ? "folders" : maintenanceTab === "duplicates" ? "folders" : "duplicates";
      openLibraryMaintenance(next);
      libraryResults.querySelector(`[data-maintenance="${next}"]`).focus();
    };
  });
}

function openLibraryMaintenance(tab = "duplicates") {
  maintenanceScanId++;
  libraryDuplicateMode = true;
  maintenanceTab = tab;
  if (tab === "folders") renderEmptyFolders();
  else renderLibraryDuplicates(libraryDuplicateData);
}

async function scanEmptyFolders() {
  const id = ++maintenanceScanId;
  const button = document.getElementById("duplicates-rescan-btn");
  button.disabled = true;
  button.innerHTML = '<span class="button-spinner" aria-hidden="true"></span> Scanning';
  try {
    const response = await fetch("/api/library/empty-folders");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Scan failed");
    emptyFolderData = data;
    if (id === maintenanceScanId && libraryDuplicateMode) renderEmptyFolders();
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Start scan";
  }
}

function renderEmptyFolders() {
  libraryResults.innerHTML = maintenanceHeader(emptyFolderData ? `${emptyFolderData.total} folders without music` : "") +
    (!emptyFolderData ? '<div class="duplicates-empty">Start a scan</div>' : !emptyFolderData.folders.length ? '<div class="duplicates-empty">No empty folders</div>' :
      emptyFolderData.folders.map(folder => `<div class="track folder-row"><div class="folder-info"><div class="track-title">${escapeHtml(folder.entry_path)}</div><div class="track-meta">${folder.file_count} residual files${folder.can_delete ? '' : ' · Other files protected'}</div></div><button class="library-delete-btn folder-delete-btn" data-entry-path="${escapeHtml(folder.entry_path)}" aria-label="Delete folder" title="Delete folder" ${folder.can_delete ? '' : 'disabled'}>${trashIcon}</button></div>`).join(""));
  bindMaintenanceControls();
  libraryResults.querySelectorAll(".folder-delete-btn:not(:disabled)").forEach(button => {
    button.onclick = () => runConfirmedAction({button, title: "Delete folder?", message: `Delete "${button.dataset.entryPath}" and its remaining files?`, actionLabel: "Delete", errorLabel: "Delete failed", action: async () => {
      const response = await fetch(`/api/library/empty-folders?entry_path=${encodeURIComponent(button.dataset.entryPath)}`, {method: "DELETE"});
      if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "Delete failed"); }
      emptyFolderData.folders = emptyFolderData.folders.filter(folder => folder.entry_path !== button.dataset.entryPath);
      emptyFolderData.total = emptyFolderData.folders.length;
      if (libraryDuplicateMode && maintenanceTab === "folders") renderEmptyFolders();
    }});
  });
}

async function loadLibraryContent(reset = true, query = searchInput.value.trim()) {
  maintenanceScanId++;
  libraryDuplicateMode = false;
  if (reset) {
    libraryOffset = 0;
    libraryTracks = [];
    libraryResults.innerHTML =
      '<div class="explore-loading">Loading local files...</div>';
  }
  try {
    const res = await fetch(
      `/api/library/files?limit=${LIBRARY_PAGE_SIZE}&offset=${libraryOffset}&q=${encodeURIComponent(query)}`,
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    libraryTotal = data.total;
    libraryTracks = libraryTracks.concat(data.tracks);
    libraryOffset += data.tracks.length;
    libraryLoaded = true;
    renderLibraryTracks();
  } catch (error) {
    libraryResults.innerHTML = `<div class="explore-error">Failed to load local files: ${error.message}</div>`;
  }
}

function renderLibraryTracks() {
  let html = `
    <div class="explore-playlist-header library-header">
      <div>
        <h2>Local Library</h2>
        <span class="explore-track-count">${libraryTracks.length} / ${libraryTotal} files${searchInput.value.trim() ? " matching your search" : ""}</span>
      </div>
      <div class="explore-section-actions">
        <button class="pagination-btn" id="library-maintenance-btn">Clean up</button><button class="library-explore-btn" id="library-explore-btn">Explore</button>
      </div>
    </div>
    <div class="explore-tracks-list" id="library-tracks-list">
  `;

  html += libraryTracks
    .map(
      (track) => `
      <div class="track" data-entry-path="${escapeHtml(track.entry_path)}">
        <div class="track-main">
          <img class="track-cover library-cover" src="/api/library/thumbnail?entry_path=${encodeURIComponent(track.entry_path)}" alt="" loading="lazy" onerror="this.onerror=null;this.src='/static/musicload-mark.svg'">
          <div class="track-info">
            <div class="track-title">${escapeHtml(track.title)}</div>
            <div class="track-artist">${escapeHtml(track.artist)}${track.album ? " - " + escapeHtml(track.album) : ""}</div>
            <div class="track-meta">
              ${track.duration ? `<span class="track-duration">${track.duration}</span>` : ""}
              <span class="track-filesize">${(track.file_size / (1024 * 1024)).toFixed(1)} MB</span>
            </div>
          </div>
        </div>
        <div class="track-actions">
          <button class="play-btn library-play-btn" data-entry-path="${escapeHtml(track.entry_path)}" aria-label="Play preview" title="Play preview">▶</button>
          <button class="library-delete-btn" data-entry-path="${escapeHtml(track.entry_path)}" data-title="${escapeHtml(track.title)}" aria-label="Delete file" title="Delete file">${trashIcon}</button>
        </div>
      </div>
    `,
    )
    .join("");

  html += "</div>";

  if (libraryTracks.length < libraryTotal) {
    html += `
      <div class="explore-section-actions" style="justify-content:center; margin-top:16px;">
        <button class="pagination-btn" id="library-load-more">Load More (${libraryTotal - libraryTracks.length} remaining)</button>
      </div>`;
  }

  libraryResults.innerHTML = html;

  document.getElementById("library-maintenance-btn").onclick = () => openLibraryMaintenance();
  const loadMoreBtn = document.getElementById("library-load-more");
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", async () => {
      loadMoreBtn.disabled = true;
      loadMoreBtn.innerHTML = '<span class="button-spinner" aria-hidden="true"></span> Loading…';
      await loadLibraryContent(false);
    });
  }

  document.getElementById("library-explore-btn").addEventListener("click", async () => {
    const query = searchInput.value.trim();
    if (query) {
      setActiveTab("songs", true);
      updateUrlParams(query, "songs");
      await performSearch(query);
      return;
    }
    setActiveTab("explore");
    updateExploreUrlParams("home");
  });

  libraryResults.querySelectorAll(".library-delete-btn").forEach((btn) => {
    btn.addEventListener("click", handleLibraryDelete);
  });
}

async function loadLibraryDuplicates() {
  const id = ++maintenanceScanId;
  const button = document.getElementById("duplicates-rescan-btn");
  button.disabled = true;
  button.innerHTML = '<span class="button-spinner" aria-hidden="true"></span> Scanning';
  try {
    const response = await fetch("/api/library/duplicates");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Scan failed");
    libraryDuplicateData = data;
    if (id === maintenanceScanId && libraryDuplicateMode) renderLibraryDuplicates(data);
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Start scan";
  }
}

function duplicateSummaryText(data) {
  const groups = `${data.total_groups} ${data.total_groups === 1 ? "group" : "groups"}`;
  const files = `${data.duplicate_files} ${data.duplicate_files === 1 ? "file" : "files"}`;
  return `${groups} · ${files} · ${data.scanned_files} scanned`;
}

function renderLibraryDuplicates(data) {
  let html = maintenanceHeader(data ? duplicateSummaryText(data) : "");
  if (!data) {
    libraryResults.innerHTML = html + '<div class="duplicates-empty">Start a scan</div>';
    bindMaintenanceControls();
    return;
  }
  html += '<p class="duplicate-explanation">Listen before deleting</p>';

  if (!data.groups.length) {
    html += `
      <div class="duplicates-empty">
        <strong>No duplicates found</strong>
        <span>No matching files or similar titles.</span>
      </div>`;
  } else {
    html += '<div class="duplicate-groups">';
    html += data.groups.map((group, index) => `
      <section class="duplicate-group ${group.kind}">
        <div class="duplicate-group-header">
          <div>
            <span class="duplicate-kind">${escapeHtml(group.label || "Similar titles")}</span>
            <strong class="duplicate-group-count">${group.tracks.length} matching files</strong>
          </div>
          <span class="duplicate-group-number">Group ${index + 1}</span>
        </div>
        <div class="explore-tracks-list">
          ${group.tracks.map((track) => `
            <div class="track duplicate-track" data-entry-path="${escapeHtml(track.entry_path)}">
              <div class="track-main">
                <img class="track-cover library-cover" src="/api/library/thumbnail?entry_path=${encodeURIComponent(track.entry_path)}" alt="" loading="lazy" onerror="this.onerror=null;this.src='/static/musicload-mark.svg'">
                <div class="track-info">
                  <div class="track-title">${escapeHtml(track.title)}</div>
                  <div class="track-artist">${escapeHtml(track.artist)}${track.album ? " - " + escapeHtml(track.album) : ""}</div>
                  <div class="track-meta">
                    ${track.duration ? `<span>${track.duration}</span>` : ""}
                    <span>${(track.file_size / (1024 * 1024)).toFixed(1)} MB</span>
                    <span>${escapeHtml((track.format || "audio").toUpperCase())}</span>
                  </div>
                  <div class="duplicate-path">${escapeHtml(track.entry_path)}</div>
                </div>
              </div>
              <div class="track-actions">
                <button class="play-btn library-play-btn" data-entry-path="${escapeHtml(track.entry_path)}" aria-label="Play file">▶</button>
                <button class="library-delete-btn" data-entry-path="${escapeHtml(track.entry_path)}" data-title="${escapeHtml(track.title)}" aria-label="Delete file" title="Delete file">${trashIcon}</button>
              </div>
            </div>
          `).join("")}
        </div>
      </section>
    `).join("");
    html += "</div>";
  }

  libraryResults.innerHTML = html;
  bindMaintenanceControls();
  libraryResults.querySelectorAll(".library-delete-btn").forEach((button) => {
    button.addEventListener("click", handleLibraryDelete);
  });
}

async function toggleLocalPlay(entryPath, button) {
  const playbackKey = `file:${entryPath}`;
  if (currentAudio && currentPlaybackKey === playbackKey) {
    currentPlayingButton = button;
    await toggleMiniPlayer();
    return;
  }

  stopCurrentAudio();
  button.disabled = true;
  setPlaybackButtonState(button, "loading");
  try {
    const audio = new Audio(`/api/library/play?entry_path=${encodeURIComponent(entryPath)}`);
    audio.playsInline = true;
    audio.preload = "auto";
    currentAudio = audio;
    currentPlayingButton = button;
    currentPlaybackKey = playbackKey;
    bindAudioLifecycle(audio, button);
    audio.addEventListener("canplay", () => {
      if (currentAudio !== audio) return;
      const isPlaying = !audio.paused;
      setPlayingVisual(isPlaying);
      showMiniPlayer(button);
    }, { once: true });
    audio.addEventListener("ended", () => {
      if (currentAudio === audio) stopCurrentAudio();
    }, { once: true });
    audio.addEventListener("error", () => {
      if (currentAudio !== audio) return;
      showStatus("The file could not be played.", true);
      stopCurrentAudio();
    }, { once: true });
    showMiniPlayer(button);
    await audio.play();
    if (currentAudio !== audio) return;
    setPlayingVisual(!audio.paused);
    showMiniPlayer(button);
  } catch (error) {
    if (currentPlayingButton !== button) return;
    showStatus("Playback could not be started.", true);
    stopCurrentAudio();
  }
}

function removeDuplicateTrackFromCurrentScan(entryPath, trackElement) {
  if (!libraryDuplicateData || !trackElement) return;

  const groupElement = trackElement.closest(".duplicate-group");
  const group = libraryDuplicateData.groups.find((candidate) =>
    candidate.tracks.some((track) => track.entry_path === entryPath),
  );
  if (group) {
    group.tracks = group.tracks.filter((track) => track.entry_path !== entryPath);
  }
  libraryDuplicateData.duplicate_files = libraryDuplicateData.groups.reduce(
    (total, candidate) => total + candidate.tracks.length,
    0,
  );

  const summary = libraryResults.querySelector(".explore-track-count");
  if (summary) {
    summary.textContent = duplicateSummaryText(libraryDuplicateData);
  }

  trackElement.classList.add("is-removing");
  setTimeout(() => {
    trackElement.remove();
    const remaining = group?.tracks.length ?? groupElement?.querySelectorAll(".duplicate-track").length ?? 0;
    if (group && remaining === 0) {
      libraryDuplicateData.groups = libraryDuplicateData.groups.filter((candidate) => candidate !== group);
      libraryDuplicateData.total_groups = libraryDuplicateData.groups.length;
      groupElement?.remove();
      const updatedSummary = libraryResults.querySelector(".explore-track-count");
      if (updatedSummary) {
        updatedSummary.textContent = duplicateSummaryText(libraryDuplicateData);
      }
      const remainingGroups = libraryResults.querySelectorAll(".duplicate-group");
      remainingGroups.forEach((element, index) => {
        const number = element.querySelector(".duplicate-group-number");
        if (number) number.textContent = `Group ${index + 1}`;
      });
      return;
    }
    const groupCount = groupElement?.querySelector(".duplicate-group-count");
    if (groupCount) {
      groupCount.textContent = remaining === 1 ? "1 file remains from this scan" : `${remaining} matching files`;
    }
  }, 220);
}

async function handleLibraryDelete(e) {
  const btn = e.currentTarget;
  const entryPath = btn.dataset.entryPath;
  const title = btn.dataset.title;
  const trackElement = btn.closest(".track");

  await runConfirmedAction({
    button: btn,
    title: "Delete local file?",
    message: `Delete "${title}" permanently from your music folder? This cannot be undone.`,
    actionLabel: "Delete File",
    errorLabel: "Delete failed",
    action: async () => {
      if (currentPlayingButton?.dataset.entryPath === entryPath) stopCurrentAudio();
      const res = await fetch(
        `/api/library/files?entry_path=${encodeURIComponent(entryPath)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        throw new Error(err?.detail || "Failed to delete file");
      }
      if (libraryDuplicateMode) {
        removeDuplicateTrackFromCurrentScan(entryPath, trackElement);
        showStatus(`Deleted: ${title}`);
        return;
      }
      libraryTracks = libraryTracks.filter((t) => t.entry_path !== entryPath);
      libraryTotal -= 1;
      const count = libraryResults.querySelector(".explore-track-count");
      if (count) {
        count.textContent = `${libraryTracks.length} / ${libraryTotal} files${searchInput.value.trim() ? " matching your search" : ""}`;
      }
      trackElement?.classList.add("is-removing");
      setTimeout(() => trackElement?.remove(), 220);
      showStatus(`Deleted: ${title}`);
    },
  });
}
