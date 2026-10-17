/** The signed-in person's own second factor, as the account page shows it. Pure: no request, no cookie, no secret of ours. */

export type MfaStatus = {
  enabled: boolean;
  required: boolean;
  recoveryCodesLeft: number;
};
export type MfaEnrollment = { secret: string; uri: string };

const BASE32 = /^[A-Z2-7]{32}$/; // the 20-byte key, base32 without padding
const RECOVERY_CODE = /^[0-9a-f]{8}-[0-9a-f]{8}$/;
const CODE_ACTIONS = ["confirm", "recovery-codes", "disable"] as const;
export type CodeAction = (typeof CODE_ACTIONS)[number];

export const isCodeAction = (value: string): value is CodeAction =>
  (CODE_ACTIONS as readonly string[]).includes(value);

const CHALLENGE = /^[A-Za-z0-9_-]{43}$/;

/** FastAPI's "the password was right, now the code" answer: the challenge to spend with a code, or null if it is not that. */
export function parseMfaChallenge(value: unknown): string | null {
  if (typeof value !== "object" || value === null) return null;
  const { mfa_required: required, challenge } = value as Record<
    string,
    unknown
  >;
  return required === true &&
    typeof challenge === "string" &&
    CHALLENGE.test(challenge)
    ? challenge
    : null;
}

export function parseMfaStatus(value: unknown): MfaStatus | null {
  if (typeof value !== "object" || value === null) return null;
  const {
    enabled,
    required,
    recovery_codes_left: left,
  } = value as Record<string, unknown>;
  if (
    typeof enabled !== "boolean" ||
    typeof required !== "boolean" ||
    typeof left !== "number" ||
    !Number.isInteger(left) ||
    left < 0 ||
    left > 100
  )
    return null;
  return { enabled, required, recoveryCodesLeft: left };
}

/** FastAPI's enrollment answer: the key and the otpauth link, and nothing else is passed on. */
export function parseEnrollment(value: unknown): MfaEnrollment | null {
  if (typeof value !== "object" || value === null) return null;
  const { secret, uri } = value as Record<string, unknown>;
  if (
    typeof secret !== "string" ||
    !BASE32.test(secret) ||
    typeof uri !== "string" ||
    !uri.startsWith("otpauth://totp/") ||
    uri.length > 1024
  )
    return null;
  return { secret, uri };
}

export function parseRecoveryCodes(value: unknown): string[] | null {
  if (typeof value !== "object" || value === null) return null;
  const codes = (value as Record<string, unknown>).recovery_codes;
  if (
    !Array.isArray(codes) ||
    codes.length === 0 ||
    codes.length > 20 ||
    !codes.every((code) => typeof code === "string" && RECOVERY_CODE.test(code))
  )
    return null;
  return codes as string[];
}

/** A code typed by a person (a 6-digit code, or a recovery code): bounded text, nothing about its shape is decided here. */
export const isTypedCode = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0 && value.length <= 64;
