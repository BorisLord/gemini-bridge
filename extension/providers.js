// Single source of truth for the local server URL. Change this if you
// run the server on a non-default port (and pass --port N to start.sh).
export const SERVER_BASE_URL = "http://localhost:6969";

// Registry of supported providers. Add an entry here to extend the bridge
// to a new browser-cookie-based LLM. Server side must implement the matching
// /auth/cookies/{id} route.
//
// Each entry:
//   id              identifier sent in the URL path
//   label           human-readable name (popup UI)
//   cookieDomainUrl URL used by chrome.cookies.get (must match cookie's domain)
//   cookieFilter    cookie domain suffix to watch in onChanged events
//   cookieNames     names of cookies to capture and forward
//
// All cookieNames are sent together as { cookies: { name: value, ... } }.
// If any required cookie is missing, the provider sync is skipped.

export const PROVIDERS = [
  {
    id: "gemini",
    label: "Gemini",
    cookieDomainUrl: "https://gemini.google.com",
    cookieFilter: "google.com",
    // First two are required for auth refresh; the rest are needed for
    // multi-account /u/{N} resolution server-side.
    cookieNames: [
      "__Secure-1PSID", "__Secure-1PSIDTS",
      "SID", "HSID", "SSID", "SAPISID", "APISID", "__Secure-1PSIDCC",
    ],
  },
  // Examples for future provider wiring (server-side stub required):
  // { id: "chatgpt", label: "ChatGPT",
  //   cookieDomainUrl: "https://chatgpt.com",
  //   cookieFilter: "chatgpt.com",
  //   cookieNames: ["__Secure-next-auth.session-token"] },
  // { id: "claude", label: "Claude",
  //   cookieDomainUrl: "https://claude.ai",
  //   cookieFilter: "claude.ai",
  //   cookieNames: ["sessionKey"] },
];
