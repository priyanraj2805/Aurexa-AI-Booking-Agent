"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, MailCheck, ArrowLeft } from "lucide-react";

/**
 * Shared "enter the code we emailed you" step used by the login, register
 * and landing-page auth flows. Owns OTP input + resend + back-to-form.
 */
export function OtpStep({
  email,
  title,
  subtitle,
  submitLabel,
  resendLabel = "Resend code",
  onVerify,
  onResend,
  onBack,
}: {
  email: string;
  title: string;
  subtitle: string;
  submitLabel: string;
  resendLabel?: string;
  onVerify: (code: string) => Promise<void>;
  onResend: () => Promise<void>;
  onBack: () => void;
}) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [isVerifying, setIsVerifying] = useState(false);
  const [isResending, setIsResending] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");
    if (code.trim().length !== 6) {
      setError("Enter the 6-digit code from your email");
      return;
    }
    setIsVerifying(true);
    try {
      await onVerify(code.trim());
    } catch (err: unknown) {
      setError(
        err instanceof Error && !(err as Error).message.includes("fetch")
          ? err.message
          : "Verification failed. Check the code and try again."
      );
    } finally {
      setIsVerifying(false);
    }
  };

  const handleResend = async () => {
    if (cooldown > 0 || isResending) return;
    setError("");
    setInfo("");
    setIsResending(true);
    try {
      await onResend();
      setInfo("A new code has been sent to your email.");
      setCooldown(30);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not resend the code.");
    } finally {
      setIsResending(false);
    }
  };

  return (
    <div>
      <div className="flex justify-center mb-4">
        <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[#3b82f6]/10 text-[#3b82f6]">
          <MailCheck className="w-6 h-6" />
        </div>
      </div>

      <div className="text-center mb-6">
        <h1 className="text-xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)]">
          {title}
        </h1>
        <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mt-1">
          {subtitle}{" "}
          <span className="text-[var(--text-primary,#ffffff)] font-medium">{email}</span>
        </p>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
          {error}
        </div>
      )}
      {info && (
        <div className="mb-4 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
          {info}
        </div>
      )}

      <form onSubmit={handleVerify} className="space-y-4">
        <div>
          <label
            htmlFor="otpCode"
            className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider"
          >
            Verification Code
          </label>
          <input
            ref={inputRef}
            id="otpCode"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            placeholder="6-digit code"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            required
            disabled={isVerifying}
            className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm tracking-[0.3em] text-center text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
          />
        </div>

        <button
          type="submit"
          disabled={isVerifying}
          className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2"
        >
          {isVerifying && <Loader2 className="h-4 w-4 animate-spin" />}
          {isVerifying ? "Verifying..." : submitLabel}
        </button>

        <div className="flex items-center justify-between text-sm">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1 text-[var(--text-muted,#71717a)] hover:text-[var(--text-secondary,#a1a1aa)] transition-colors"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back
          </button>
          <button
            type="button"
            onClick={handleResend}
            disabled={cooldown > 0 || isResending}
            className="text-[#3b82f6] hover:underline font-medium transition-colors disabled:opacity-50"
          >
            {isResending
              ? "Sending..."
              : cooldown > 0
                ? `${resendLabel} in ${cooldown}s`
                : resendLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
