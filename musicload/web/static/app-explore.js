// --- Explore functionality ---
let exploreLoaded = false;
let exploreBreadcrumb = []; // tracks navigation: [{type, title, params?}]

async function loadExploreContent() {
  if (exploreLoaded && exploreBreadcrumb.length === 0) return;
  if (exploreBreadcrumb.length > 0) return; // Don't reload if navigating

  exploreLoaded = true;
  exploreResults.innerHTML =
    '<div class="explore-loading">Loading explore content...</div>';

  // Fetch moods, charts, and new releases independently so one failure does not block the others
  let moods = null;
  let charts = null;
  let newReleases = null;

  const [moodsResult, chartsResult, newReleasesResult] = await Promise.allSettled([
    fetch("/api/explore/moods").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
    fetch("/api/explore/charts?country=ZZ").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
    fetch("/api/explore/new-releases").then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    }),
  ]);

  if (moodsResult.status === "fulfilled") {
    moods = moodsResult.value;
  } else {
    console.error("Failed to load moods:", moodsResult.reason);
  }

  if (chartsResult.status === "fulfilled") {
    charts = chartsResult.value;
  } else {
    console.error("Failed to load charts:", chartsResult.reason);
  }

  if (newReleasesResult.status === "fulfilled") {
    newReleases = newReleasesResult.value;
  } else {
    console.error("Failed to load new releases:", newReleasesResult.reason);
  }

  if (!moods && !charts && !newReleases) {
    exploreResults.innerHTML =
      '<div class="explore-error">Failed to load explore content. Please try again later.</div>';
    exploreLoaded = false;
    return;
  }

  renderExploreHome(moods, charts, false, newReleases);
}
function renderExploreHome(moods, charts, skipUrlUpdate = false, newReleases = null) {
  if (!skipUrlUpdate) chartPage = 0;
  exploreBreadcrumb = [];
  // Cache moods and new releases so country selector changes skip re-fetching them
  if (moods) {
    cachedMoods = moods;
  }
  if (newReleases) {
    cachedNewReleases = newReleases;
  }
  // Update URL to reflect charts country if not ZZ
  if (!skipUrlUpdate) {
    const chartsCountryCode =
      charts && charts.country ? charts.country : "ZZ";
    if (chartsCountryCode !== "ZZ") {
      updateExploreUrlParams("charts", { country: chartsCountryCode });
    } else {
      updateExploreUrlParams("home");
    }
  }
  let html = '<div class="explore-home">';

  // Charts section -- always render the header with country selector
  const chartsCountry = charts && charts.country ? charts.country : "ZZ";
  const chartTracks = charts && charts.tracks ? charts.tracks : [];

  html += `
          <div class="explore-charts-section">
              <div class="explore-section-header">
                  <h2>Top Charts (${escapeHtml(chartsCountry === "ZZ" ? "Global" : chartsCountry)})</h2>
                  <div class="explore-section-actions">
                      <select id="charts-country-selector" class="country-selector">
                          <option value="ZZ" ${chartsCountry === "ZZ" ? "selected" : ""}>Global</option>
                          <option value="US" ${chartsCountry === "US" ? "selected" : ""}>United States</option>
                          <option value="GB" ${chartsCountry === "GB" ? "selected" : ""}>United Kingdom</option>
                          <option value="DE" ${chartsCountry === "DE" ? "selected" : ""}>Germany</option>
                          <option value="FR" ${chartsCountry === "FR" ? "selected" : ""}>France</option>
                          <option value="JP" ${chartsCountry === "JP" ? "selected" : ""}>Japan</option>
                          <option value="KR" ${chartsCountry === "KR" ? "selected" : ""}>South Korea</option>
                          <option value="BR" ${chartsCountry === "BR" ? "selected" : ""}>Brazil</option>
                          <option value="IN" ${chartsCountry === "IN" ? "selected" : ""}>India</option>
                          <option value="AU" ${chartsCountry === "AU" ? "selected" : ""}>Australia</option>
                          <option value="CA" ${chartsCountry === "CA" ? "selected" : ""}>Canada</option>
                      </select>
                      ${chartTracks.length > 0 ? '<button class="download-all-btn" id="download-all-charts">Download All</button>' : ""}
                  </div>
              </div>
      `;

  if (chartTracks.length > 0) {
    const start = chartPage * TRACKS_PER_PAGE;
    const pageTracks = chartTracks.slice(start, start + TRACKS_PER_PAGE);
    html += `
              <div class="chart-tracks-list">
                  ${pageTracks
                    .map(
                      (track) => `
                      <div class="track">
                          <div class="track-main">
                              <img class="track-cover" src="${proxyImageUrl(track.thumbnail_url)}" alt="" loading="lazy" onerror="this.style.display='none'">
                              <div class="track-info">
                                  <div class="track-title">${escapeHtml(track.title)}${isUgcVideoType(track.video_type) ? '<span class="ugc-badge">UGC</span>' : ""}</div>
                                  <div class="track-artist">${escapeHtml(track.artist)}</div>
                                  <div class="track-meta">
                                      ${track.duration ? `<span class="track-duration">${track.duration}</span>` : ""}
                                      ${track.view_count ? `<span class="track-views">${track.view_count} views</span>` : ""}
                                      ${track.trend ? `<span class="chart-trend trend-${track.trend}">${track.trend === "up" ? "&#9650;" : track.trend === "down" ? "&#9660;" : "&#8212;"}</span>` : ""}
                                  </div>
                              </div>
                          </div>
                          <div class="track-actions">
                              <button class="play-btn" data-video-id="${track.video_id}" aria-label="Play preview">&#9654;</button>
                              <button class="download-btn" data-video-id="${track.video_id}" data-title="${escapeHtml(track.title)}" data-artist="${escapeHtml(track.artist)}">Download</button>
                          </div>
                      </div>
                  `,
                    )
                    .join("")}
              </div>
              ${renderPaginationControls(chartPage, chartTracks.length, "chart-page")}
          `;
  } else {
    html +=
      '<div class="explore-charts-empty">No chart tracks available. Try selecting a different country.</div>';
  }

  html += "</div>";

  // New Releases section
  const releaseAlbums = (newReleases || cachedNewReleases || []);
  if (releaseAlbums.length > 0) {
    const nrStart = newReleasesPage * ALBUMS_PER_PAGE;
    const pageAlbums = releaseAlbums.slice(nrStart, nrStart + ALBUMS_PER_PAGE);
    html += `
      <div class="explore-new-releases-section">
        <div class="explore-section-header">
          <h2>New Releases</h2>
          <div class="explore-section-actions">
          </div>
        </div>
        <div class="new-releases-grid">
          ${pageAlbums.map((album) => `
            <div class="new-release-card">
              <img class="new-release-cover" src="${proxyImageUrl(album.thumbnail_url)}" alt="${escapeHtml(album.title)}" loading="lazy" onerror="this.style.display='none'">
              <div class="new-release-info">
                <div class="new-release-title">${escapeHtml(album.title)}${album.is_explicit ? '<span class="explicit-badge">E</span>' : ""}</div>
                <div class="new-release-artist">${escapeHtml(album.artist)}</div>
                <div class="new-release-meta">
                  ${album.album_type ? `<span>${escapeHtml(album.album_type)}</span>` : ""}
                  ${album.year ? `<span>${album.year}</span>` : ""}
                </div>
              </div>
              <div class="new-release-actions">
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
          `).join("")}
        </div>
        ${renderPaginationControls(newReleasesPage, releaseAlbums.length, "new-releases-page", ALBUMS_PER_PAGE)}
      </div>`;
  }

  // Moods & Genres section
  if (moods && moods.length > 0) {
    html += '<div class="explore-moods-section">';
    for (const section of moods) {
      html += `
                  <div class="mood-section">
                      <h2>${escapeHtml(section.title)}</h2>
                      <div class="mood-categories-grid">
                          ${section.categories
                            .map(
                              (cat) => `
                              <button class="mood-category-card" data-params="${escapeHtml(cat.params)}" data-title="${escapeHtml(cat.title)}">
                                  ${escapeHtml(cat.title)}
                              </button>
                          `,
                            )
                            .join("")}
                      </div>
                  </div>
              `;
    }
    html += "</div>";
  }

  html += "</div>";
  exploreResults.innerHTML = html;
  attachExploreHandlers(charts);
}

// Cache moods data so country selector changes do not re-fetch moods
let cachedMoods = null;
let cachedNewReleases = null;

// Pagination state
const TRACKS_PER_PAGE = 10;
const ALBUMS_PER_PAGE = 8;
let chartPage = 0;
let playlistPage = 0;
let newReleasesPage = 0;
let albumExplorePage = 0;

function renderPaginationControls(page, totalTracks, idPrefix, perPage = TRACKS_PER_PAGE) {
  const totalPages = Math.ceil(totalTracks / perPage);
  if (totalPages <= 1) return "";
  return `
    <div class="pagination-controls">
      <button class="pagination-btn" id="${idPrefix}-prev" ${page === 0 ? "disabled" : ""}>&#8592; Prev</button>
      <span class="pagination-info">${page + 1} / ${totalPages}</span>
      <button class="pagination-btn" id="${idPrefix}-next" ${page >= totalPages - 1 ? "disabled" : ""}>Next &#8594;</button>
    </div>
  `;
}

function attachPaginationHandlers(idPrefix, onPageChange) {
  const prevBtn = document.getElementById(`${idPrefix}-prev`);
  const nextBtn = document.getElementById(`${idPrefix}-next`);
  if (prevBtn) prevBtn.addEventListener("click", () => onPageChange(-1));
  if (nextBtn) nextBtn.addEventListener("click", () => onPageChange(1));
}

function attachExploreHandlers(charts) {
  // Country selector
  const countrySelector = document.getElementById(
    "charts-country-selector",
  );
  if (countrySelector) {
    countrySelector.addEventListener("change", async (e) => {
      const country = e.target.value;
      chartPage = 0;
      showStatus("Loading charts...");
      try {
        const res = await fetch(
          `/api/explore/charts?country=${encodeURIComponent(country)}`,
        );
        if (!res.ok) throw new Error("Failed to load charts");
        const newCharts = await res.json();
        showStatus("");
        renderExploreHome(cachedMoods, newCharts, false, cachedNewReleases);
      } catch (error) {
        showStatus("Failed to load charts: " + error.message, true);
      }
    });
  }

  // Chart pagination
  attachPaginationHandlers("chart-page", (delta) => {
    chartPage += delta;
    renderExploreHome(cachedMoods, charts, true);
  });

  // Download all charts
  const chartTracks = charts && charts.tracks ? charts.tracks : [];
  const downloadAllChartsBtn = document.getElementById(
    "download-all-charts",
  );
  if (downloadAllChartsBtn && chartTracks.length > 0) {
    downloadAllChartsBtn.addEventListener("click", () => {
      queueMultipleTracks(chartTracks, downloadAllChartsBtn);
    });
  }

  // New releases pagination
  attachPaginationHandlers("new-releases-page", (delta) => {
    newReleasesPage += delta;
    renderExploreHome(cachedMoods, charts, true, cachedNewReleases);
  });

  // New releases album buttons — show tracks inline in explore tab
  exploreResults.querySelectorAll(".new-release-card .view-album-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const browseId = e.target.dataset.browseId;
      const card = e.target.closest(".new-release-card");
      const title = card ? card.querySelector(".new-release-title")?.textContent?.trim() : "Album";
      loadAlbumTracksInExplore(browseId, title);
    });
  });
  exploreResults.querySelectorAll(".new-release-card .download-album-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownloadAlbum);
  });

  // Mood category cards
  document.querySelectorAll(".mood-category-card").forEach((card) => {
    card.addEventListener("click", () => {
      const params = card.dataset.params;
      const title = card.dataset.title;
      loadMoodPlaylists(params, title);
    });
  });

  // Download buttons in chart tracks
  exploreResults.querySelectorAll(".download-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownload);
  });
}

async function loadMoodPlaylists(
  params,
  categoryTitle,
  skipUrlUpdate = false,
) {
  showStatus("Loading playlists...");
  if (!skipUrlUpdate) {
    updateExploreUrlParams("mood", { params, title: categoryTitle });
  }
  try {
    const res = await fetch(
      `/api/explore/mood-playlists?params=${encodeURIComponent(params)}`,
    );
    if (!res.ok) throw new Error("Failed to load playlists");
    const playlists = await res.json();
    showStatus("");
    renderMoodPlaylists(playlists, categoryTitle, params);
  } catch (error) {
    showStatus("Failed to load playlists: " + error.message, true);
  }
}

function renderMoodPlaylists(playlists, categoryTitle, params) {
  exploreBreadcrumb = [
    { type: "category", title: categoryTitle, params },
  ];
  let html = `
          <div class="explore-breadcrumb">
              <button class="breadcrumb-link" id="breadcrumb-home">Explore</button>
              <span class="breadcrumb-sep">›</span>
              <span class="breadcrumb-current">${escapeHtml(categoryTitle)}</span>
          </div>
          <div class="mood-playlists-grid">
              ${playlists
                .map(
                  (pl) => `
                  <div class="mood-playlist-card" data-playlist-id="${escapeHtml(pl.playlist_id)}" data-title="${escapeHtml(pl.title)}">
                      <img class="mood-playlist-thumb" src="${proxyImageUrl(pl.thumbnail_url)}" alt="${escapeHtml(pl.title)}" loading="lazy" onerror="this.style.display='none'">
                      <div class="mood-playlist-info">
                          <div class="mood-playlist-title">${escapeHtml(pl.title)}</div>
                          ${pl.author ? `<div class="mood-playlist-author">${escapeHtml(pl.author)}</div>` : ""}
                      </div>
                      <div class="mood-playlist-actions">
                          <button class="view-playlist-btn" data-playlist-id="${escapeHtml(pl.playlist_id)}" data-title="${escapeHtml(pl.title)}">View Tracks</button>
                          <button class="download-playlist-btn" data-playlist-id="${escapeHtml(pl.playlist_id)}" data-title="${escapeHtml(pl.title)}">Download All</button>
                      </div>
                  </div>
              `,
                )
                .join("")}
          </div>
      `;
  exploreResults.innerHTML = html;

  // Breadcrumb home link
  document
    .getElementById("breadcrumb-home")
    .addEventListener("click", () => {
      exploreBreadcrumb = [];
      exploreLoaded = false;
      updateExploreUrlParams("home");
      loadExploreContent();
    });

  // View playlist tracks
  document.querySelectorAll(".view-playlist-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      loadPlaylistTracks(btn.dataset.playlistId, btn.dataset.title);
    });
  });

  // Download all tracks from playlist
  document.querySelectorAll(".download-playlist-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      btn.textContent = "Loading...";
      try {
        const res = await fetch(
          `/api/explore/playlist/${encodeURIComponent(btn.dataset.playlistId)}/tracks`,
        );
        if (!res.ok) {
          const err = await res.json().catch(() => null);
          throw new Error(err?.detail || "Failed to load tracks");
        }
        const data = await res.json();
        await queueMultipleTracks(data.tracks, btn);
      } catch (error) {
        btn.textContent = "Failed";
        showStatus("Failed: " + error.message, true);
        setTimeout(() => {
          btn.disabled = false;
          btn.textContent = "Download All";
        }, 3000);
      }
    });
  });
}

async function loadPlaylistTracks(
  playlistId,
  playlistTitle,
  skipUrlUpdate = false,
) {
  showStatus("Loading tracks...");
  if (!skipUrlUpdate) {
    // Build URL params including parent mood context from breadcrumb
    const urlOptions = { playlistId, playlistTitle };
    if (
      exploreBreadcrumb.length > 0 &&
      exploreBreadcrumb[0].type === "category"
    ) {
      urlOptions.moodParams = exploreBreadcrumb[0].params;
      urlOptions.moodTitle = exploreBreadcrumb[0].title;
    }
    updateExploreUrlParams("playlist", urlOptions);
  }
  try {
    const res = await fetch(
      `/api/explore/playlist/${encodeURIComponent(playlistId)}/tracks`,
    );
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || "Failed to load tracks");
    }
    const data = await res.json();
    showStatus("");
    playlistPage = 0;
    renderPlaylistTracks(data.tracks, playlistTitle, playlistId);
  } catch (error) {
    showStatus("Failed to load tracks: " + error.message, true);
  }
}

function renderPlaylistTracks(tracks, playlistTitle, playlistId) {
  const prevBreadcrumb = [...exploreBreadcrumb];
  exploreBreadcrumb.push({
    type: "playlist",
    title: playlistTitle,
    playlistId,
  });

  // Preserve the parent mood so breadcrumb navigation can return to it.
  const parentMoodParams =
    prevBreadcrumb.length > 0 && prevBreadcrumb[0].params
      ? prevBreadcrumb[0].params
      : "";

  let html = `
          <div class="explore-breadcrumb">
              <button class="breadcrumb-link" id="breadcrumb-home">Explore</button>
              <span class="breadcrumb-sep">›</span>
              ${
                prevBreadcrumb.length > 0
                  ? `
                  <button class="breadcrumb-link" id="breadcrumb-category">${escapeHtml(prevBreadcrumb[0].title)}</button>
                  <span class="breadcrumb-sep">›</span>
              `
                  : ""
              }
              <span class="breadcrumb-current">${escapeHtml(playlistTitle)}</span>
          </div>
          <div class="explore-playlist-header">
              <div>
                  <h2>${escapeHtml(playlistTitle)}</h2>
                  <span class="explore-track-count">${tracks.length} tracks</span>
              </div>
              <div class="explore-section-actions">
                  <button class="download-all-btn" id="download-all-playlist">Download All</button>
              </div>
          </div>
          <div class="explore-tracks-list">
      `;

  const plStart = playlistPage * TRACKS_PER_PAGE;
  const pageTracks = tracks.slice(plStart, plStart + TRACKS_PER_PAGE);
  html += pageTracks
    .map(
      (track) => `
          <div class="track">
              <div class="track-main">
                  <img class="track-cover" src="${proxyImageUrl(track.thumbnail_url)}" alt="" loading="lazy">
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
                  <button class="play-btn" data-video-id="${track.video_id}" aria-label="Play preview">&#9654;</button>
                  <button class="download-btn" data-video-id="${track.video_id}" data-title="${escapeHtml(track.title)}" data-artist="${escapeHtml(track.artist)}">Download</button>
              </div>
          </div>
      `,
    )
    .join("");

  html += "</div>";
  html += renderPaginationControls(playlistPage, tracks.length, "playlist-page");
  exploreResults.innerHTML = html;

  // Breadcrumb navigation
  document
    .getElementById("breadcrumb-home")
    .addEventListener("click", () => {
      exploreBreadcrumb = [];
      exploreLoaded = false;
      updateExploreUrlParams("home");
      loadExploreContent();
    });

  const catBtn = document.getElementById("breadcrumb-category");
  if (catBtn && prevBreadcrumb.length > 0) {
    catBtn.addEventListener("click", () => {
      exploreBreadcrumb = [];
      loadMoodPlaylists(
        prevBreadcrumb[0].params,
        prevBreadcrumb[0].title,
      );
    });
  }

  // Download all tracks
  document
    .getElementById("download-all-playlist")
    .addEventListener("click", () => {
      queueMultipleTracks(
        tracks,
        document.getElementById("download-all-playlist"),
      );
    });

  // Individual download buttons
  exploreResults.querySelectorAll(".download-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownload);
  });

  // Playlist pagination
  attachPaginationHandlers("playlist-page", (delta) => {
    playlistPage += delta;
    renderPlaylistTracks(tracks, playlistTitle, playlistId);
  });
}

async function loadAlbumTracksInExplore(browseId, albumTitle) {
  showStatus("Loading album tracks...");
  try {
    const res = await fetch(`/api/album/${encodeURIComponent(browseId)}/tracks`);
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || "Failed to load album tracks");
    }
    const data = await res.json();
    showStatus("");
    albumExplorePage = 0;
    renderAlbumTracksInExplore(data.tracks, albumTitle || data.album_title);
  } catch (error) {
    showStatus("Failed to load album tracks: " + error.message, true);
  }
}

function renderAlbumTracksInExplore(tracks, albumTitle) {
  exploreBreadcrumb = [{ type: "album", title: albumTitle }];

  let html = `
    <div class="explore-breadcrumb">
      <button class="breadcrumb-link" id="breadcrumb-home">Explore</button>
      <span class="breadcrumb-sep">›</span>
      <span class="breadcrumb-current">${escapeHtml(albumTitle)}</span>
    </div>
    <div class="explore-playlist-header">
      <div>
        <h2>${escapeHtml(albumTitle)}</h2>
        <span class="explore-track-count">${tracks.length} tracks</span>
      </div>
      <div class="explore-section-actions">
        <button class="download-all-btn" id="download-all-album-explore">Download All</button>
      </div>
    </div>
    <div class="explore-tracks-list">`;

  const start = albumExplorePage * TRACKS_PER_PAGE;
  const pageTracks = tracks.slice(start, start + TRACKS_PER_PAGE);
  html += pageTracks.map((track) => `
    <div class="track">
      <div class="track-main">
        <img class="track-cover" src="${proxyImageUrl(track.thumbnail_url)}" alt="" loading="lazy">
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
        <button class="play-btn" data-video-id="${track.video_id}" aria-label="Play preview">&#9654;</button>
        <button class="download-btn" data-video-id="${track.video_id}" data-title="${escapeHtml(track.title)}" data-artist="${escapeHtml(track.artist)}">Download</button>
      </div>
    </div>
  `).join("");

  html += "</div>";
  html += renderPaginationControls(albumExplorePage, tracks.length, "album-explore-page");
  exploreResults.innerHTML = html;

  // Breadcrumb home
  document.getElementById("breadcrumb-home").addEventListener("click", () => {
    exploreBreadcrumb = [];
    exploreLoaded = false;
    updateExploreUrlParams("home");
    loadExploreContent();
  });

  // Download all
  document.getElementById("download-all-album-explore").addEventListener("click", () => {
    queueMultipleTracks(tracks, document.getElementById("download-all-album-explore"));
  });

  // Download buttons
  exploreResults.querySelectorAll(".download-btn").forEach((btn) => {
    btn.addEventListener("click", handleDownload);
  });

  // Pagination
  attachPaginationHandlers("album-explore-page", (delta) => {
    albumExplorePage += delta;
    renderAlbumTracksInExplore(tracks, albumTitle);
  });
}

async function queueMultipleTracks(tracks, btn) {
  if (!tracks || tracks.length === 0) {
    showStatus("No tracks to download");
    return;
  }

  const originalText = btn.textContent;
  btn.disabled = true;
  const format = formatSelector.value;
  let queued = 0;

  for (let i = 0; i < tracks.length; i++) {
    btn.textContent = `Queueing ${i + 1}/${tracks.length}...`;
    const track = tracks[i];
    try {
      const response = await fetch("/api/queue/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: track.video_id,
          title: track.title,
          artist: track.artist,
          artists: track.artists || [],
          album: track.album || null,
          audio_format: format,
        }),
      });
      if (response.ok) {
        const data = await response.json();
        if (data.status === "existing") continue;
        downloadAttention = true;
        queued++;
        await fetchQueue();
      }
    } catch (error) {
      console.error(`Failed to queue ${track.title}:`, error);
    }
  }

  if (queued === 0) {
    btn.textContent = originalText;
    btn.disabled = false;
    return;
  }
  btn.textContent = `Queued ${queued}/${tracks.length}`;
  setTimeout(() => {
    btn.textContent = originalText;
    btn.disabled = false;
  }, 3000);
}

function isUgcVideoType(videoType) {
  return videoType === "MUSIC_VIDEO_TYPE_UGC" || videoType === "MUSIC_VIDEO_TYPE_OFFICIAL_SOURCE_MUSIC";
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function proxyImageUrl(url) {
  if (!url) return "";
  return "/api/image-proxy?url=" + encodeURIComponent(url);
}
