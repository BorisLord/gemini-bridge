export const DEFAULT_SERVER_BASE_URL = "http://localhost:6969";

// Reads the user's override from chrome.storage.local; falls back to the
// default. Exported as an async accessor so any rebind takes effect on the
// next call without reloading the extension.
export async function getServerBaseUrl() {
  const { serverBaseUrl } = await chrome.storage.local.get("serverBaseUrl");
  const trimmed = (serverBaseUrl || "").trim().replace(/\/+$/, "");
  return trimmed || DEFAULT_SERVER_BASE_URL;
}

export const PROVIDERS = [
  {
    id: "gemini",
    label: "Gemini",
    cookieDomainUrl: "https://gemini.google.com",
    cookieFilter: "google.com",
    // First two are required for auth refresh; the rest enable multi-account /u/{N}
    // resolution against gemini.google.com.
    cookieNames: [
      "__Secure-1PSID", "__Secure-1PSIDTS",
      "SID", "HSID", "SSID", "SAPISID", "APISID", "__Secure-1PSIDCC",
      "__Secure-1PAPISID", "NID",
    ],
  },
];
