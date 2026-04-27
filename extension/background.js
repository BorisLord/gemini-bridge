import { PROVIDERS, SERVER_BASE_URL } from "./providers.js";

const SERVER_BASE = `${SERVER_BASE_URL}/auth`;
const ALARM_NAME = "gemini-bridge-refresh";
const ALARM_PERIOD_MIN = 5;

// Chrome MV3 strips Origin on fetches to host_permissions URLs, and Origin is a
// forbidden header we can't set manually — server's extension-only check uses this instead.
const EXT_HEADERS = { "X-Extension-Id": chrome.runtime.id };

function bridgeFetch(url, init = {}) {
  const headers = { ...EXT_HEADERS, ...(init.headers || {}) };
  return fetch(url, { ...init, headers });
}

async function getCookies(provider) {
  const out = {};
  for (const name of provider.cookieNames) {
    const c = await chrome.cookies.get({ url: provider.cookieDomainUrl, name });
    if (c) out[name] = c.value;
  }
  // First two cookieNames are the required auth pair; missing => not signed in.
  const [r1, r2] = provider.cookieNames;
  if (!out[r1] || !out[r2]) return null;
  return out;
}

async function getSelectedIndex(providerId) {
  const { selections = {} } = await chrome.storage.local.get("selections");
  return selections[providerId] ?? 0;
}

async function setSelectedIndex(providerId, idx) {
  const { selections = {} } = await chrome.storage.local.get("selections");
  selections[providerId] = idx;
  await chrome.storage.local.set({ selections });
}

async function fetchServerStatus() {
  try {
    const res = await bridgeFetch(`${SERVER_BASE_URL}/admin/status`);
    if (res.ok) {
      return { ...(await res.json()), reachable: true };
    }
    return { reachable: false };
  } catch {
    return { reachable: false };
  }
}

async function setStatus(providerId, s) {
  const { statuses = {} } = await chrome.storage.local.get("statuses");
  statuses[providerId] = s;
  await chrome.storage.local.set({ statuses });
}

async function setAccounts(providerId, accounts) {
  const { accounts: acctMap = {} } = await chrome.storage.local.get("accounts");
  acctMap[providerId] = accounts;
  await chrome.storage.local.set({ accounts: acctMap });
}

async function pushProvider(provider, reason) {
  const cookies = await getCookies(provider);
  if (!cookies) {
    await setStatus(provider.id, { ok: false, reason, error: `Missing cookies — sign in to ${provider.label}.` });
    return;
  }
  const account_index = await getSelectedIndex(provider.id);
  try {
    const res = await bridgeFetch(`${SERVER_BASE}/cookies/${provider.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies, account_index }),
    });
    if (!res.ok) {
      const text = await res.text();
      await setStatus(provider.id, { ok: false, reason, error: `${res.status}: ${text.slice(0, 200)}` });
      return;
    }
    await setStatus(provider.id, { ok: true, reason, at: Date.now(), account_index });
  } catch (e) {
    await setStatus(provider.id, { ok: false, reason, error: `Server unreachable: ${e.message}` });
  }
}

async function discoverAccounts(provider) {
  const cookies = await getCookies(provider);
  if (!cookies) return [];
  try {
    const res = await bridgeFetch(`${SERVER_BASE}/accounts/${provider.id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cookies, account_index: 0 }),
    });
    if (!res.ok) return [];
    const list = await res.json();
    await setAccounts(provider.id, list);
    return list;
  } catch {
    return [];
  }
}

async function pushAll(reason) {
  const status = await fetchServerStatus();
  if (!status.reachable) {
    for (const p of PROVIDERS) {
      await setStatus(p.id, { ok: false, reason, error: `Server not running at ${SERVER_BASE_URL}` });
    }
    return;
  }
  await Promise.all(PROVIDERS.map((p) => pushProvider(p, reason)));
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_PERIOD_MIN });
  pushAll("installed");
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_PERIOD_MIN });
  pushAll("startup");
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) pushAll("alarm");
});

// Coalesce Google cookie-rotation bursts (one onChanged per cookie, 8+ per rotation)
// into a single push. 2s is wide enough for typical bursts, short enough that a fresh
// 1PSIDTS reaches the server before the next chat call. Manual syncs bypass this.
const COOKIE_DEBOUNCE_MS = 2000;
const _pushTimers = new Map();
function schedulePushProvider(provider, reason) {
  const existing = _pushTimers.get(provider.id);
  if (existing) clearTimeout(existing);
  const t = setTimeout(() => {
    _pushTimers.delete(provider.id);
    pushProvider(provider, reason);
  }, COOKIE_DEBOUNCE_MS);
  _pushTimers.set(provider.id, t);
}

chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (removed) return;
  for (const p of PROVIDERS) {
    if (cookie.domain.endsWith(p.cookieFilter) && p.cookieNames.includes(cookie.name)) {
      schedulePushProvider(p, `cookie:${cookie.name}`);
      return;
    }
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Cheap MV3 hygiene: drop messages from other extensions / external pages.
  if (sender.id !== chrome.runtime.id) return false;
  (async () => {
    if (msg?.type === "sync-now") {
      await pushAll("manual");
      sendResponse({ done: true });
    } else if (msg?.type === "discover-accounts") {
      const provider = PROVIDERS.find((p) => p.id === msg.providerId);
      if (!provider) { sendResponse({ accounts: [] }); return; }
      const list = await discoverAccounts(provider);
      sendResponse({ accounts: list });
    } else if (msg?.type === "server-status") {
      const status = await fetchServerStatus();
      sendResponse({ status });
    } else if (msg?.type === "select-account") {
      await setSelectedIndex(msg.providerId, msg.account_index);
      const provider = PROVIDERS.find((p) => p.id === msg.providerId);
      if (provider) await pushProvider(provider, "account-selected");
      sendResponse({ done: true });
    } else if (msg?.type === "reset-fallback") {
      try {
        const res = await bridgeFetch(`${SERVER_BASE_URL}/admin/reset-fallback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        sendResponse({ ok: res.ok, status: res.status });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    } else if (msg?.type === "select-gem") {
      try {
        const res = await bridgeFetch(`${SERVER_BASE_URL}/admin/gem`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gem_id: msg.gem_id || null }),
        });
        const body = await res.json().catch(() => ({}));
        sendResponse({ ok: res.ok, status: res.status, body });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    } else if (msg?.type === "openrouter-update") {
      try {
        const res = await bridgeFetch(`${SERVER_BASE_URL}/admin/openrouter`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(msg.patch || {}),
        });
        const body = await res.json().catch(() => ({}));
        sendResponse({ ok: res.ok, status: res.status, body });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    } else {
      // Always answer — silence hangs the popup until Chrome's 5s timeout.
      sendResponse({ ok: false, error: `unknown message type: ${msg?.type}` });
    }
  })();
  return true;
});
