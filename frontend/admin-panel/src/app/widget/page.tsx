"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";

// Public demo tenant used when nobody is signed in. The previous value
// (ds_wid_uDy9_...) no longer exists in the database, so /widget/config
// answered 404, the widget could not initialise, and the page looked
// dead to every visitor. The widget token is public by design (it ships
// in every tenant's website HTML), so it is safe to embed here.
const PUBLIC_DEMO_WIDGET_TOKEN = "ds_wid_NK3_YPtMXm2x-SJe6jEGmFywmm8Af93o";

export default function WidgetPage() {
  const [widgetToken, setWidgetToken] = useState(PUBLIC_DEMO_WIDGET_TOKEN);
  const [apiBase, setApiBase] = useState("");
  const [brandColor, setBrandColor] = useState("#ee5a2e");
  const [widgetError, setWidgetError] = useState<string | null>(null);
  const brandTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch logged-in org's widget token — ensures /widget shows YOUR org's theme, not just demo org
  useEffect(() => {
    const base = `${window.location.origin}/api/v1`;
    setApiBase(base);
    fetch(`${base}/dashboard/widget-token`, { credentials: "include", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.widget_token) setWidgetToken(data.widget_token);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    const base = `${window.location.origin}/api/v1`;
    const existing = document.querySelector('script[data-widget-token]');
    if (existing) existing.remove();
    const script = document.createElement("script");
    script.src = `${window.location.origin}/static/widget/widget.js`;
    script.setAttribute("data-widget-token", widgetToken);
    script.setAttribute("data-api-base", base);
    script.setAttribute("data-color", brandColor);
    script.defer = true;
    document.body.appendChild(script);
  }, [widgetToken]);

  // Live brand color polling — updates page + widget without refresh
  useEffect(() => {
    const base = `${window.location.origin}/api/v1`;
    const loadBrand = async () => {
      try {
        const res = await fetch(`${base}/widget/config?widget_token=${encodeURIComponent(widgetToken)}`);
        if (res.status === 404) {
          // The token is not valid for any org. Stop polling (it was
          // firing a 404 every 3 seconds) and say so instead of leaving a
          // silently dead widget on the page.
          setWidgetError("This widget token is not valid. Sign in to load your own organisation's token.");
          if (brandTimer.current) { clearInterval(brandTimer.current); brandTimer.current = null; }
          return;
        }
        if (res.ok) {
          setWidgetError(null);
          const cfg = await res.json();
          const color = cfg?.brand_color || cfg?.ui_brand_color || cfg?.brandColor;
          if (color && /^#[0-9A-Fa-f]{6}$/.test(color) && color.toLowerCase() !== brandColor.toLowerCase()) {
            setBrandColor(color);
            // Update widget host CSS var live
            const w = document.querySelector("dotstark-booking-widget") as HTMLElement | null;
            if (w) w.style.setProperty("--brand-color", color);
            // Also update script data-color for next load
            const s = document.querySelector('script[data-widget-token]') as HTMLScriptElement | null;
            if (s) s.setAttribute("data-color", color);
          }
        }
      } catch {}
    };
    loadBrand();
    const interval = window.setInterval(loadBrand, 3000);
    brandTimer.current = interval;
    return () => {
      window.clearInterval(interval);
      if (brandTimer.current === interval) brandTimer.current = null;
    };
  }, [widgetToken, brandColor]);

  return (
    <div className="min-h-screen bg-white text-zinc-900 flex flex-col" style={{ fontFamily: 'Inter, sans-serif' }}>
      {/* Announcement */}
      <div className="bg-[#fff3cd] border-b border-[#ffe69c] px-4 py-2 text-center text-sm">
        <b>Kentico 13 EOS:</b> Support ends Dec 31, 2026 — 218d left. <a href="#" className="underline font-semibold">Free Audit</a>
      </div>
      {/* Header from dotstark.com */}
      <header className="sticky top-0 z-20 bg-white border-b border-zinc-200">
        <div className="max-w-[1400px] mx-auto px-6 h-[72px] flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <div className="size-8 rounded-lg bg-[#ee5a2e] flex items-center justify-center text-white font-extrabold text-lg">D</div>
            <span className="font-bold text-xl tracking-tight">DotStark</span>
            <span className="hidden md:inline text-xs px-2 py-1 rounded-full bg-zinc-900 text-white ml-2">Kentico Silver Partner</span>
          </Link>
          <nav className="hidden lg:flex items-center gap-6 text-sm font-medium">
            <span className="cursor-pointer flex items-center gap-1">Kentico ▾</span>
            <span className="cursor-pointer flex items-center gap-1">DXP ▾</span>
            <span className="cursor-pointer flex items-center gap-1">Microsoft ▾</span>
            <span className="cursor-pointer flex items-center gap-1">Data & AI ▾</span>
            <span className="cursor-pointer">Insights ▾</span>
            <span className="cursor-pointer">Our Company ▾</span>
          </nav>
          <Link href="/contact-us" className="hidden sm:inline-flex px-5 py-2 rounded-full bg-[#ee5a2e] text-white font-semibold text-sm">Contact Us</Link>
        </div>
      </header>

      {/* Hero from dotstark.com */}
      <section className="max-w-[1400px] mx-auto w-full px-6 py-10 lg:py-14 grid lg:grid-cols-2 gap-8 items-center">
        <div>
          <h1 className="text-4xl lg:text-5xl font-black leading-tight">Upgrade From Kentico 13<br /><span className="text-[#ee5a2e]">Before End of Support</span><br />Risks Grow</h1>
          <p className="text-zinc-600 mt-4 leading-relaxed max-w-xl">DotStark helps enterprises migrate from Kentico 13 or any older version to Xperience by Kentico with zero downtime, preserved SEO and future-ready Microsoft architecture.</p>
          <Link href="#widget" className="inline-flex mt-6 px-6 py-3 rounded-full bg-[#ee5a2e] text-white font-bold">Book a Free Consultation →</Link>
          <div className="grid grid-cols-4 gap-4 mt-10">
            {[
              { v: "17+", k: "Years of Expertise" },
              { v: "400+", k: "Projects Delivered" },
              { v: "40+", k: "Countries Served" },
              { v: "15+", k: "Certified Developers" },
            ].map((m) => (
              <div key={m.k} className="text-center p-4 rounded-2xl bg-zinc-50 border border-zinc-200">
                <div className="text-2xl font-black">{m.v}</div><div className="text-xs text-zinc-500">{m.k}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-[24px] overflow-hidden bg-zinc-900 aspect-video flex items-center justify-center relative">
          <video autoPlay loop muted playsInline className="absolute inset-0 w-full h-full object-cover opacity-60" poster="https://dotstark.com/content/images/img/videos/main_4.mp4">
            <source src="https://dotstark.com/content/images/img/videos/main_4.mp4" type="video/mp4" />
          </video>
          <div className="relative z-10 text-center p-6">
            <p className="text-white font-bold text-xl">Your AI Booking Agent is live →</p>
            <p className="text-white/70 text-sm mt-2">Click the chat bubble at bottom-right to try the widget on this dotstark.com-style homepage.</p>
          </div>
        </div>
      </section>

      {/* Widget embed info */}
      <section id="widget" className="max-w-[1400px] mx-auto w-full px-6 pb-8">
        {widgetError ? (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-6 mb-4">
            <h3 className="font-bold text-red-800">Chat widget could not load</h3>
            <p className="text-sm text-red-700 mt-1">{widgetError}</p>
            <Link href="/dashboard/agent" className="inline-block mt-3 px-4 py-2 rounded-full bg-zinc-900 text-white text-sm font-semibold">
              Open Agent Personalisation →
            </Link>
          </div>
        ) : null}
        <div className="rounded-2xl border border-zinc-200 bg-zinc-50 p-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div>
            <h3 className="font-bold">Chat Widget Live</h3>
            <p className="text-sm text-zinc-600 mt-1">Powered by <code className="bg-white border px-1 py-0.5 rounded text-xs">{`data-widget-token="${widgetToken}"`}</code> via <code className="bg-white border px-1 py-0.5 rounded text-xs">{apiBase || "/api/v1"}</code></p>
          </div>
          <Link href="/dashboard/agent" className="px-4 py-2 rounded-full bg-zinc-900 text-white text-sm font-semibold">Agent Personalisation →</Link>
        </div>
      </section>

      {/* Trusted */}
      <section className="border-t border-zinc-200 py-6">
        <div className="max-w-[1400px] mx-auto px-6 flex items-center gap-4 overflow-hidden">
          <span className="text-xs font-bold tracking-widest text-zinc-500 whitespace-nowrap">TRUSTED BY ENTERPRISE CLIENTS</span>
          <div className="h-px flex-1 bg-zinc-200" />
          <span className="text-xs text-zinc-400 whitespace-nowrap">••• Xperience by Kentico • Microsoft Partner • Kentico Silver •••</span>
        </div>
      </section>

      <footer className="mt-auto border-t border-zinc-200 py-6 text-center text-xs text-zinc-500">
        © 2026 DotStark Technologies — <Link href="/privacy-policy" className="underline">Privacy Policy</Link> · <Link href="/" className="underline">Home</Link> · <code className="bg-zinc-100 border px-1 py-0.5 rounded">{typeof window !== "undefined" ? window.location.origin : ""}/widget</code>
      </footer>
    </div>
  );
}
