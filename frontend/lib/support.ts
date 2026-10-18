/** Where people write for help. The same address is the incident contact in the launch checklist (TODO.md). */
export const SUPPORT_EMAIL = "support@bygdell.solutions";

/**
 * A `mailto:` link that opens the person's own email program with the subject and a short template filled in. Nothing is sent from
 * here: the person reads the message and presses send themselves. When they are signed in, the address and name of the account are
 * written into the body so support knows which account it is about; their password or any other secret never is.
 */
export function supportMailto(who: { name: string; email: string } | null): string {
  const lines = ["Hello,", "", "I need help with:", "", "", "What I expected to happen:", "", "", "What happened instead:", "", ""];
  if (who !== null) lines.push("---", `Account: ${who.email}${who.name ? ` (${who.name})` : ""}`);
  const params = new URLSearchParams({ subject: "Support request", body: lines.join("\n") });
  // URLSearchParams writes a space as "+", which mail programs show literally; %20 is what mailto: expects.
  return `mailto:${SUPPORT_EMAIL}?${params.toString().replace(/\+/g, "%20")}`;
}
