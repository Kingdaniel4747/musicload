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
const formatSelectorButton = document.getElementById("format-selector-button");
const formatSelectorLabel = document.getElementById("format-selector-label");
const formatModal = document.getElementById("format-modal");
const formatModalClose = document.getElementById("format-modal-close");
const serverDefaultAudioFormat = appScript.dataset.defaultAudioFormat;

function renderFormatSelection() {
  const selectedOption = formatSelector.options[formatSelector.selectedIndex];
  formatSelectorLabel.textContent = selectedOption?.textContent || "Opus";
  document.querySelectorAll(".format-option").forEach((button) => {
    const isSelected = button.dataset.format === formatSelector.value;
    button.classList.toggle("selected", isSelected);
    button.setAttribute("aria-checked", String(isSelected));
  });
}

function initFormat() {
  const savedFormat = localStorage.getItem("audioFormat") || serverDefaultAudioFormat || "opus";
  formatSelector.value = savedFormat;
  if (!formatSelector.value) formatSelector.value = "opus";
  renderFormatSelection();
}

function saveFormat() {
  localStorage.setItem("audioFormat", formatSelector.value);
  renderFormatSelection();
}

function openFormatModal() {
  renderFormatSelection();
  formatModal.classList.add("active");
  formatModal.querySelector(".format-option.selected")?.focus();
}

function closeFormatModal({ restoreFocus = true } = {}) {
  formatModal.classList.remove("active");
  if (restoreFocus) formatSelectorButton.focus();
}

formatSelector.addEventListener("change", saveFormat);
formatSelectorButton.addEventListener("click", openFormatModal);
formatModalClose.addEventListener("click", () => closeFormatModal());
formatModal.addEventListener("click", (event) => {
  if (event.target === formatModal) closeFormatModal();
});
formatModal.querySelectorAll(".format-option").forEach((button) => {
  button.addEventListener("click", () => {
    formatSelector.value = button.dataset.format;
    saveFormat();
    closeFormatModal();
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && formatModal.classList.contains("active")) {
    closeFormatModal();
  }
});
initFormat();

// Audio playback management
let currentAudio = null;
let currentPlayingButton = null;
let currentPlaybackKey = null;

const playbackIcons = {
  play: '<svg class="playback-icon playback-icon-play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6.5v11l9-5.5z"/></svg>',
  pause: '<svg class="playback-icon playback-icon-pause" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6.5v11M16 6.5v11"/></svg>',
  loading: '<svg class="playback-icon playback-icon-loading" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/></svg>',
};

function setPlaybackButtonState(button, state) {
  if (!button) return;
  const nextState = playbackIcons[state] ? state : "play";
  if (button.dataset.playbackState === nextState) return;
  button.innerHTML = playbackIcons[nextState];
  button.dataset.playbackState = nextState;
  const label = nextState === "pause" ? "Pause playback" : nextState === "loading" ? "Loading playback" : "Play playback";
  button.setAttribute("aria-label", label);
}

function playbackKeyForButton(button) {
  if (!button) return null;
  if (button.classList.contains("library-play-btn")) {
    return button.dataset.entryPath ? `file:${button.dataset.entryPath}` : null;
  }
  return button.dataset.videoId ? `video:${button.dataset.videoId}` : null;
}

function syncPlaybackVisuals(isPlaying = Boolean(currentAudio && !currentAudio.paused)) {
  let connectedCurrentButton = null;
  document.querySelectorAll(".play-btn").forEach((button) => {
    const matchesCurrent = Boolean(
      currentPlaybackKey && playbackKeyForButton(button) === currentPlaybackKey,
    );
    const showPlaying = matchesCurrent && isPlaying;
    setPlaybackButtonState(button, showPlaying ? "pause" : "play");
    button.disabled = false;
    button.closest(".track")?.classList.toggle("is-playing", showPlaying);
    if (matchesCurrent && !connectedCurrentButton) connectedCurrentButton = button;
  });
  if (connectedCurrentButton) currentPlayingButton = connectedCurrentButton;
}

function initializePlaybackButtons(root = document) {
  const buttons = [];
  if (root.matches?.(".play-btn")) buttons.push(root);
  root.querySelectorAll?.(".play-btn").forEach((button) => buttons.push(button));
  buttons.forEach((button) => {
    if (button.dataset.playbackBound !== "true") {
      button.dataset.playbackBound = "true";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (button.classList.contains("library-play-btn")) {
          toggleLocalPlay(button.dataset.entryPath, button).catch(() => showStatus("Playback could not be resumed.", true));
          return;
        }
        togglePlay(button.dataset.videoId, button).catch(() => showStatus("Playback could not be resumed.", true));
      });
    }
    const isCurrentAndPlaying = Boolean(
      currentAudio &&
      !currentAudio.paused &&
      currentPlaybackKey &&
      playbackKeyForButton(button) === currentPlaybackKey
    );
    setPlaybackButtonState(button, isCurrentAndPlaying ? "pause" : "play");
    button.closest(".track")?.classList.toggle("is-playing", isCurrentAndPlaying);
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
  if (line.dataset.text === value) return;
  line.dataset.text = value;
  line.textContent = value;
}

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
  setMiniPlayerText("mini-player-title", metadataText(track?.querySelector(".track-title")) || "Playing");
  setMiniPlayerText("mini-player-artist", metadataText(track?.querySelector(".track-artist")));
  const isPlaying = Boolean(currentAudio && !currentAudio.paused);
  setPlaybackButtonState(document.getElementById("mini-player-toggle"), isPlaying ? "pause" : "play");
  document.getElementById("mini-player-toggle").title = isPlaying ? "Pause" : "Resume";
  document.getElementById("mini-player").hidden = false;
}

function setPlayingVisual(isPlaying) {
  syncPlaybackVisuals(isPlaying);
  const toggle = document.getElementById("mini-player-toggle");
  setPlaybackButtonState(toggle, isPlaying ? "pause" : "play");
  toggle.title = isPlaying ? "Pause" : "Resume";
}

function stopCurrentAudio() {
  if (currentAudio) {
    const audio = currentAudio;
    currentAudio = null;
    window.clearTimeout(audio.playbackStartTimer);
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  if (currentPlayingButton) {
    currentPlayingButton.disabled = false;
  }
  currentPlaybackKey = null;
  currentPlayingButton = null;
  syncPlaybackVisuals(false);
  document.getElementById("mini-player").hidden = true;
}

async function toggleMiniPlayer() {
  if (!currentAudio) return;
  const audio = currentAudio;
  if (audio.paused) {
    await audio.play();
    if (currentAudio === audio) setPlayingVisual(!audio.paused);
  } else {
    currentAudio.pause();
    setPlayingVisual(false);
  }
}

document.getElementById("mini-player-toggle").addEventListener("click", () => {
  toggleMiniPlayer().catch(() => showStatus("Playback failed", true));
});
document.getElementById("mini-player-stop").addEventListener("click", stopCurrentAudio);

function bindAudioLifecycle(audio, button) {
  audio.playbackStartTimer = window.setTimeout(() => {
    if (currentAudio !== audio) return;
    stopCurrentAudio();
    showStatus("Playback took too long. Please try again.", true);
  }, 60000);
  audio.addEventListener("playing", () => {
    window.clearTimeout(audio.playbackStartTimer);
    if (currentAudio !== audio) return;
    setPlayingVisual(!audio.paused);
    showMiniPlayer(button);
  });
  audio.addEventListener("pause", () => {
    if (currentAudio === audio) setPlayingVisual(false);
  });
  audio.addEventListener("waiting", () => {
    if (currentAudio !== audio) return;
    setPlaybackButtonState(currentPlayingButton, "loading");
    setPlaybackButtonState(document.getElementById("mini-player-toggle"), "loading");
  });
}

async function togglePlay(videoId, button) {
  const playbackKey = `video:${videoId}`;
  if (currentAudio && currentPlaybackKey === playbackKey) {
    currentPlayingButton = button;
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
    currentPlaybackKey = playbackKey;
    bindAudioLifecycle(audio, button);

    const handlePlaybackFailure = (error) => {
      if (currentAudio !== audio) return;
      console.error("Audio playback error:", error);
      showStatus("Playback failed", true);
      stopCurrentAudio();
    };

    audio.addEventListener("loadedmetadata", () => {
      if (currentAudio !== audio) return;
      const isPlaying = !audio.paused;
      setPlayingVisual(isPlaying);
      showMiniPlayer(button);
    });

    audio.addEventListener("ended", () => {
      if (currentAudio === audio) stopCurrentAudio();
    });

    audio.addEventListener("error", (error) => {
      handlePlaybackFailure(error);
    });

    // Never expose a short-lived googlevideo URL to the browser. The
    // server validates a working upstream before returning audio/mpeg.
    audio.src = `/api/preview/${encodeURIComponent(videoId)}`;
    showMiniPlayer(button);
    await audio.play();
    if (currentAudio !== audio) return;
    button.disabled = false;
    setPlayingVisual(!audio.paused);
    showMiniPlayer(button);
  } catch (error) {
    if (currentPlayingButton !== button) return;
    console.error("Failed to play:", error);
    showStatus("Failed to start playback: " + error.message, true);
    stopCurrentAudio();
  }
}
