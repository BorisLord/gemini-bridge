import { PROVIDERS, SERVER_BASE_URL } from "./providers.js";

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

function fmtAge(epoch) {
  const age = (Date.now() / 1000) - epoch;
  if (age < 60) return "just now";
  if (age < 3600) return `${Math.round(age / 60)}m ago`;
  return `${Math.round(age / 3600)}h ago`;
}

function fmtRemaining(epoch) {
  const r = epoch - (Date.now() / 1000);
  if (r < 60) return "<1m";
  if (r < 3600) return `${Math.round(r / 60)}m`;
  return `${(r / 3600).toFixed(1)}h`;
}

function renderServer(status) {
  const $ = document.getElementById("server");
  if (!status || !status.reachable) {
    $.innerHTML = `<span class="err">Server not reachable at ${esc(SERVER_BASE_URL)}</span>
                   <div class="sub">Start the bridge with one of:
                     <ul style="margin:4px 0 0 16px;padding:0">
                       <li><code>./start.sh</code> (native)</li>
                       <li><code>docker compose up -d</code></li>
                       <li><code>systemctl --user start gemini-bridge</code></li>
                     </ul>
                   </div>`;
    return;
  }
  const fb = status.last_fallback || {};
  let fbBlock = "";
  if (fb.sticky_until) {
    fbBlock = `<div class="sub" style="color:#a87">↪ Sticky fallback active · ${esc(fb.model)} · ${esc(fmtRemaining(fb.sticky_until))} left</div>
               <button id="reset-fallback" style="margin-top:4px">Retry Gemini now</button>`;
  } else if (fb.at) {
    if (fb.ok) {
      fbBlock = `<div class="sub" style="color:#a87">↪ Last fallback ${esc(fmtAge(fb.at))} · ${esc(fb.model)} · ${esc(fb.reason)}</div>`;
    } else {
      fbBlock = `<div class="sub" style="color:#c44">⚠ Fallback failed ${esc(fmtAge(fb.at))} · ${esc(fb.error || "unknown error")}</div>`;
    }
  }
  $.innerHTML = `<div>Server reachable · Gemini active</div>${fbBlock}`;
}

function renderGem(status) {
  const $ = document.getElementById("gem");
  $.replaceChildren();
  if (!status || !status.reachable) {
    const div = document.createElement("div");
    div.className = "sub";
    div.textContent = "Server unreachable.";
    $.appendChild(div);
    return;
  }
  const selected = (status.gem && status.gem.selected_id) || "";

  const current = document.createElement("div");
  current.className = "sub";
  if (selected) {
    current.classList.add("ok");
    current.textContent = "Active Gem ID: ";
    const code = document.createElement("code");
    code.textContent = selected;
    current.appendChild(code);
  } else {
    current.textContent = "No Gem applied (default Gemini).";
  }
  $.appendChild(current);

  const row = document.createElement("div");
  row.className = "keyrow";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "gem-input";
  input.placeholder = "Paste Gem URL or ID (empty to clear)";
  const apply = document.createElement("button");
  apply.id = "gem-apply";
  apply.textContent = "Apply";
  row.appendChild(input);
  row.appendChild(apply);
  $.appendChild(row);

  const hint = document.createElement("div");
  hint.className = "sub";
  hint.append(
    "Open your Gem on ",
  );
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

function renderOpenRouter(status) {
  const $ = document.getElementById("openrouter");
  if (!status || !status.reachable) {
    $.innerHTML = `<div class="sub">Server unreachable.</div>`;
    return;
  }
  const or = status.openrouter || {};
  const enabled = !!or.enabled;
  const hasKey = !!or.has_api_key;
  const models = or.available_models || [];
  const opts = models.map(m =>
    `<option value="${esc(m)}" ${m === or.model ? "selected" : ""}>${esc(m)}</option>`
  ).join("");
  const customNotInList = or.model && !models.includes(or.model)
    ? `<option value="${esc(or.model)}" selected>${esc(or.model)} (custom)</option>` : "";
  const keyHint = hasKey
    ? `<span class="ok">key set: ${esc(or.api_key_masked || "•••")}</span>`
    : `<span class="err">no API key</span>`;
  $.className = "or-block" + (enabled ? "" : " disabled");
  $.innerHTML = `
    <label class="toggle">
      <input id="or-enabled" type="checkbox" ${enabled ? "checked" : ""} />
      <span>Enable OpenRouter fallback (free models)</span>
    </label>
    <div class="sub" style="margin-top:6px">${keyHint}</div>
    <div class="keyrow">
      <input id="or-key" type="password" placeholder="sk-or-v1-… (paste to set, leave blank to clear)" />
      <button id="or-key-save">Save</button>
    </div>
    <select id="or-model" class="modelsel">${customNotInList}${opts}</select>
    <div class="sub">Free models — daily caps apply (≈50 req/day per model). Get a key at <a href="https://openrouter.ai/keys" target="_blank">openrouter.ai/keys</a>.</div>
  `;
}

async function applyOpenRouter(patch) {
  await chrome.runtime.sendMessage({ type: "openrouter-update", patch });
  setTimeout(render, 200);
}

async function render() {
  const status = await fetchStatus();
  renderServer(status);
  renderGem(status);
  renderOpenRouter(status);

  document.getElementById("reset-fallback")?.addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "reset-fallback" });
    setTimeout(render, 200);
  });

  document.getElementById("gem-apply")?.addEventListener("click", async () => {
    const input = document.getElementById("gem-input");
    const raw = (input?.value || "").trim();
    await chrome.runtime.sendMessage({ type: "select-gem", gem_id: raw });
    setTimeout(render, 200);
  });

  document.getElementById("or-enabled")?.addEventListener("change", (e) => {
    applyOpenRouter({ enabled: e.target.checked });
  });
  document.getElementById("or-key-save")?.addEventListener("click", () => {
    const v = document.getElementById("or-key").value;
    applyOpenRouter({ api_key: v });
    document.getElementById("or-key").value = "";
  });
  document.getElementById("or-model")?.addEventListener("change", (e) => {
    applyOpenRouter({ model: e.target.value });
  });

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

(async () => {
  chrome.runtime.sendMessage({ type: "sync-now" });
  await render();
  setTimeout(render, 600);
})();

