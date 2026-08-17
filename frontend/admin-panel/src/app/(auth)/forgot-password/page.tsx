"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import Link from "next/link";
import { Loader2, Eye, EyeOff, CheckCircle2 } from "lucide-react";
import { OtpStep } from "@/components/auth/otp-step";

type Step = "email" | "otp" | "new-password" | "done";

export default function ForgotPasswordPage() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [resetCode, setResetCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const { requestPasswordReset, resetPassword } = useAuth();

  const handleRequestCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInfo("");
    setIsLoading(true);
    try {
      await requestPasswordReset(email);
      setInfo(
        "If an account exists for this email, a reset code is on its way."
      );
      setStep("otp");
    } catch (err: unknown) {
      let message = "Could not send the reset code";
      if (err instanceof TypeError && (err.message === "Failed to fetch" || err.message === "NetworkError when attempting to fetch resource.")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleNewPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (newPassword.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }
    setIsLoading(true);
    try {
      await resetPassword(email, resetCode, newPassword);
      setStep("done");
    } catch (err: unknown) {
      let message = "Could not reset the password";
      if (err instanceof TypeError && (err.message === "Failed to fetch" || err.message === "NetworkError when attempting to fetch resource.")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="w-full max-w-[420px]">
      {/* Logo */}
      <div className="flex justify-center mb-8">
        <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[#3b82f6] text-white font-bold text-lg tracking-tight">
          DS
        </div>
      </div>

      {/* Card */}
      <div className="bg-[var(--bg-card,#121212)] border border-[var(--border,#27272a)] rounded-2xl p-8 shadow-2xl">
        {step === "otp" && (
          <OtpStep
            email={email}
            title="Enter Reset Code"
            subtitle="We emailed a 6-digit code to"
            submitLabel="Continue"
            onVerify={async (code) => {
              setResetCode(code);
              setStep("new-password");
            }}
            onResend={async () => {
              await requestPasswordReset(email);
            }}
            onBack={() => setStep("email")}
          />
        )}

        {step === "done" && (
          <div className="text-center">
            <div className="flex justify-center mb-4">
              <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-500/10 text-emerald-400">
                <CheckCircle2 className="w-6 h-6" />
              </div>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)] mb-2">
              Password Updated
            </h1>
            <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mb-6">
              You can now sign in with your new password.
            </p>
            <Link
              href="/login"
              className="block w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all flex items-center justify-center"
            >
              Back to Sign In
            </Link>
          </div>
        )}

        {(step === "email" || step === "new-password") && (
          <>
            <div className="text-center mb-6">
              <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)]">
                {step === "email" ? "Forgot Password" : "Set a New Password"}
              </h1>
              <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mt-1">
                {step === "email"
                  ? "We'll email you a code to verify it's you"
                  : "Choose a new password for your account"}
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

            {step === "email" ? (
              <form onSubmit={handleRequestCode} className="space-y-4">
                <div>
                  <label htmlFor="email" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                    Email
                  </label>
                  <input
                    id="email"
                    type="email"
                    placeholder="name@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    disabled={isLoading}
                    className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                  />
                </div>

                <button
                  type="submit"
                  disabled={isLoading}
                  className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-6"
                >
                  {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                  {isLoading ? "Sending code..." : "Send Reset Code"}
                </button>
              </form>
            ) : (
              <form onSubmit={handleNewPassword} className="space-y-4">
                <div>
                  <label htmlFor="newPassword" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                    New Password
                  </label>
                  <div className="relative">
                    <input
                      id="newPassword"
                      type={showPassword ? "text" : "password"}
                      placeholder="Min. 6 characters"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      required
                      disabled={isLoading}
                      className="w-full px-3 py-2.5 pr-10 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-muted,#71717a)] hover:text-[var(--text-secondary,#a1a1aa)] transition-colors"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>

                <div>
                  <label htmlFor="confirmPassword" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                    Confirm New Password
                  </label>
                  <div className="relative">
                    <input
                      id="confirmPassword"
                      type={showConfirmPassword ? "text" : "password"}
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      required
                      disabled={isLoading}
                      className="w-full px-3 py-2.5 pr-10 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                    />
                    <button
                      type="button"
                      onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                      aria-label={showConfirmPassword ? "Hide password" : "Show password"}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-muted,#71717a)] hover:text-[var(--text-secondary,#a1a1aa)] transition-colors"
                    >
                      {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isLoading}
                  className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-6"
                >
                  {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                  {isLoading ? "Resetting..." : "Reset Password"}
                </button>
              </form>
            )}

            <p className="text-center text-sm text-[var(--text-muted,#71717a)] mt-6">
              Remembered it?{" "}
              <Link href="/login" className="text-[#3b82f6] hover:underline font-medium">
                Back to Sign In
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}