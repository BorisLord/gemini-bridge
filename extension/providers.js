export const SERVER_BASE_URL = "http://localhost:6969";

export const PROVIDERS = [
  {
    id: "gemini",
    label: "Gemini",
    cookieDomainUrl: "https://gemini.google.com",
    cookieFilter: "google.com",
    // First two are required for auth refresh; the rest enable multi-account /u/{N} resolution.
    cookieNames: [
      "__Secure-1PSID", "__Secure-1PSIDTS",
      "SID", "HSID", "SSID", "SAPISID", "APISID", "__Secure-1PSIDCC",
      "__Secure-1PAPISID", "NID",
    ],
  },
];
