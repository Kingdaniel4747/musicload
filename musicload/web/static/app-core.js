const appScript = document.currentScript;
const isAdmin = appScript.dataset.isAdmin === "true";

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch((error) => {
      console.warn("PWA service worker registration failed:", error);
    });
  });
}
// Format management
const formatSelector = document.getElementById("format-selector");
const serverDefaultAudioFormat = appScript.dataset.defaultAudioFormat;

function initFormat() {
  const savedFormat = localStorage.getItem("audioFormat") || serverDefaultAudioFormat || "opus";
  formatSelector.value = savedFormat;
}

function saveFormat() {
  localStorage.setItem("audioFormat", formatSelector.value);
}

formatSelector.addEventListener("change", saveFormat);
initFormat();

// Audio playback management
let currentAudio = null;
let currentPlayingButton = null;

const playbackIcons = {
  play: '<svg class="playback-icon playback-icon-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6.5v11l9-5.5z"/></svg>',
  pause: '<svg class="playback-icon playback-icon-pause" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6.5v11M16 6.5v11"/></svg>',
  loading: '<svg class="playback-icon playback-icon-loading" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>',
};

function setPlaybackButtonState(button, state) {
  if (!button) return;
  const nextState = playbackIcons[state] ? state : "play";
  button.innerHTML = playbackIcons[nextState];
  button.dataset.playbackState = nextState;
  const label = nextState === "pause" ? "Pause playback" : nextState === "loading" ? "Loading playback" : "Play playback";
  button.setAttribute("aria-label", label);
}

function initializePlaybackButtons(root = document) {
  const buttons = [];
  if (root.matches?.(".play-btn")) buttons.push(root);
  root.querySelectorAll?.(".play-btn").forEach((button) => buttons.push(button));
  buttons.forEach((button) => {
    setPlaybackButtonState(button, "play");
    if (button.dataset.playbackBound === "true") return;
    button.dataset.playbackBound = "true";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (button.classList.contains("library-play-btn")) {
        toggleLocalPlay(button.dataset.entryPath, button);
        return;
      }
      togglePlay(button.dataset.videoId, button);
    });
  });
}

const playbackButtonObserver = new MutationObserver((mutations) => {
  mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE) initializePlaybackButtons(node);
  }));
});
playbackButtonObserver.observe(document.body, { childList: true, subtree: true });
initializePlaybackButtons();

function setMiniPlayerText(id, text) {
  const line = document.getElementById(id);
  const value = text || "";
  line.dataset.text = value;
  line.innerHTML = `<span class="mini-player-text-track"><span>${escapeHtml(value)}</span><span aria-hidden="true">${escapeHtml(value)}</span></span>`;
  requestAnimationFrame(() => {
    const firstCopy = line.querySelector(".mini-player-text-track > span");
    line.classList.toggle("is-overflowing", Boolean(firstCopy && firstCopy.scrollWidth > line.clientWidth));
  });
}

function refreshMiniPlayerText() {
  ["mini-player-title", "mini-player-artist"].forEach((id) => {
    const line = document.getElementById(id);
    if (line?.dataset.text !== undefined) setMiniPlayerText(id, line.dataset.text);
  });
}

window.addEventListener("resize", refreshMiniPlayerText);

function showMiniPlayer(button) {
  const track = button.closest(".track");
  const trackCover = track?.querySelector(".track-cover");
  const miniPlayerCover = document.getElementById("mini-player-cover");
  miniPlayerCover.onerror = () => {
    miniPlayerCover.onerror = null;
    miniPlayerCover.src = "/static/musicload-mark.svg";
  };
  if (trackCover?.src) {
    miniPlayerCover.src = trackCover.src;
    miniPlayerCover.hidden = false;
  } else {
    miniPlayerCover.src = "/static/musicload-mark.svg";
    miniPlayerCover.hidden = false;
  }
  setMiniPlayerText("mini-player-title", track?.querySelector(".track-title")?.textContent || "Playing");
  setMiniPlayerText("mini-player-artist", track?.querySelector(".track-artist")?.textContent || "");
  const isPlaying = Boolean(currentAudio && !currentAudio.paused);
  setPlaybackButtonState(document.getElementById("mini-player-toggle"), isPlaying ? "pause" : "play");
  document.getElementById("mini-player-toggle").title = isPlaying ? "Pause" : "Resume";
  document.getElementById("mini-player").hidden = false;
}

function setPlayingVisual(isPlaying) {
  if (!currentPlayingButton) return;
  setPlaybackButtonState(currentPlayingButton, isPlaying ? "pause" : "play");
  currentPlayingButton.closest(".track")?.classList.toggle("is-playing", isPlaying);
  const toggle = document.getElementById("mini-player-toggle");
  setPlaybackButtonState(toggle, isPlaying ? "pause" : "play");
  toggle.title = isPlaying ? "Pause" : "Resume";
}

function stopCurrentAudio() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if (currentPlayingButton) {
    currentPlayingButton.closest(".track")?.classList.remove("is-playing");
    setPlaybackButtonState(currentPlayingButton, "play");
    currentPlayingButton.disabled = false;
    currentPlayingButton = null;
  }
  document.getElementById("mini-player").hidden = true;
}

async function toggleMiniPlayer() {
  if (!currentAudio) return;
  if (currentAudio.paused) {
    await currentAudio.play();
    setPlayingVisual(true);
  } else {
    currentAudio.pause();
    setPlayingVisual(false);
  }
}

document.getElementById("mini-player-toggle").addEventListener("click", () => {
  toggleMiniPlayer().catch(() => showStatus("Playback failed", true));
});
document.getElementById("mini-player-stop").addEventListener("click", stopCurrentAudio);

async function togglePlay(videoId, button) {
  if (currentPlayingButton === button) {
    await toggleMiniPlayer();
    return;
  }

  stopCurrentAudio();
  button.disabled = true;
  setPlaybackButtonState(button, "loading");

  try {
    const audio = new Audio();
    audio.playsInline = true;
    audio.preload = "auto";
    currentAudio = audio;
    currentPlayingButton = button;

    audio.addEventListener("loadedmetadata", () => {
      if (currentAudio !== audio) return;
      const isPlaying = !audio.paused;
      setPlaybackButtonState(button, isPlaying ? "pause" : "play");
      button.disabled = false;
      button.closest(".track")?.classList.toggle("is-playing", isPlaying);
      showMiniPlayer(button);
    });

    audio.addEventListener("ended", () => {
      if (currentAudio === audio) stopCurrentAudio();
    });

    audio.addEventListener("error", (e) => {
      if (currentAudio !== audio) return;
      console.error("Audio playback error:", e);
      showStatus("Playback failed", true);
      stopCurrentAudio();
    });

    // Keep the expiring Google stream URL on the server. Direct browser
    // requests can lose yt-dlp's required headers and fail with HTTP 403.
    audio.src = `/api/preview/${videoId}`;
    showMiniPlayer(button);
    await audio.play();
    if (currentAudio !== audio) return;
    button.disabled = false;
    setPlayingVisual(true);
    showMiniPlayer(button);
  } catch (error) {
    if (currentPlayingButton !== button) return;
    console.error("Failed to play:", error);
    showStatus("Failed to start playback: " + error.message, true);
    stopCurrentAudio();
  }
}
