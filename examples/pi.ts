// Pi (https://pi.dev) — drop this at ~/.pi/agent/extensions/gemini-bridge.ts.
//
// Auto-discovery variant: fetches the bridge's /v1/models endpoint at startup
// and registers every advertised model as a pi provider. No hand-maintained
// list — add an account on the bridge and pi sees it on next launch.
//
// Suffix mapping: `-advanced` = AI Ultra, `-plus` = AI Pro, none = Free.
// Calling a tier above your subscription downgrades silently.
//
// The bridge currently exposes only the accounts whose `/v1/models` lists
// them. If you want extra accounts without touching the bridge, prefer the
// static `examples/pi.jsonc` config (drop into ~/.pi/agent/models.json).
//
// pi loads extensions from ~/.pi/agent/extensions/, .pi/extensions/, and
// settings.json sources. Async factories are awaited before startup, so the
// remote fetch resolves before `/model` is available.

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const BASE_URL = "http://localhost:6969/v1";

export default async function (pi: ExtensionAPI) {
  let payload: { data: Array<{ id: string; owned_by?: string }> };
  try {
    const r = await fetch(`${BASE_URL}/models`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    payload = await r.json();
  } catch (err) {
    pi.log?.(
      `gemini-bridge unreachable at ${BASE_URL} — provider not registered (${
        err instanceof Error ? err.message : err
      })`,
    );
    return;
  }

  pi.registerProvider("gemini-bridge", {
    name: "Gemini Bridge",
    baseUrl: BASE_URL,
    apiKey: "dummy",
    api: "openai-completions",
    models: payload.data.map((m) => {
      const thinking = m.id.includes("thinking");
      return {
        id: m.id,
        name: tierLabel(m.id),
        reasoning: thinking,
        input: ["text", "image"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 1_000_000,
        maxTokens: 65_536,
        // The bridge surfaces Gemini Thinking traces as `delta.reasoning_content`
        // (DeepSeek-R1 convention). Without this hint pi assumes the OpenAI
        // shape, which only exposes a `reasoning_tokens` count — thinking
        // pane would stay empty. Plain models ignore the field.
        ...(thinking && { compat: { thinkingFormat: "deepseek" as const } }),
      };
    }),
  });
}

function tierLabel(id: string): string {
  const [model, account] = id.split("@");
  const tier = model.endsWith("-advanced")
    ? "Ultra"
    : model.endsWith("-plus")
      ? "Pro"
      : "Free";
  const family = model.replace(/-(advanced|plus)$/, "");
  return `${tier} · ${family} · ${account ?? ""}`.trim();
}
