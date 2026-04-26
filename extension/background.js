import { PROVIDERS, SERVER_BASE_URL } from "./providers.js";

const SERVER_BASE = `${SERVER_BASE_URL}/auth`;
const ALARM_NAME = "gemini-bridge-refresh";
const ALARM_PERIOD_MIN = 5;

async function getCookies(provider) {
  const out = {};
  for (const name of provider.cookieNames) {
    const c = await chrome.cookies.get({ url: provider.cookieDomainUrl, name });
    if (c) out[name] = c.value;
  }
  // Provider must declare required cookies as the first two entries (auth pair).
  // If those are missing, treat as not-signed-in.
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
  // /admin/status only exists in webai mode (the FastAPI worker).
  // In g4f mode, that worker is replaced — /admin/status 404s but /v1/models still serves.
  try {
    const adminRes = await fetch(`${SERVER_BASE_URL}/admin/status`);
    if (adminRes.ok) {
      return { ...(await adminRes.json()), reachable: true };
    }
    if (adminRes.status === 404) {
      // No /admin endpoint — likely g4f mode. Probe /v1/models to confirm something's listening.
      const modelsRes = await fetch(`${SERVER_BASE_URL}/v1/models`).catch(() => null);
      if (modelsRes && modelsRes.ok) {
        return { mode: "g4f", g4f_installed: true, reachable: true };
      }
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
    const res = await fetch(`${SERVER_BASE}/cookies/${provider.id}`, {
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
    const res = await fetch(`${SERVER_BASE}/accounts/${provider.id}`, {
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
  // In g4f mode, /auth/cookies/* is gone (FastAPI worker is replaced).
  // Mark cookie sync as paused — not failed — so the popup doesn't scream.
  if (status.mode === "g4f") {
    for (const p of PROVIDERS) {
      await setStatus(p.id, { paused: true, reason, at: Date.now() });
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

chrome.cookies.onChanged.addListener(({ cookie, removed }) => {
  if (removed) return;
  for (const p of PROVIDERS) {
    if (cookie.domain.endsWith(p.cookieFilter) && p.cookieNames.includes(cookie.name)) {
      pushProvider(p, `cookie:${cookie.name}`);
      return;
    }
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
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
        const res = await fetch(`${SERVER_BASE_URL}/admin/reset-fallback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        sendResponse({ ok: res.ok, status: res.status });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    } else if (msg?.type === "switch-mode") {
      try {
        const res = await fetch(`${SERVER_BASE_URL}/admin/mode`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: msg.mode }),
        });
        const body = await res.text();
        sendResponse({ ok: res.ok, status: res.status, body });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    }
  })();
  return true;
});
