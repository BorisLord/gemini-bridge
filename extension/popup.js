import { PROVIDERS, SERVER_BASE_URL } from "./providers.js";

async function loadState() {
  const { statuses = {}, accounts = {}, selections = {} } = await chrome.storage.local.get([
    "statuses", "accounts", "selections",
  ]);
  return { statuses, accounts, selections };
}

async function renderServer() {
  const $ = document.getElementById("server");
  const { status } = await chrome.runtime.sendMessage({ type: "server-status" });
  if (!status || !status.reachable) {
    $.innerHTML = `<span class="err">Server not reachable at ${SERVER_BASE_URL}</span>
                   <div class="sub">Start the bridge with one of:
                     <ul style="margin:4px 0 0 16px;padding:0">
                       <li><code>./start.sh</code> (native)</li>
                       <li><code>docker compose up -d</code></li>
                       <li><code>systemctl --user start gemini-bridge</code></li>
                     </ul>
                   </div>`;
    return status;
  }
  if (status.mode === "g4f") {
    // /admin/mode lives in the webai FastAPI worker and is gone in g4f mode,
    // so we can't switch back via HTTP. Restart brings the service back to
    // webai automatically (initial_mode is hardcoded).
    $.innerHTML = `<div class="row"><b>g4f fallback active</b></div>
                   <div class="sub">Gemini is paused. Return to Gemini by restarting the bridge:
                     <ul style="margin:4px 0 0 16px;padding:0">
                       <li>native: Ctrl+C in the <code>./start.sh</code> terminal, then re-run <code>./start.sh</code> (or type <b>1</b> ⏎)</li>
                       <li>docker: <code>docker compose restart</code></li>
                       <li>systemd: <code>systemctl --user restart gemini-bridge</code></li>
                     </ul>
                   </div>`;
    return status;
  }
  // webai mode
  const g4fBlock = status.g4f_installed
    ? `<button id="switch-g4f" style="margin-top:6px">Switch to g4f fallback</button>`
    : `<div class="sub">g4f not installed — re-install with <code>WITH_G4F=1</code> :
         <ul style="margin:4px 0 0 16px;padding:0">
           <li><code>WITH_G4F=1 ./start.sh</code></li>
           <li><code>WITH_G4F=1 docker compose build && docker compose up -d</code></li>
         </ul>
       </div>`;
  const fb = status.last_fallback || {};
  let fbBlock = "";
  if (fb.sticky_until) {
    const remaining = fb.sticky_until - (Date.now() / 1000);
    const eta = remaining < 60 ? "<1m" : remaining < 3600 ? `${Math.round(remaining / 60)}m` : `${(remaining / 3600).toFixed(1)}h`;
    fbBlock = `<div class="sub" style="color:#a87">↪ Sticky fallback active · ${fb.model} · ${eta} left</div>
               <button id="reset-fallback" style="margin-top:4px">Retry Gemini now</button>`;
  } else if (fb.at) {
    const age = (Date.now() / 1000) - fb.at;
    const when = age < 60 ? "just now" : age < 3600 ? `${Math.round(age / 60)}m ago` : `${Math.round(age / 3600)}h ago`;
    if (fb.ok) {
      fbBlock = `<div class="sub" style="color:#a87">↪ Last fallback ${when} · ${fb.model} · ${fb.reason}</div>`;
    } else {
      fbBlock = `<div class="sub" style="color:#c44">⚠ Fallback failed ${when} · ${fb.error || "unknown error"}</div>`;
    }
  }
  $.innerHTML = `<div class="row">Mode <b>${status.mode}</b> (Gemini active)</div>
                 ${fbBlock}
                 ${g4fBlock}`;
  return status;
}

async function switchMode(mode) {
  const res = await chrome.runtime.sendMessage({ type: "switch-mode", mode });
  const $ = document.getElementById("server");
  if (!res?.ok) {
    $.innerHTML += `<div class="err" style="margin-top:6px">Switch failed: ${res?.body || res?.error || "unknown"}</div>`;
    return;
  }
  $.innerHTML = `<div class="sub">Switching to <b>${mode}</b>… (${"~3-8s"})</div>`;
  // Server flips within ~1-2s; rebuild the popup once it settles.
  setTimeout(render, 4000);
}

async function render() {
  const serverStatus = await renderServer();
  // Wire mode-switch buttons (created in renderServer's innerHTML).
  document.getElementById("switch-g4f")?.addEventListener("click", () => switchMode("g4f"));
  document.getElementById("reset-fallback")?.addEventListener("click", async () => {
    await chrome.runtime.sendMessage({ type: "reset-fallback" });
    setTimeout(render, 300);
  });
  const $ = document.getElementById("providers");
  $.innerHTML = "";
  if (!serverStatus || !serverStatus.reachable) return;
  const { statuses, accounts, selections } = await loadState();
  for (const p of PROVIDERS) {
    const s = statuses[p.id];
    const accts = accounts[p.id] || [];
    const sel = selections[p.id] ?? 0;
    const block = document.createElement("div");
    block.className = "provider";
    let header, sub;
    if (!s) {
      header = "—"; sub = "Not synced yet.";
    } else if (s.paused) {
      header = `<span style="color:#888">⏸ Cookie sync paused</span>`;
      sub = `<div class="sub">Server is in g4f mode. Restart to resume Gemini cookie sync.</div>`;
    } else if (s.ok) {
      header = `<span class="ok">✓ Connected (u/${s.account_index ?? 0})</span>`;
      sub = `<div class="sub">${new Date(s.at).toLocaleTimeString()} · ${s.reason}</div>`;
    } else {
      header = `<span class="err">× Failed</span>`;
      sub = `<div class="sub">${s.error || ""}
              <div style="margin-top:4px">If this persists, reload the extension in
              <code>chrome://extensions/</code> (toggle off/on or click the reload icon).</div>
             </div>`;
    }
    let acctSelect = "";
    if (accts.length > 0) {
      const opts = accts.map(a =>
        `<option value="${a.index}" ${a.index === sel ? "selected" : ""}>u/${a.index} — ${a.email}</option>`
      ).join("");
      acctSelect = `<select data-provider="${p.id}" class="acctsel">${opts}</select>`;
    }
    block.innerHTML = `
      <div class="name">${p.label}</div>
      <div>${header}</div>
      ${sub}
      ${acctSelect}
      <button class="discover" data-provider="${p.id}">Detect accounts</button>
    `;
    $.appendChild(block);
  }
  // wire selectors
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
      render();
    });
  });
}

document.getElementById("sync").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "sync-now" });
  setTimeout(render, 400);
});

// Auto-sync on popup open (and again on close — fire-and-forget, the
// background worker keeps running after the popup is gone).
(async () => {
  chrome.runtime.sendMessage({ type: "sync-now" });
  await render();
  setTimeout(render, 600);
})();

window.addEventListener("pagehide", () => {
  chrome.runtime.sendMessage({ type: "sync-now" });
});
