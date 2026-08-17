"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import PasswordInput from "@/components/password-input";

type Step = "email" | "otp" | "password" | "done";

export default function ForgotPasswordPage() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const codeRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  useEffect(() => {
    if (step === "otp") codeRef.current?.focus();
  }, [step]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const requestCode = async (target: string) => {
    setLoading(true);
    setError(null);
    setInfo(null);
    try {
      await api.post("/auth/password/forgot", { email: target });
      setInfo("If an account exists for this email, a reset code has been sent.");
      setStep("otp");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not request a reset code.");
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    if (cooldown > 0 || loading) return;
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      await api.post("/auth/password/forgot", { email });
      setInfo("A new code has been sent to your email.");
      setCooldown(30);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resend the code.");
    } finally {
      setLoading(false);
    }
  };

  const advanceFromOtp = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (code.trim().length !== 6) {
      setError("Enter the 6-digit code from your email");
      return;
    }
    setStep("password");
  };

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setLoading(true);
    try {
      await api.post("/auth/password/reset", {
        email,
        code: code.trim(),
        new_password: newPassword,
      });
      setStep("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password reset failed.");
    } finally {
      setLoading(false);
    }
  };

  if (step === "done") {
    return (
      <main className="flex min-h-screen items-center justify-center p-8">
        <div className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8 text-center">
          <h1 className="text-xl font-bold">Password updated</h1>
          <p className="mt-2 text-sm text-slate-400">
            Your password has been reset. All existing sessions were signed out.
          </p>
          <Link
            href="/login"
            className="mt-6 inline-block w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
          >
            Sign in
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8">
        <h1 className="text-2xl font-bold">
          {step === "email" ? "Reset password" : step === "otp" ? "Check your email" : "New password"}
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          {step === "email"
            ? "Enter your account email and we will send you a reset code."
            : step === "otp"
              ? `Enter the 6-digit code sent to`
              : "Choose a strong new password."}
        </p>

        {error && (
          <p className="mt-4 rounded-md border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-400" role="alert">
            {error}
          </p>
        )}
        {info && (
          <p className="mt-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-400">
            {info}
          </p>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (step === "email") requestCode(email);
            if (step === "otp") advanceFromOtp(e);
            if (step === "password") submitReset(e);
          }}
          className="mt-6 space-y-4"
        >
          {step === "email" && (
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-slate-300">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              />
            </div>
          )}

          {step === "otp" && (
            <div>
              <label htmlFor="code" className="block text-sm font-medium text-slate-300">
                Verification code
              </label>
              <input
                ref={codeRef}
                id="code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="6-digit code"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                required
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-center text-sm tracking-[0.3em] outline-none focus:border-indigo-500"
              />
              <div className="mt-3 flex items-center justify-between text-sm">
                <button
                  type="button"
                  onClick={() => {
                    setStep("email");
                    setCode("");
                    setError(null);
                    setInfo(null);
                  }}
                  className="text-slate-400 hover:text-slate-200"
                >
                  Back
                </button>
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={cooldown > 0 || loading}
                  className="text-indigo-400 hover:underline disabled:opacity-50"
                >
                  {loading ? "Sending…" : cooldown > 0 ? `Resend in ${cooldown}s` : "Resend code"}
                </button>
              </div>
            </div>
          )}

          {step === "password" && (
            <div>
              <label htmlFor="newPassword" className="block text-sm font-medium text-slate-300">
                New password
              </label>
              <PasswordInput
                id="newPassword"
                minLength={6}
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              />
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {loading
              ? "Working…"
              : step === "email"
                ? "Send reset code"
                : step === "otp"
                  ? "Continue"
                  : "Set new password"}
          </button>
        </form>

        {step === "password" && (
          <button
            type="button"
            onClick={() => {
              setStep("otp");
              setNewPassword("");
              setError(null);
            }}
            className="mt-3 w-full text-center text-sm text-slate-400 hover:text-slate-200"
          >
            Back
          </button>
        )}

        {step === "email" && (
          <p className="mt-4 text-center text-sm text-slate-400">
            Remembered it?{" "}
            <Link href="/login" className="text-indigo-400 hover:underline">
              Sign in
            </Link>
          </p>
        )}
      </div>
    </main>
  );
}