/**
 * Runs once when the server starts. An unusable authentication configuration stops the server instead of
 * leaving it running half-configured: with APP_ENV unset (production) a missing or invalid AUTH_MODE or
 * PUBLIC_ORIGIN, or AUTH_MODE=dev, is fatal. In development it is a loud warning (every request is refused
 * anyway: the mode is then "none").
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { appEnv, authConfig } = await import("@/lib/auth/config");
  const { problem } = authConfig();
  if (problem === null) return;
  const message = `Authentication is misconfigured: ${problem}.`;
  if (appEnv() === "production") throw new Error(message);
  console.warn(message);
}
