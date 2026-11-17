// Frontend workflow regressions. Run with: node --test tests/test_ui.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

class Element {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.innerHTML = '';
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    const classes = new Set();
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, force) => {
        const enabled = force ?? !classes.has(name);
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    };
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  click() { return this.listeners.click?.({ target: this }); }
  focus() { this.focused = true; }
}

function app() {
  const elements = new Map();
  const get = (id) => {
    if (['downloads-btn', 'downloads-results', 'queue-list', 'queue-section', 'cancel-all-btn', 'page-title', 'page-description'].includes(id)) return null;
    if (!elements.has(id)) elements.set(id, new Element(id));
    return elements.get(id);
  };
  const tabs = ['explore', 'library', 'listenbrainz'];
  const desktop = tabs.map((tab) => get(`${tab}-btn`));
  const mobile = tabs.map((tab) => Object.assign(new Element(), { dataset: { mobileTab: tab } }));
  const download = new Element();
  download.dataset.videoId = 'song-id';
  const document = {
    getElementById: get,
    addEventListener() {},
    querySelectorAll: (selector) => {
      if (selector === '.mobile-nav-btn') return mobile;
      if (selector === '.header-controls > .icon-btn, #logs-btn') return [...desktop, get('logs-btn')];
      if (selector === '.download-btn[data-video-id]') return [download];
      return [];
    },
    querySelector: (selector) => selector.includes('data-mobile-tab') ? mobile[2] : get('search-submit'),
  };
  const window = { setInterval() {}, addEventListener(name, fn) { this[name] = fn; }, location: new URL('http://localhost/'), history: { pushState() {} } };
  const context = vm.createContext({
    document, window, console, URL, URLSearchParams, isAdmin: true,
    formatSelectorButton: get('format-selector-button'),
    closeFormatDropdown() {},
    setTimeout: () => 1, clearTimeout() {}, requestAnimationFrame() {},
    syncPlaybackVisuals() {}, loadExploreContent() {}, loadLibraryContent() {},
    escapeHtml: (value) => String(value ?? ''),
  });
  for (const file of ['app-search.js', 'app-downloads.js', 'app-settings.js']) {
    vm.runInContext(readFileSync(new URL(`../musicload/web/static/${file}`, import.meta.url), 'utf8'), context, { filename: file });
  }
  const run = (code) => vm.runInContext(code, context);
  return { get, run, mobile, download, window, document };
}

test('errors and empty search results are visible and can be cleared', () => {
  const { get, run } = app();
  run('showStatus("Connection failed", true)');
  assert.equal(get('app-status').hidden, false);
  assert.equal(get('app-status').textContent, 'Connection failed');
  assert.equal(get('app-status').classList.contains('error'), true);
  run('showStatus("No results found.")');
  assert.equal(get('app-status').classList.contains('error'), false);
  run('showStatus("")');
  assert.equal(get('app-status').hidden, true);
});

test('section navigation clears stale filters and synchronizes desktop and mobile', () => {
  const { get, mobile } = app();
  get('search-input').value = 'Previous online search';
  get('library-btn').click();
  assert.equal(get('search-input').value, '');
  assert.equal(get('library-btn').attributes['aria-current'], 'page');
  assert.equal(mobile[1].attributes['aria-current'], 'page');
  assert.equal(get('format-selector-button').hidden, true);
  get('explore-btn').click();
  assert.equal(get('library-btn').attributes['aria-current'], undefined);
  assert.equal(get('format-selector-button').hidden, false);
});

test('download progress and cancellation remain on song buttons without a Downloads view', () => {
  const { run, download } = app();
  run('updateQueueUI([{id:"1", video_id:"song-id", status:"downloading", progress:42, title:"Song", artist:"Artist", created_at:"2026-01-01"}])');
  assert.equal(download.dataset.queueJobId, '1');
  assert.equal(download.dataset.progressDetails, '42.0%');
  assert.match(download.innerHTML, /Cancel/);
  run('updateQueueUI([{id:"1", video_id:"song-id", status:"completed", title:"Song", artist:"Artist", created_at:"2026-01-01"}])');
  assert.equal(download.dataset.queueJobId, undefined);
  assert.equal(download.classList.contains('inline-download-progress'), false);
  assert.equal(download.disabled, false);
  run('updateQueueUI([])');
});

test('old Downloads bookmarks return to Explore without accessing removed elements', () => {
  const { get, run, document, window } = app();
  run('setActiveTab("downloads")');
  assert.equal(document.title, 'Explore · Musicload');
  assert.equal(get('explore-results').classList.contains('active'), true);
  window.location = new URL('http://localhost/?tab=downloads&q=stale');
  window.popstate();
  assert.equal(get('search-input').value, '');
  assert.equal(get('explore-btn').attributes['aria-current'], 'page');
});

test('browser history restores a local filter before loading the library', () => {
  const { get, run, window } = app();
  run('loadLibraryContent = () => { document.getElementById("library-results").textContent = searchInput.value; }');
  window.location = new URL('http://localhost/?tab=library&q=Restored');
  window.popstate();
  assert.equal(get('library-results').textContent, 'Restored');
  assert.equal(get('search-clear-btn').classList.contains('visible'), true);
});
