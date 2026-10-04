/**
 * A fresh retry key for one attempt to create an organization: 32 random bytes, base64url without padding
 * (43 characters, the shape the backend and the BFF accept). It is generated in the browser for exactly one
 * purpose: a retry after an ambiguous failure (the answer was lost) must not create a second organization.
 * It is not a credential, identifies no one, and is scoped to the authenticated creator by FastAPI.
 */
export function newRequestKey(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
