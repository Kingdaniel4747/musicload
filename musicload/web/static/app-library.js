async function loadLibraryContent(reset = true, query = searchInput.value.trim()) {
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
        <button class="library-explore-btn" id="library-explore-btn">Explore</button>
      </div>
    </div>
    <div class="explore-tracks-list" id="library-tracks-list">
  `;

  html += libraryTracks
    .map(
      (track) => `
      <div class="track" data-entry-path="${escapeHtml(track.entry_path)}">
        <div class="track-main">
          <img class="track-cover library-cover" src="/api/library/thumbnail?entry_path=${encodeURIComponent(track.entry_path)}" alt="" loading="lazy" onerror="this.style.display='none'">
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
          <button class="library-delete-btn" data-entry-path="${escapeHtml(track.entry_path)}" data-title="${escapeHtml(track.title)}" aria-label="Delete file" title="Delete file">Delete</button>
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
  libraryDuplicateMode = true;
  libraryResults.innerHTML = `
    <div class="explore-loading">
      <span class="button-spinner" aria-hidden="true"></span>
      Scanning local files for duplicates…
    </div>`;
  try {
    const response = await fetch("/api/library/duplicates");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Duplicate scan failed");
    libraryDuplicateData = data;
    renderLibraryDuplicates(data);
  } catch (error) {
    libraryResults.innerHTML = `<div class="explore-error">Duplicate scan failed: ${escapeHtml(error.message)}</div>`;
  }
}

function duplicateSummaryText(data) {
  const groups = `${data.total_groups} ${data.total_groups === 1 ? "group" : "groups"}`;
  const files = `${data.duplicate_files} ${data.duplicate_files === 1 ? "file" : "files"}`;
  return `${groups} · ${files} · ${data.scanned_files} scanned`;
}

function renderLibraryDuplicates(data) {
  let html = `
    <div class="explore-playlist-header library-header duplicate-library-header">
      <div>
        <h2>Library duplicates</h2>
        <span class="explore-track-count">${duplicateSummaryText(data)}</span>
      </div>
      <div class="explore-section-actions">
        <button id="duplicates-back-btn" class="pagination-btn">All files</button>
        <button id="duplicates-rescan-btn" class="pagination-btn">Scan again</button>
      </div>
    </div>
    <p class="duplicate-explanation">Possible matches have the same meaningful words in their song names. Common additions such as “official audio”, “lyrics”, “copy”, and remaster years are ignored. Review the cover, song details and audio before deciding. Musicload never selects or deletes a file automatically. The current result stays in place until you select Scan again.</p>
  `;

  if (!data.groups.length) {
    html += `
      <div class="duplicates-empty">
        <strong>No duplicates found</strong>
        <span>No song names with the same meaningful words were found.</span>
      </div>`;
  } else {
    html += '<div class="duplicate-groups">';
    html += data.groups.map((group, index) => `
      <section class="duplicate-group ${group.kind}">
        <div class="duplicate-group-header">
          <div>
            <span class="duplicate-kind">Possible name match</span>
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
                <button class="library-delete-btn" data-entry-path="${escapeHtml(track.entry_path)}" data-title="${escapeHtml(track.title)}">Delete</button>
              </div>
            </div>
          `).join("")}
        </div>
      </section>
    `).join("");
    html += "</div>";
  }

  libraryResults.innerHTML = html;
  document.getElementById("duplicates-back-btn").addEventListener("click", () => {
    loadLibraryContent(true, searchInput.value.trim());
  });
  document.getElementById("duplicates-rescan-btn").addEventListener("click", loadLibraryDuplicates);
  libraryResults.querySelectorAll(".library-delete-btn").forEach((button) => {
    button.addEventListener("click", handleLibraryDelete);
  });
}

async function toggleLocalPlay(entryPath, button) {
  if (currentPlayingButton === button) {
    await toggleMiniPlayer();
    return;
  }

  stopCurrentAudio();
  button.disabled = true;
  setPlaybackButtonState(button, "loading");
  try {
    currentAudio = new Audio(`/api/library/play?entry_path=${encodeURIComponent(entryPath)}`);
    currentAudio.playsInline = true;
    currentAudio.preload = "auto";
    currentPlayingButton = button;
    currentAudio.addEventListener("canplay", () => {
      const isPlaying = !currentAudio.paused;
      setPlaybackButtonState(button, isPlaying ? "pause" : "play");
      button.disabled = false;
      button.closest(".track")?.classList.toggle("is-playing", isPlaying);
      showMiniPlayer(button);
    }, { once: true });
    currentAudio.addEventListener("ended", stopCurrentAudio, { once: true });
    currentAudio.addEventListener("error", () => {
      showStatus("The file could not be played.", true);
      stopCurrentAudio();
    }, { once: true });
    await currentAudio.play();
  } catch (error) {
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
