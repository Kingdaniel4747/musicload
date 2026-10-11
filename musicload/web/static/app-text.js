// One measured, constant-speed marquee for metadata throughout the app.
// Only visible, overflowing labels animate; DOM reads are batched per frame.
const scrollingLabels = ".track-title, .track-artist, .album-title, .album-artist, .job-title, .job-artist, .new-release-title, .new-release-artist, .mood-playlist-title, .mini-player-text-line";
const pendingLabels = new Set();
let labelFrame = 0;

function metadataText(line) {
  return line?.querySelector(":scope > .marquee-track > span")?.textContent || line?.textContent || "";
}

function scheduleLabel(line) {
  pendingLabels.add(line);
  if (!labelFrame) labelFrame = requestAnimationFrame(measureLabels);
}

const labelResizeObserver = new ResizeObserver((entries) => {
  entries.forEach(({ target }) => scheduleLabel(target));
});
const labelVisibilityObserver = new IntersectionObserver((entries) => {
  entries.forEach(({ target, isIntersecting }) => {
    target.classList.toggle("is-visible", isIntersecting);
    if (isIntersecting) scheduleLabel(target);
  });
});

function measureLabels() {
  labelFrame = 0;
  const measurements = [];
  const prepared = [];
  pendingLabels.forEach((line) => {
    if (!line.isConnected) {
      labelResizeObserver.unobserve(line);
      labelVisibilityObserver.unobserve(line);
      return;
    }
    let track = line.querySelector(":scope > .marquee-track");
    if (!track) {
      // Preserve interactive children and status badges.
      if ([...line.children].some((child) => !child.matches(".ugc-badge, .explicit-badge"))) return;
      const value = line.textContent;
      line.title = value;
      track = document.createElement("span");
      track.className = "marquee-track";
      const text = document.createElement("span");
      text.append(...line.childNodes);
      track.append(text);
      line.replaceChildren(track);
    }
    if (!line.classList.contains("scrolling-label")) {
      line.classList.add("scrolling-label");
      labelResizeObserver.observe(line);
      labelVisibilityObserver.observe(line);
    }
    prepared.push({ line, track });
  });
  pendingLabels.clear();
  prepared.forEach(({ line, track }) => {
    if (!line.clientWidth) return;
    const textWidth = track.firstElementChild.getBoundingClientRect().width;
    // The 32px gap belongs to the animation, not to the overflow test.
    const width = textWidth - (line.classList.contains("is-overflowing") ? 32 : 0);
    measurements.push({ line, track, width, overflows: width > line.clientWidth + 1 });
  });
  measurements.forEach(({ line, track, width, overflows }) => {
    line.classList.toggle("is-overflowing", overflows);
    if (overflows && track.children.length === 1) {
      const copy = track.firstElementChild.cloneNode(true);
      copy.setAttribute("aria-hidden", "true");
      track.append(copy);
    } else if (!overflows && track.children.length > 1) {
      track.lastElementChild.remove();
    }
    line.style.setProperty("--marquee-distance", `${width + 32}px`);
    line.style.setProperty("--marquee-duration", `${Math.max(10, (width + 32) / 24)}s`);
  });
}

function discoverLabels(root) {
  if (root.matches?.(scrollingLabels)) scheduleLabel(root);
  root.querySelectorAll?.(scrollingLabels).forEach(scheduleLabel);
}

const labelMutationObserver = new MutationObserver((records) => {
  records.forEach(({ target, addedNodes, removedNodes }) => {
    if (target.parentElement?.closest(".marquee-track") || target.matches?.(".marquee-track")) return;
    if (target.matches?.(scrollingLabels)) scheduleLabel(target);
    addedNodes.forEach((node) => {
      if (node.nodeType === Node.ELEMENT_NODE) discoverLabels(node);
    });
    removedNodes.forEach((node) => {
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      // Unobserve removed rows, so long browsing sessions do not retain them.
      const labels = [...node.querySelectorAll(".scrolling-label")];
      if (node.matches(".scrolling-label")) labels.push(node);
      labels.forEach((line) => {
        labelResizeObserver.unobserve(line);
        labelVisibilityObserver.unobserve(line);
      });
    });
  });
});
labelMutationObserver.observe(document.body, { childList: true, subtree: true });
document.addEventListener("visibilitychange", () => {
  document.documentElement.classList.toggle("is-background", document.hidden);
});
document.fonts.ready.then(() => discoverLabels(document));
discoverLabels(document);
