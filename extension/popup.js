import { PROVIDERS, getServerBaseUrl } from "./providers.js";

async function loadState() {
  const { statuses = {}, accounts = {}, selections = {} } = await chrome.storage.local.get([
    "statuses", "accounts", "selections",
  ]);
  return { statuses, accounts, selections };
}

async function fetchStatus() {
  const { status } = await chrome.runtime.sendMessage({ type: "server-status" });
  return status;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function renderServer(status) {
  const $ = document.getElementById("server");
  const base = await getServerBaseUrl();
  if (!status || !status.reachable) {
    $.innerHTML = `<span class="err">Server not reachable at ${esc(base)}</span>
                   <div class="sub">Start the bridge with one of:
                     <ul style="margin:4px 0 0 16px;padding:0">
                       <li><code>./start.sh</code> (native)</li>
                       <li><code>docker compose up -d</code></li>
                       <li><code>systemctl --user start gemini-bridge</code></li>
                     </ul>
                     If the bridge runs on a non-default port, set it under <strong>Server URL</strong> below.
                   </div>`;
    return;
  }
  $.innerHTML = `<div>Server reachable · Gemini active <span class="sub">at ${esc(base)}</span></div>`;
}

function renderGem(status) {
  const headerCb = document.getElementById("gem-enabled");
  const $ = document.getElementById("gem");
  $.replaceChildren();
  if (!status || !status.reachable) {
    headerCb.checked = false;
    headerCb.disabled = true;
    $.classList.add("hidden");
    return;
  }
  headerCb.disabled = false;
  const selected = (status.gem && status.gem.selected_id) || "";
  // Section is "on" if a Gem ID is currently applied. User checks the box to
  // reveal the input; unchecking clears the active Gem server-side.
  const enabled = !!selected;
  headerCb.checked = enabled;
  $.classList.toggle("hidden", !enabled);
  if (!enabled) return;

  const current = document.createElement("div");
  current.className = "sub ok";
  current.textContent = "Active Gem ID: ";
  const code = document.createElement("code");
  code.textContent = selected;
  current.appendChild(code);
  $.appendChild(current);

  const row = document.createElement("div");
  row.className = "keyrow";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "gem-input";
  input.placeholder = "Paste Gem URL or ID to switch";
  const apply = document.createElement("button");
  apply.id = "gem-apply";
  apply.textContent = "Apply";
  row.appendChild(input);
  row.appendChild(apply);
  $.appendChild(row);

  const hint = document.createElement("div");
  hint.className = "sub";
  hint.append("Open your Gem on ");
  const a = document.createElement("a");
  a.href = "https://gemini.google.com/gems/view";
  a.target = "_blank";
  a.textContent = "gemini.google.com";
  hint.appendChild(a);
  hint.append(
    " and paste the URL — e.g. https://gemini.google.com/u/0/gem/abc123. " +
    "The Gem must exist on the Google account currently selected above.",
  );
  $.appendChild(hint);
}

async function applyGemFromInput() {
  const raw = (document.getElementById("gem-input")?.value || "").trim();
  await chrome.runtime.sendMessage({ type: "select-gem", gem_id: raw });
  setTimeout(render, 200);
}

function renderGemPrompt() {
  // Shown when the user toggles Gem ON but no Gem is active yet.
  // Bind directly here — render() doesn't re-run after a toggle click, so the
  // global gem-apply binding wouldn't catch this button.
  const $ = document.getElementById("gem");
  $.classList.remove("hidden");
  $.replaceChildren();
  const row = document.createElement("div");
  row.className = "keyrow";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "gem-input";
  input.placeholder = "Paste Gem URL or ID";
  const apply = document.createElement("button");
  apply.id = "gem-apply";
  apply.textContent = "Apply";
  apply.addEventListener("click", applyGemFromInput);
  row.appendChild(input);
  row.appendChild(apply);
  $.appendChild(row);
}

async function render() {
  const status = await fetchStatus();
  await renderServer(status);
  renderGem(status);

  document.getElementById("gem-apply")?.addEventListener("click", applyGemFromInput);

  const $ = document.getElementById("providers");
  $.replaceChildren();
  if (!status || !status.reachable) return;
  const { statuses, accounts, selections } = await loadState();
  for (const p of PROVIDERS) {
    const s = statuses[p.id];
    const accts = accounts[p.id] || [];
    const selIdx = selections[p.id] ?? 0;
    const block = document.createElement("div");
    block.className = "provider";

    const nameDiv = document.createElement("div");
    nameDiv.className = "name";
    nameDiv.textContent = p.label;
    block.appendChild(nameDiv);

    const headerDiv = document.createElement("div");
    if (!s) {
      headerDiv.textContent = "—";
    } else if (s.ok) {
      const span = document.createElement("span");
      span.className = "ok";
      span.textContent = `✓ Connected (u/${s.account_index ?? 0})`;
      headerDiv.appendChild(span);
    } else {
      const span = document.createElement("span");
      span.className = "err";
      span.textContent = "× Failed";
      headerDiv.appendChild(span);
    }
    block.appendChild(headerDiv);

    const subDiv = document.createElement("div");
    subDiv.className = "sub";
    if (!s) {
      subDiv.textContent = "Not synced yet.";
    } else if (s.ok) {
      subDiv.textContent = `${new Date(s.at).toLocaleTimeString()} · ${s.reason}`;
    } else {
      subDiv.textContent = s.error || "";
      const hint = document.createElement("div");
      hint.style.marginTop = "4px";
      hint.append(
        "If this persists, reload the extension in ",
      );
      const code = document.createElement("code");
      code.textContent = "chrome://extensions/";
      hint.appendChild(code);
      hint.append(" (toggle off/on or click the reload icon).");
      subDiv.appendChild(hint);
    }
    block.appendChild(subDiv);

    if (accts.length > 0) {
      const countDiv = document.createElement("div");
      countDiv.className = "sub";
      countDiv.textContent = `${accts.length} account${accts.length > 1 ? "s" : ""} detected`;
      block.appendChild(countDiv);

      const acctSelect = document.createElement("select");
      acctSelect.className = "acctsel";
      acctSelect.dataset.provider = p.id;
      for (const a of accts) {
        const o = document.createElement("option");
        o.value = String(a.index);
        o.textContent = `u/${a.index} — ${a.email}`;
        if (a.index === selIdx) o.selected = true;
        acctSelect.appendChild(o);
      }
      block.appendChild(acctSelect);

      const hint = document.createElement("div");
      hint.className = "sub";
      hint.style.marginTop = "4px";
      hint.textContent = "Account not listed? Sign in to it at gemini.google.com, then click Detect again.";
      block.appendChild(hint);
    }

    const detectBtn = document.createElement("button");
    detectBtn.className = "discover";
    detectBtn.dataset.provider = p.id;
    detectBtn.textContent = "Detect accounts";
    block.appendChild(detectBtn);

    $.appendChild(block);
  }
  document.querySelectorAll(".acctsel").forEach((el) => {
    el.addEventListener("change", async (e) => {
      const idx = parseInt(e.target.value, 10);
      const pid = e.target.dataset.provider;
      await chrome.runtime.sendMessage({ type: "select-account", providerId: pid, account_index: idx });
      setTimeout(render, 600);
    });
  });
  document.querySelectorAll(".discover").forEach((el) => {
    el.addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "Detecting…";
      await chrome.runtime.sendMessage({ type: "discover-accounts", providerId: e.target.dataset.provider });
      await render();
    });
  });
}

document.getElementById("sync").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "sync-now" });
  setTimeout(render, 400);
});

document.getElementById("gem-enabled").addEventListener("change", async (e) => {
  if (e.target.checked) {
    renderGemPrompt();
  } else {
    await chrome.runtime.sendMessage({ type: "select-gem", gem_id: "" });
    setTimeout(render, 200);
  }
});

async function refreshSettingsUi() {
  const stored = (await chrome.storage.local.get("serverBaseUrl")).serverBaseUrl || "";
  const enabled = !!stored;
  document.getElementById("settings-enabled").checked = enabled;
  document.getElementById("settings").classList.toggle("hidden", !enabled);
  document.getElementById("server-url").value = stored;
  document.getElementById("server-error").textContent = "";
}

function isValidServerUrl(raw) {
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

document.getElementById("settings-enabled").addEventListener("change", async (e) => {
  document.getElementById("settings").classList.toggle("hidden", !e.target.checked);
  if (!e.target.checked) {
    await chrome.storage.local.remove("serverBaseUrl");
    await chrome.runtime.sendMessage({ type: "sync-now" });
    await refreshSettingsUi();
    setTimeout(render, 200);
  }
});

document.getElementById("server-save").addEventListener("click", async () => {
  const raw = (document.getElementById("server-url").value || "").trim();
  const errEl = document.getElementById("server-error");
  if (raw && !isValidServerUrl(raw)) {
    errEl.textContent = "Invalid URL — must start with http:// or https://";
    return;
  }
  errEl.textContent = "";
  if (raw) {
    await chrome.storage.local.set({ serverBaseUrl: raw });
  } else {
    await chrome.storage.local.remove("serverBaseUrl");
  }
  const btn = document.getElementById("server-save");
  const originalLabel = btn.textContent;
  btn.textContent = "✓ Saved";
  setTimeout(() => { btn.textContent = originalLabel; }, 1000);
  await chrome.runtime.sendMessage({ type: "sync-now" });
  await refreshSettingsUi();
  setTimeout(render, 200);
});

(async () => {
  await refreshSettingsUi();
  chrome.runtime.sendMessage({ type: "sync-now" });
  await render();
  setTimeout(render, 600);
})();
