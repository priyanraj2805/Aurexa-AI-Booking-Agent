"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type EnrollResponse = {
  secret: string;
  otpauth_url: string;
  qr_svg: string;
};

export default function MFAPage() {
  const [qr, setQr] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [enrolled, setEnrolled] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  // If MFA is already enrolled, /auth/mfa/enroll returns 400 → just show the code input.
  useEffect(() => {
    api
      .post<EnrollResponse>("/auth/mfa/enroll")
      .then((data) => {
        setQr(data.qr_svg);
        setSecret(data.secret);
      })
      .catch(() => setEnrolled(true));
  }, []);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post("/auth/mfa/verify", { code });
      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-8">
      <div className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-8">
        <h1 className="text-2xl font-bold">Two-factor authentication</h1>
        <p className="mt-1 text-sm text-slate-400">
          {enrolled
            ? "Enter the 6-digit code from your authenticator app."
            : "Scan this QR code with your authenticator app (e.g. Google Authenticator), then enter the code."}
        </p>

        {!enrolled && qr && (
          <div className="mt-4 rounded-lg bg-white p-3">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={qr} alt="TOTP QR code" className="mx-auto h-48 w-48" />
            <p className="mt-2 text-center text-xs text-slate-600">
              Manual key: <code className="font-mono">{secret}</code>
            </p>
          </div>
        )}

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div>
            <label htmlFor="code" className="block text-sm font-medium text-slate-300">
              6-digit code
            </label>
            <input
              id="code"
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-center text-lg tracking-widest outline-none focus:border-indigo-500"
            />
          </div>

          {error && (
            <p className="text-sm text-red-400" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || code.length !== 6}
            className="w-full rounded-md bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {loading ? "Verifying…" : "Verify"}
          </button>
        </form>
      </div>
    </main>
  );
}
