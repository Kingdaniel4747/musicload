// Settings and cookie management
const settingsBtn = document.getElementById("settings-btn");
const settingsModal = document.getElementById("settings-modal");
const settingsClose = document.getElementById("settings-close");
const settingsForm = document.getElementById("settings-form");
const settingsMessage = document.getElementById("settings-message");
const cookieFileInput = document.getElementById("cookie-file-input");
const uploadCookieBtn = document.getElementById("upload-cookie-btn");
const deleteCookieBtn = document.getElementById("delete-cookie-btn");
const cookieStatus = document.getElementById("cookie-status");
const findDuplicatesSettingsBtn = document.getElementById("find-duplicates-settings-btn");

const settingsFields = {
  audio_format: "setting-audio-format",
  filename_template: "setting-filename-template",
  organization_mode: "setting-organization-mode",
  use_primary_artist: "setting-primary-artist",
  web_playlist_name: "setting-web-playlist",
  gotify_url: "setting-gotify-url",
  cookie_mode: "setting-cookie-mode",
  multi_user: "setting-multi-user",
  allow_ugc: "setting-allow-ugc",
  navidrome_url: "setting-navidrome-url",
  session_https_only: "setting-session-https-only",
  listenbrainz_web: "setting-listenbrainz-web",
};

function populateSettings(data) {
  for (const [name, id] of Object.entries(settingsFields)) {
    const input = document.getElementById(id);
    if (!input) continue;
    const value = data.values[name];
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = value ?? "";
  }
  const sessionSecret = document.getElementById("setting-session-secret");
  const gotifyToken = document.getElementById("setting-gotify-token");
  sessionSecret.value = "";
  gotifyToken.value = "";
  sessionSecret.placeholder = data.configured.session_secret
    ? "Configured — leave blank to keep"
    : "Minimum 32 characters";
  gotifyToken.placeholder = data.configured.gotify_token
    ? "Configured — leave blank to keep"
    : "Optional application token";
  document.getElementById("clear-session-secret").checked = false;
  document.getElementById("clear-gotify-token").checked = false;
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Could not load settings");
  populateSettings(data);
}

settingsBtn.addEventListener("click", async () => {
  settingsModal.classList.add("active");
  settingsMessage.textContent = "Loading settings…";
  settingsMessage.className = "settings-message";
  try {
    await Promise.all([loadSettings(), refreshCookieStatus()]);
    settingsMessage.textContent = "";
  } catch (error) {
    settingsMessage.textContent = error.message;
    settingsMessage.className = "settings-message error";
  }
});
settingsClose.addEventListener("click", () => {
  settingsModal.classList.remove("active");
});
settingsModal.addEventListener("click", (event) => {
  if (event.target === settingsModal) {
    settingsModal.classList.remove("active");
  }
});
findDuplicatesSettingsBtn.addEventListener("click", () => {
  settingsModal.classList.remove("active");
  updateUrlParams("", "library");
  setActiveTab("library", true);
  loadLibraryDuplicates();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && settingsModal.classList.contains("active")) {
    settingsModal.classList.remove("active");
  }
});

document.getElementById("generate-session-secret").addEventListener("click", () => {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  document.getElementById("setting-session-secret").value = Array.from(
    bytes,
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  document.getElementById("clear-session-secret").checked = false;
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const saveButton = document.getElementById("save-settings-btn");
  saveButton.disabled = true;
  saveButton.textContent = "Saving…";
  settingsMessage.textContent = "";
  settingsMessage.className = "settings-message";
  const value = (id) => document.getElementById(id).value.trim();
  const checked = (id) => document.getElementById(id).checked;
  const payload = {
    audio_format: value("setting-audio-format"),
    filename_template: value("setting-filename-template"),
    organization_mode: value("setting-organization-mode"),
    use_primary_artist: checked("setting-primary-artist"),
    web_playlist_name: value("setting-web-playlist") || null,
    gotify_url: value("setting-gotify-url") || null,
    gotify_token: value("setting-gotify-token") || null,
    clear_gotify_token: checked("clear-gotify-token"),
    cookie_mode: value("setting-cookie-mode"),
    multi_user: checked("setting-multi-user"),
    allow_ugc: checked("setting-allow-ugc"),
    navidrome_url: value("setting-navidrome-url") || null,
    session_secret: value("setting-session-secret") || null,
    clear_session_secret: checked("clear-session-secret"),
    session_https_only: checked("setting-session-https-only"),
    listenbrainz_web: checked("setting-listenbrainz-web"),
  };
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Could not save settings");
    populateSettings(data);
    formatSelector.value = data.values.audio_format;
    localStorage.setItem("audioFormat", data.values.audio_format);
    settingsMessage.textContent = data.message;
    settingsMessage.className = "settings-message success";
  } catch (error) {
    settingsMessage.textContent = error.message;
    settingsMessage.className = "settings-message error";
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "Save settings";
  }
});

document.getElementById("reset-settings-btn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  await runConfirmedAction({
    button,
    title: "Reset web settings?",
    message: "Musicload will use environment values and built-in defaults again after a restart. Uploaded cookies are kept.",
    actionLabel: "Reset Settings",
    pendingLabel: "Resetting…",
    errorLabel: "Reset failed",
    action: async () => {
      const response = await fetch("/api/settings", { method: "DELETE" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Reset failed");
      await loadSettings();
      settingsMessage.textContent = data.message;
      settingsMessage.className = "settings-message success";
    },
  });
});

// Refresh cookie status
async function refreshCookieStatus() {
  try {
    const response = await fetch("/api/settings/cookies/status");
    const data = await response.json();

    const indicator = cookieStatus.querySelector(".status-indicator");
    const text = cookieStatus.querySelector(".status-text");

    if (data.configured && data.exists) {
      indicator.className = "status-indicator status-success";
      text.textContent = `Cookie file configured (${data.source})`;
      deleteCookieBtn.style.display =
        data.source === "uploaded" ? "inline-block" : "none";
    } else if (data.configured && !data.exists) {
      indicator.className = "status-indicator status-warning";
      text.textContent = `Cookie file configured but not found`;
      deleteCookieBtn.style.display = "none";
    } else {
      indicator.className = "status-indicator status-none";
      text.textContent = "No cookie file configured";
      deleteCookieBtn.style.display = "none";
    }
  } catch (error) {
    console.error("Failed to get cookie status:", error);
    const indicator = cookieStatus.querySelector(".status-indicator");
    const text = cookieStatus.querySelector(".status-text");
    indicator.className = "status-indicator status-error";
    text.textContent = "Error checking cookie status";
  }
}

// Upload cookie file
uploadCookieBtn.addEventListener("click", () => {
  cookieFileInput.click();
});

cookieFileInput.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);

  uploadCookieBtn.disabled = true;
  uploadCookieBtn.textContent = "Uploading...";

  try {
    const response = await fetch("/api/settings/cookies/upload", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();

    if (response.ok) {
      showStatus("Cookie file uploaded successfully");
      await refreshCookieStatus();
    } else {
      throw new Error(data.detail || "Upload failed");
    }
  } catch (error) {
    showStatus("Failed to upload cookie file: " + error.message, true);
  } finally {
    uploadCookieBtn.disabled = false;
    uploadCookieBtn.textContent = "Upload cookies.txt";
    cookieFileInput.value = "";
  }
});

// Delete cookie file
deleteCookieBtn.addEventListener("click", async () => {
  await runConfirmedAction({
    button: deleteCookieBtn,
    title: "Delete uploaded cookie?",
    message: "This removes the cookies.txt file stored by Musicload. You can upload a new file later.",
    actionLabel: "Delete Cookie",
    errorLabel: "Failed to delete cookie file",
    action: async () => {
      const response = await fetch("/api/settings/cookies", {
        method: "DELETE",
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Delete failed");
      showStatus("Cookie file deleted successfully");
      await refreshCookieStatus();
    },
  });
});
