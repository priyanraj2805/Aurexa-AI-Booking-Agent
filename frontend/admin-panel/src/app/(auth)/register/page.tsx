"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Eye, EyeOff } from "lucide-react";
import { OtpStep } from "@/components/auth/otp-step";

export default function RegisterPage() {
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [step, setStep] = useState<"details" | "otp">("details");
  const { register, sendOtp } = useAuth();
  const router = useRouter();

  const handleRequestOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }

    setIsLoading(true);
    try {
      // Validate the email is free + email the verification code.
      await sendOtp(email);
      setStep("otp");
    } catch (err: unknown) {
      let message: string;
      if (err instanceof TypeError && (err.message === "Failed to fetch" || err.message === "NetworkError when attempting to fetch resource.")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else {
        message = err instanceof Error ? err.message : "Registration failed";
      }
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRegister = async (code: string) => {
    await register({ full_name: fullName, org_name: orgName, email, password, otp_code: code });
    router.push("/login?registered=1");
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
        {step === "otp" ? (
          <OtpStep
            email={email}
            title="Verify Your Email"
            subtitle="We emailed a 6-digit code to"
            submitLabel="Verify & Create Account"
            onVerify={handleRegister}
            onResend={async () => {
              await sendOtp(email);
            }}
            onBack={() => setStep("details")}
          />
        ) : (
          <>
            <div className="text-center mb-6">
              <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)]">Create Account</h1>
              <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mt-1">
                Set up your organization and start booking
              </p>
            </div>

            {error && (
              <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                {error}
              </div>
            )}

            <form onSubmit={handleRequestOtp} className="space-y-4">
              {/* Full Name */}
              <div>
                <label htmlFor="fullName" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                  Full Name
                </label>
                <input
                  id="fullName"
                  type="text"
                  placeholder="John Doe"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  required
                  disabled={isLoading}
                  className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                />
              </div>

              {/* Organization Name */}
              <div>
                <label htmlFor="orgName" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                  Organization Name
                </label>
                <input
                  id="orgName"
                  type="text"
                  placeholder="Acme Corp"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  required
                  disabled={isLoading}
                  className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                />
              </div>

              {/* Email */}
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

              {/* Password */}
              <div>
                <label htmlFor="password" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                  Password
                </label>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    placeholder="Min. 6 characters"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
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

              {/* Confirm Password */}
              <div>
                <label htmlFor="confirmPassword" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                  Confirm Password
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

              {/* Submit */}
              <button
                type="submit"
                disabled={isLoading}
                className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-6"
              >
                {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                {isLoading ? "Sending code..." : "Create Account"}
              </button>
            </form>

            <p className="text-center text-sm text-[var(--text-muted,#71717a)] mt-6">
              Already have an account?{" "}
              <Link href="/login" className="text-[#3b82f6] hover:underline font-medium">
                Sign in
              </Link>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
