/**
 * The DEVELOPMENT identity adapter: a developer picks a seeded user on /dev-login and the choice is kept in an
 * httpOnly cookie that only server code can read. It is used only when AUTH_MODE=dev with APP_ENV=development
 * (see `lib/auth/config`); in any other mode it is not consulted at all. Real authentication is the session
 * path in `lib/auth/*`.
 */
export const DEV_USER_COOKIE = "bp_dev_user";
export const DEV_USER_MAX_AGE_SECONDS = 60 * 60 * 8;

const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$/;

/** A normalized email, or null if it is not a plausible email. */
export function parseEmail(value: string | undefined | null): string | null {
  const email = value?.trim().toLowerCase() ?? "";
  return email.length <= 320 && EMAIL.test(email) ? email : null;
}
