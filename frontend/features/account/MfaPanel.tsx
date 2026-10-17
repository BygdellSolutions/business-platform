"use client";

import { useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Notice } from "@/components/ui/Notice";
import { CSRF_HEADER, readCsrfToken } from "@/lib/auth/cookies";
import type { MfaEnrollment, MfaStatus } from "@/lib/auth/own-mfa";

type Step = "idle" | "password" | "scan" | "codes" | "regenerate" | "turn-off";
type Outcome = { tone: "success" | "error"; text: string } | null;

const FIELD =
  "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";

const ERRORS: Record<string, string> = {
  invalid_code: "That code is not correct.",
  wrong_password: "The password is not correct.",
  already_enabled: "A second factor is already on.",
  not_enabled: "No second factor is set up.",
  required: "Your role requires a second factor, so it cannot be turned off.",
  throttled: "Too many attempts. Try again later.",
};

/**
 * Where a person turns their second factor (an authenticator app) on and off, and gets their recovery codes. The server decides
 * everything: this panel shows its answers. Recovery codes appear ONCE, right after the code is confirmed, and are never kept
 * here after the person says they saved them. The QR code is drawn in the browser from the otpauth link; nothing is sent anywhere.
 */
export function MfaPanel({
  initial,
  mustSetUp,
}: {
  initial: MfaStatus;
  mustSetUp: boolean;
}) {
  const [status, setStatus] = useState<MfaStatus>(initial);
  const [step, setStep] = useState<Step>("idle");
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  async function call(
    path: string,
    method: "GET" | "POST",
    body?: unknown,
  ): Promise<{
    ok: boolean;
    status: number;
    data: Record<string, unknown> | null;
  }> {
    const token = readCsrfToken(document.cookie);
    const response = await fetch(path, {
      method,
      headers: {
        ...(body === undefined ? {} : { "content-type": "application/json" }),
        ...(token === null ? {} : { [CSRF_HEADER]: token }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
    return {
      ok: response.ok,
      status: response.status,
      data: (await response.json().catch(() => null)) as Record<
        string,
        unknown
      > | null,
    };
  }

  function explain(result: {
    status: number;
    data: Record<string, unknown> | null;
  }) {
    const detail = result.data?.detail as { code?: unknown } | undefined;
    const code =
      typeof detail?.code === "string"
        ? detail.code
        : result.status === 429
          ? "throttled"
          : "";
    setOutcome({
      tone: "error",
      text: ERRORS[code] ?? "That did not work. Try again.",
    });
  }

  async function run(work: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setOutcome(null);
    try {
      await work();
    } catch {
      setOutcome({
        tone: "error",
        text: "No answer from the server. Try again.",
      });
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  async function refresh() {
    const result = await call("/api/auth/mfa", "GET");
    const next = result.data?.status as MfaStatus | undefined;
    if (result.ok && next) setStatus(next);
  }

  const read = (event: FormEvent<HTMLFormElement>, name: string) =>
    String(new FormData(event.currentTarget).get(name) ?? "");

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = read(event, "password");
    void run(async () => {
      const result = await call("/api/auth/mfa/enroll", "POST", { password });
      const found = result.data?.enrollment as MfaEnrollment | undefined;
      if (!result.ok || !found) return explain(result);
      const { default: QRCode } = await import("qrcode");
      setQr(await QRCode.toDataURL(found.uri, { margin: 1, width: 192 }));
      setEnrollment(found);
      setStep("scan");
    });
  }

  function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = read(event, "code");
    void run(async () => {
      const result = await call("/api/auth/mfa/confirm", "POST", { code });
      const found = result.data?.recoveryCodes as string[] | undefined;
      if (!result.ok || !found) return explain(result);
      setCodes(found);
      setEnrollment(null);
      setQr(null);
      setStep("codes");
    });
  }

  function submitCode(
    event: FormEvent<HTMLFormElement>,
    path: string,
    done: (data: Record<string, unknown> | null) => void,
  ) {
    event.preventDefault();
    const code = read(event, "code");
    void run(async () => {
      const result = await call(path, "POST", { code });
      if (!result.ok) return explain(result);
      done(result.data);
    });
  }

  function regenerate(event: FormEvent<HTMLFormElement>) {
    submitCode(event, "/api/auth/mfa/recovery-codes", (data) => {
      setCodes((data?.recoveryCodes as string[] | undefined) ?? []);
      setStep("codes");
    });
  }

  function turnOff(event: FormEvent<HTMLFormElement>) {
    submitCode(event, "/api/auth/mfa/disable", () => {
      setStep("idle");
      setOutcome({ tone: "success", text: "The second factor is off." });
      void refresh();
    });
  }

  async function saved() {
    setCodes([]);
    setStep("idle");
    await run(refresh);
    setOutcome({ tone: "success", text: "The second factor is on." });
  }

  return (
    <div className="flex flex-col gap-3" data-testid="mfa-panel">
      {mustSetUp && !status.enabled && (
        <Notice tone="error" testId="mfa-required-notice">
          Your role requires a second factor. Set it up to keep working.
        </Notice>
      )}
      {outcome && (
        <Notice
          tone={outcome.tone === "error" ? "error" : undefined}
          testId={outcome.tone === "error" ? "mfa-error" : "mfa-done"}
        >
          {outcome.text}
        </Notice>
      )}

      {step === "idle" && !status.enabled && (
        <>
          <p className="text-sm text-zinc-500">
            Ask for a code from an authenticator app each time you sign in. It
            is not on yet.
          </p>
          <div>
            <Button
              type="button"
              variant="primary"
              onClick={() => setStep("password")}
              data-testid="mfa-set-up"
            >
              Set up a second factor
            </Button>
          </div>
        </>
      )}

      {step === "idle" && status.enabled && (
        <>
          <p className="text-sm" data-testid="mfa-state">
            On. {status.recoveryCodesLeft} recovery{" "}
            {status.recoveryCodesLeft === 1 ? "code" : "codes"} left.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={() => setStep("regenerate")}
              data-testid="mfa-new-codes"
            >
              New recovery codes
            </Button>
            <Button
              type="button"
              variant="danger"
              disabled={status.required}
              onClick={() => setStep("turn-off")}
              data-testid="mfa-turn-off"
              title={status.required ? ERRORS.required : undefined}
            >
              Turn off
            </Button>
          </div>
        </>
      )}

      {step === "password" && (
        <form
          onSubmit={start}
          className="flex flex-col gap-3"
          aria-label="Confirm your password"
        >
          <label className="flex flex-col gap-1 text-sm font-medium">
            Your password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
              className={FIELD}
              data-testid="mfa-password"
            />
          </label>
          <div className="flex gap-2">
            <Button
              type="submit"
              disabled={pending}
              data-testid="mfa-password-submit"
            >
              Continue
            </Button>
            <Button
              type="button"
              disabled={pending}
              onClick={() => setStep("idle")}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {step === "scan" && enrollment && (
        <form
          onSubmit={confirm}
          className="flex flex-col gap-3"
          aria-label="Turn on the second factor"
        >
          <p className="text-sm">
            Scan this code with an authenticator app (Google Authenticator,
            Microsoft Authenticator, 1Password and similar), or type the key in
            by hand.
          </p>
          {qr && (
            // eslint-disable-next-line @next/next/no-img-element -- a data URL drawn here from the otpauth link; next/image has nothing to optimise
            <img
              src={qr}
              alt="QR code for your authenticator app"
              width={192}
              height={192}
              className="rounded border border-border bg-white"
              data-testid="mfa-qr"
            />
          )}
          <p className="text-sm">
            Key:{" "}
            <code className="font-mono break-all" data-testid="mfa-secret">
              {enrollment.secret}
            </code>
          </p>
          <label className="flex flex-col gap-1 text-sm font-medium">
            The 6-digit code the app shows
            <input
              name="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              maxLength={64}
              className={FIELD}
              data-testid="mfa-code"
            />
          </label>
          <div className="flex gap-2">
            <Button type="submit" disabled={pending} data-testid="mfa-confirm">
              Turn on
            </Button>
            <Button
              type="button"
              disabled={pending}
              onClick={() => {
                setEnrollment(null);
                setQr(null);
                setStep("idle");
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {step === "codes" && (
        <div className="flex flex-col gap-3" data-testid="mfa-codes">
          <Notice testId="mfa-codes-notice">
            Save these recovery codes somewhere safe. Each works once if you
            lose your authenticator app. They are shown only now.
          </Notice>
          <ol className="grid grid-cols-1 gap-1 font-mono text-sm sm:grid-cols-2">
            {codes.map((code) => (
              <li key={code} data-testid="mfa-recovery-code">
                {code}
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={() =>
                void navigator.clipboard?.writeText(codes.join("\n"))
              }
              data-testid="mfa-copy"
            >
              Copy
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={pending}
              onClick={() => void saved()}
              data-testid="mfa-saved"
            >
              I have saved them
            </Button>
          </div>
        </div>
      )}

      {(step === "regenerate" || step === "turn-off") && (
        <form
          onSubmit={step === "regenerate" ? regenerate : turnOff}
          className="flex flex-col gap-3"
          aria-label={
            step === "regenerate"
              ? "New recovery codes"
              : "Turn off the second factor"
          }
        >
          <label className="flex flex-col gap-1 text-sm font-medium">
            A current code (from the app, or a recovery code)
            <input
              name="code"
              autoComplete="one-time-code"
              required
              maxLength={64}
              className={FIELD}
              data-testid="mfa-current-code"
            />
          </label>
          <div className="flex gap-2">
            <Button
              type="submit"
              variant={step === "turn-off" ? "danger" : "primary"}
              disabled={pending}
              data-testid="mfa-current-submit"
            >
              {step === "regenerate" ? "Get new codes" : "Turn off"}
            </Button>
            <Button
              type="button"
              disabled={pending}
              onClick={() => setStep("idle")}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
