import { createHmac } from "node:crypto";

/** A TOTP code as an authenticator app computes it (RFC 6238: SHA-1, 6 digits, 30-second steps), for the browser tests. */

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(text: string): Buffer {
  let bits = "";
  for (const char of text.replace(/=+$/, "").toUpperCase()) bits += ALPHABET.indexOf(char).toString(2).padStart(5, "0");
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(Number.parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export const STEP_SECONDS = 30;

export function totp(secretBase32: string, atMs: number = Date.now()): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(atMs / 1000 / STEP_SECONDS)));
  const digest = createHmac("sha1", base32Decode(secretBase32)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const number = digest.readUInt32BE(offset) & 0x7fffffff;
  return String(number % 1_000_000).padStart(6, "0");
}

/** Milliseconds until the next time step starts, plus a little margin: a code is accepted only from a newer step than the last one used. */
export const untilNextStep = (now: number = Date.now()): number => STEP_SECONDS * 1000 - (now % (STEP_SECONDS * 1000)) + 1500;
