// Load the complete deferred script chain, including the real format controls.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const template = readFileSync(new URL('../musicload/web/templates/index.html', import.meta.url), 'utf8');

async function boot({ storageBlocked = false, missingDropdown = false, streamingBlocked = false, savedCountry = null } = {}) {
  const elements = new Map();
  const calls = [];
  const stored = new Map();
  if (savedCountry) stored.set('chartCountry', savedCountry);
  let document;
  class Element {
    constructor(id = '') {
      this.id = id; this.dataset = {}; this.style = {}; this.value = ''; this.hidden = false;
      this.listeners = {}; this.textContent = ''; this._html = '';
      const classes = new Set();
      this.classList = {
        add: (name) => classes.add(name), remove: (name) => classes.delete(name),
        contains: (name) => classes.has(name),
        toggle: (name, force) => { if (force ?? !classes.has(name)) classes.add(name); else classes.delete(name); },
      };
    }
    set innerHTML(value) {
      this._html = value;
      for (const match of value.matchAll(/\bid="([^"]+)"/g)) {
        if (!elements.has(match[1])) elements.set(match[1], new Element(match[1]));
      }
    }
    get innerHTML() { return this._html || this.textContent; }
    addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
    dispatch(name, extra = {}) {
      return Promise.all((this.listeners[name] || []).map((callback) => callback({ target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...extra })));
    }
    click() { return this.dispatch('click'); }
    focus() { document.activeElement = this; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    querySelectorAll(selector) { return selector === '.format-option' ? options : []; }
    querySelector() { return null; }
    matches() { return false; }
    contains(target) { return target === this || target?.parentElement === this; }
  }
  for (const match of template.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], new Element(match[1]));
  const get = (id) => elements.get(id) || null;
  const options = ['opus', 'mp3', 'flac'].map((format) => {
    const option = new Element(); option.dataset.format = format; return option;
  });
  const formatControl = new Element();
  get('format-selector-button').parentElement = formatControl;
  get('format-dropdown').parentElement = formatControl;
  get('format-dropdown').hidden = true;
  options.forEach((option) => { option.parentElement = formatControl; });
  if (missingDropdown) elements.delete('format-dropdown');
  get('format-selector').options = [{ textContent: 'Opus' }, { textContent: 'MP3' }, { textContent: 'FLAC' }];
  Object.defineProperty(get('format-selector'), 'selectedIndex', { get() { return ['opus', 'mp3', 'flac'].indexOf(this.value); } });
  const submit = new Element();
  const mobile = ['explore', 'library', 'listenbrainz'].map((tab) => {
    const element = new Element(); element.dataset.mobileTab = tab; return element;
  });
  document = Object.assign(new Element(), {
    body: new Element(), documentElement: new Element(),
    currentScript: { dataset: { isAdmin: 'true', defaultAudioFormat: 'opus' } },
    getElementById: get, createElement: () => new Element(), fonts: { ready: Promise.resolve() },
    querySelector: () => submit,
    querySelectorAll: (selector) => {
      if (selector === '.mobile-nav-btn') return mobile;
      if (selector === '.header-controls > .icon-btn, #logs-btn') return ['explore', 'library', 'listenbrainz', 'logs'].map((tab) => get(`${tab}-btn`));
      return [];
    },
  });
  const window = Object.assign(new Element(), {
    location: new URL('http://localhost/'), history: {
      pushState(_state, _title, url) { window.location = new URL(url); },
      replaceState(_state, _title, url) { window.location = new URL(url); },
    },
    setInterval() {}, setTimeout() {}, clearTimeout() {},
  });
  class Observer { observe() {} unobserve() {} disconnect() {} }
  class EventSource {
    constructor(url) { if (streamingBlocked) throw new Error('Streaming blocked'); calls.push(url); }
    addEventListener() {} close() {}
  }
  const context = vm.createContext({
    document, window, navigator: {}, URL, URLSearchParams, console,
    localStorage: {
      getItem(key) { if (storageBlocked) throw new Error('Storage access denied'); return stored.get(key); },
      setItem(key, value) { if (storageBlocked) throw new Error('Storage access denied'); stored.set(key, value); },
    },
    MutationObserver: Observer, ResizeObserver: Observer, IntersectionObserver: Observer,
    Node: { ELEMENT_NODE: 1 }, EventSource, requestAnimationFrame: () => 1,
    setTimeout: () => 1, clearTimeout() {},
    fetch: async (url) => {
      calls.push(url);
      let data = [];
      if (url.includes('/queue/')) data = { jobs: [] };
      if (url.includes('/charts')) data = {
        country: new URL(url, 'http://localhost').searchParams.get('country'),
        source_title: 'Weekly Top Music Videos', source_url: 'https://music.youtube.com/playlist?list=PLcharts',
        tracks: [{ video_id: 'abcdefghijk', title: 'Test song', artist: 'Test artist', rank: '1' }],
      };
      if (url.includes('/search')) data = { results: [] };
      return { ok: true, json: async () => data };
    },
  });
  for (const match of template.matchAll(/<script src="\/static\/([^?"/]+\.js)/g)) {
    vm.runInContext(readFileSync(new URL(`../musicload/web/static/${match[1]}`, import.meta.url), 'utf8'), context, { filename: match[1] });
  }
  await new Promise((resolve) => setImmediate(resolve));
  return { get, calls, options, context, stored, document, window };
}

test('full page starts, loads songs and the format dropdown responds', async () => {
  const { get, calls, options, stored } = await boot();
  assert.ok(calls.includes('/api/explore/charts?country=DE'));
  assert.match(get('explore-results').innerHTML, /Test song/);
  await get('format-selector-button').click();
  assert.equal(get('format-dropdown').hidden, false);
  await options[1].click();
  assert.equal(get('format-dropdown').hidden, true);
  assert.equal(get('format-selector').value, 'mp3');
  assert.equal(stored.get('audioFormat'), 'mp3');
});

test('blocked browser storage does not prevent startup or changing the format', async () => {
  const { get, options, calls } = await boot({ storageBlocked: true });
  assert.ok(calls.includes('/api/explore/charts?country=DE'));
  await get('format-selector-button').click();
  await options[2].click();
  assert.equal(get('format-selector').value, 'flac');
  assert.equal(get('format-dropdown').hidden, true);
});

test('a cached page without the new dropdown still loads songs', async () => {
  const { get, calls } = await boot({ missingDropdown: true });
  assert.ok(calls.includes('/api/explore/charts?country=DE'));
  assert.match(get('explore-results').innerHTML, /Test song/);
  await get('explore-btn').click();
  assert.equal(get('explore-btn')['aria-current'], 'page');
});

test('blocked event streaming falls back to polling without preventing startup', async () => {
  const { get, calls } = await boot({ streamingBlocked: true });
  assert.ok(calls.includes('/api/queue/jobs'));
  assert.ok(calls.includes('/api/explore/charts?country=DE'));
  await get('format-selector-button').click();
  assert.equal(get('format-dropdown').hidden, false);
});

test('charts use the saved region and show their source and published rank', async () => {
  const { get, calls } = await boot({ savedCountry: 'US' });
  assert.ok(calls.includes('/api/explore/charts?country=US'));
  const html = get('explore-results').innerHTML;
  assert.match(html, /Top Charts \(United States\)/);
  assert.match(html, /https:\/\/music.youtube.com\/playlist\?list=PLcharts/);
  assert.match(html, /chart-rank">1\./);
});

test('switching to Global stores ZZ explicitly in both the preference and URL', async () => {
  const { get, stored, window, calls } = await boot();
  await get('charts-country-selector').dispatch('change', { target: { value: 'ZZ' } });
  assert.ok(calls.includes('/api/explore/charts?country=ZZ'));
  assert.equal(stored.get('chartCountry'), 'ZZ');
  assert.equal(window.location.searchParams.get('country'), 'ZZ');
  assert.match(get('explore-results').innerHTML, /Top Charts \(Global\)/);
});
