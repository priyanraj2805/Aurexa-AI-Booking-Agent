"use client";

import Link from "next/link";
import {
  MessageSquare,
  Calendar,
  Zap,
  Globe,
  ArrowRight,
  Bot,
  BarChart3,
  CreditCard,
  CheckCircle2,
  Clock,
  Video,
} from "lucide-react";

const features = [
  {
    icon: Bot,
    title: "AI-Powered Conversations",
    description: "Intelligent chatbot that understands context and handles bookings naturally.",
  },
  {
    icon: Calendar,
    title: "Smart Scheduling",
    description: "Automatic calendar sync with real-time availability management.",
  },
  {
    icon: Globe,
    title: "Multi-Channel",
    description: "Works on your website, WhatsApp, and more — one agent, everywhere.",
  },
  {
    icon: BarChart3,
    title: "Analytics Dashboard",
    description: "Track bookings, peak hours, and customer insights at a glance.",
  },
  {
    icon: CreditCard,
    title: "Payment Integration",
    description: "Accept deposits and payments seamlessly during booking.",
  },
  {
    icon: Zap,
    title: "Instant Setup",
    description: "Get up and running in minutes with our no-code configuration.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden" style={{ scrollPaddingTop: "50px" }}>
      {/* Animated Background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-[#FF6B00]/10 rounded-full blur-[150px]" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-[#3b82f6]/10 rounded-full blur-[150px]" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-purple-500/5 rounded-full blur-[200px]" />
      </div>

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-6 py-4 max-w-7xl mx-auto">
        <Link href="/" className="flex items-center gap-2">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-sm">
            A
          </div>
          <span className="text-xl font-bold tracking-tight">Aurexa</span>
        </Link>
        <nav className="hidden md:flex items-center gap-8">
          <button
            onClick={() => document.getElementById("features")?.scrollIntoView({ behavior: "smooth" })}
            className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer"
          >
            Features
          </button>
          <button
            onClick={() => document.getElementById("how-it-works")?.scrollIntoView({ behavior: "smooth" })}
            className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer"
          >
            How it Works
          </button>
          <button
            onClick={() => document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" })}
            className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer"
          >
            Pricing
          </button>
        </nav>
        <div className="flex items-center gap-3">
          <Link
            href="/login"
            className="px-4 py-2 text-sm font-medium text-zinc-300 hover:text-white transition-colors"
          >
            Sign In
          </Link>
          <Link
            href="/register"
            className="px-4 py-2 text-sm font-semibold bg-[#FF6B00] hover:bg-[#e55f00] text-white rounded-lg transition-colors"
          >
            Get Started
          </Link>
        </div>
      </header>

      {/* Hero Section */}
      <section className="relative z-10 px-6 py-8 md:py-12 max-w-7xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          {/* Left Content */}
          <div className="text-center lg:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-800/50 border border-zinc-700 text-xs text-zinc-400 mb-6">
              <Zap className="w-3 h-3 text-[#FF6B00]" />
              AI-Powered Booking Assistant
            </div>
            <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold leading-tight mb-4">
              <span className="text-white">Aurexa</span> —{" "}
              <span className="text-[#FF6B00]">Your 24/7 Booking Agent</span>{" "}
              <span className="text-white">That Never Sleeps</span>
            </h1>
            <p className="text-lg font-medium text-white mb-3 max-w-xl mx-auto lg:mx-0">
              Aurexa is an AI-powered booking and appointment management platform for businesses.
            </p>
            <p className="text-base text-zinc-400 mb-6 max-w-xl mx-auto lg:mx-0">
              Automatically handle appointments, answer questions, and confirm bookings. Integrate with your
              website and WhatsApp in minutes — with calendar sync and online meeting scheduling.
            </p>
            <ul className="text-sm text-zinc-300 mb-8 max-w-xl mx-auto lg:mx-0 space-y-2 text-left inline-block lg:block">
              <li className="flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-[#FF6B00] mt-0.5 shrink-0" /> Automated appointment booking
              </li>
              <li className="flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-[#FF6B00] mt-0.5 shrink-0" /> Calendar integration (Google Calendar &amp; Outlook)
              </li>
              <li className="flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-[#FF6B00] mt-0.5 shrink-0" /> Real-time availability management
              </li>
              <li className="flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-[#FF6B00] mt-0.5 shrink-0" /> Customer appointment management
              </li>
              <li className="flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 text-[#FF6B00] mt-0.5 shrink-0" /> Google Meet / online meeting scheduling
              </li>
            </ul>
            <div className="flex flex-col sm:flex-row items-center gap-4 justify-center lg:justify-start">
              <Link
                href="/register"
                className="inline-flex items-center gap-2 px-6 py-3 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all hover:scale-105"
              >
                Start Free Trial
                <ArrowRight className="w-4 h-4" />
              </Link>
              <Link
                href="/login"
                className="inline-flex items-center gap-2 px-6 py-3 border border-zinc-700 hover:border-zinc-600 text-zinc-300 font-medium rounded-lg transition-colors"
              >
                <MessageSquare className="w-4 h-4" />
                Sign In
              </Link>
            </div>
          </div>

          {/* Right - Marketing preview (no form, no inputs) */}
          <div className="flex justify-center lg:justify-end">
            <div className="w-full max-w-md bg-zinc-900/80 backdrop-blur-xl border border-zinc-800 rounded-2xl p-6">
              <div className="flex items-center gap-2 mb-4">
                <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#FF6B00]/20 text-[#FF6B00]">
                  <Bot className="w-4 h-4" />
                </div>
                <div>
                  <p className="text-sm font-semibold">Aurexa Assistant</p>
                  <p className="text-xs text-zinc-500">Online — Ready to help</p>
                </div>
                <span className="ml-auto h-2 w-2 rounded-full bg-emerald-500 animate-pulse" aria-hidden />
              </div>

              <div className="space-y-3">
                <div className="rounded-2xl bg-zinc-800/80 border border-zinc-700 px-4 py-3 text-sm text-zinc-200">
                  Hi! I&apos;m Aurexa — I can book your appointment in seconds. What service do you need?
                </div>
                <div className="ml-auto max-w-[80%] rounded-2xl bg-[#FF6B00] px-4 py-2.5 text-sm text-white">
                  I need a consultation tomorrow at 10am
                </div>
                <div className="rounded-2xl bg-zinc-800/80 border border-zinc-700 px-4 py-3 text-sm">
                  <p className="text-zinc-200 mb-2">Great! Here&apos;s what&apos;s available on Apr 12:</p>
                  <div className="grid grid-cols-3 gap-2">
                    <span className="rounded-lg bg-zinc-700 px-2 py-1.5 text-center text-xs font-medium">10:00 AM</span>
                    <span className="rounded-lg bg-[#FF6B00] px-2 py-1.5 text-center text-xs font-semibold text-white">10:30 AM</span>
                    <span className="rounded-lg bg-zinc-700 px-2 py-1.5 text-center text-xs font-medium">11:00 AM</span>
                  </div>
                  <p className="text-xs text-zinc-500 mt-2 flex items-center gap-1">
                    <Clock className="w-3 h-3" /> Synced with Google Calendar
                  </p>
                </div>
                <div className="rounded-2xl bg-emerald-500/10 border border-emerald-500/20 px-4 py-3 text-sm">
                  <p className="font-medium text-emerald-400 flex items-center gap-2">
                    <CheckCircle2 className="w-4 h-4" /> Booking Confirmed!
                  </p>
                  <p className="text-xs text-zinc-400 mt-1">Google Meet link created • Confirmation email sent</p>
                  <p className="text-xs text-zinc-500 mt-1 flex items-center gap-1">
                    <Video className="w-3 h-3" /> meet.google.com/xxx-xxxx-xxx
                  </p>
                </div>
              </div>

              <div className="mt-4 flex items-center justify-between text-xs text-zinc-500">
                <span className="flex items-center gap-1.5">
                  <Globe className="w-3 h-3" /> Website • WhatsApp
                </span>
                <Link href="/privacy-policy" className="hover:text-zinc-300 underline underline-offset-4">
                  Privacy Policy
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="relative z-10 px-6 py-20 bg-zinc-900/30">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold mb-4">
              Everything You Need to <span className="text-[#FF6B00]">Automate Bookings</span>
            </h2>
            <p className="text-zinc-400 max-w-2xl mx-auto">
              Powerful features designed to save time, reduce no-shows, and grow your business.
            </p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((feature, index) => (
              <div
                key={index}
                className="group p-6 bg-zinc-900/50 border border-zinc-800 rounded-xl hover:border-[#FF6B00]/50 transition-all hover:transform hover:-translate-y-1"
              >
                <div className="w-12 h-12 rounded-lg bg-[#FF6B00]/10 flex items-center justify-center mb-4 group-hover:bg-[#FF6B00]/20 transition-colors">
                  <feature.icon className="w-6 h-6 text-[#FF6B00]" />
                </div>
                <h3 className="text-lg font-semibold mb-2">{feature.title}</h3>
                <p className="text-sm text-zinc-400">{feature.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section id="how-it-works" className="relative z-10 px-6 py-20">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold mb-4">
              Get Started in <span className="text-[#FF6B00]">3 Easy Steps</span>
            </h2>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {[
              { step: "01", title: "Create Account", desc: "Sign up in seconds. No credit card required." },
              { step: "02", title: "Configure Agent", desc: "Set your business hours, services, and preferences." },
              { step: "03", title: "Embed Widget", desc: "Add our snippet to your website and go live." },
            ].map((item, index) => (
              <div key={index} className="relative">
                <div className="text-6xl font-bold text-zinc-600 mb-4">{item.step}</div>
                <h3 className="text-xl font-semibold mb-2">{item.title}</h3>
                <p className="text-zinc-400">{item.desc}</p>
                {index < 2 && <ArrowRight className="hidden md:block absolute -right-4 top-8 text-zinc-700" />}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="relative z-10 px-6 py-16">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold mb-4">Ready to Automate Your Bookings?</h2>
          <p className="text-zinc-400 mb-8 max-w-2xl mx-auto">
            Join businesses using Aurexa to handle bookings 24/7, sync calendars, and create Google Meet
            links automatically.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link
              href="/register"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all hover:scale-105"
            >
              Start Free Trial
              <ArrowRight className="w-5 h-5" />
            </Link>
            <Link
              href="/login"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 border border-zinc-700 hover:border-zinc-600 text-zinc-300 font-medium rounded-lg transition-colors"
            >
              Sign In
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 px-6 py-8 border-t border-zinc-800">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-xs">
              A
            </div>
            <span className="text-sm text-zinc-500">© 2026 Aurexa. All rights reserved.</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-zinc-500">
            <Link href="/privacy-policy" className="hover:text-white transition-colors">
              Privacy Policy
            </Link>
            <Link href="/privacy-policy#contact" className="hover:text-white transition-colors">
              Contact
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
