"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import Link from "next/link";
import { OtpStep } from "@/components/auth/otp-step";
import { 
  Loader2, 
  Eye, 
  EyeOff, 
  MessageSquare, 
  Calendar, 
  Zap, 
  Globe, 
  ArrowRight,
  CheckCircle2,
  Bot,
  BarChart3,
  CreditCard
} from "lucide-react";

const features = [
  {
    icon: Bot,
    title: "AI-Powered Conversations",
    description: "Intelligent chatbot that understands context and handles bookings naturally."
  },
  {
    icon: Calendar,
    title: "Smart Scheduling",
    description: "Automatic calendar sync with real-time availability management."
  },
  {
    icon: Globe,
    title: "Multi-Channel",
    description: "Works on your website, WhatsApp, and more — one agent, everywhere."
  },
  {
    icon: BarChart3,
    title: "Analytics Dashboard",
    description: "Track bookings, peak hours, and customer insights at a glance."
  },
  {
    icon: CreditCard,
    title: "Payment Integration",
    description: "Accept deposits and payments seamlessly during booking."
  },
  {
    icon: Zap,
    title: "Instant Setup",
    description: "Get up and running in minutes with our no-code configuration."
  }
];

export default function LandingPage() {
  const [activeTab, setActiveTab] = useState<"login" | "register">("login");
  const [isLoading, setIsLoading] = useState(false);
  
  // Login form
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loginError, setLoginError] = useState("");
  
  // Register form
  const [fullName, setFullName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showRegPassword, setShowRegPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [regError, setRegError] = useState("");
  const [registerStep, setRegisterStep] = useState<"form" | "otp">("form");

  const { login, register, sendOtp, isAuthenticated } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, router]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginError("");
    setIsLoading(true);
    try {
      await login(email, password);
    } catch (err: unknown) {
      let message = "Login failed";
      if (err instanceof TypeError && err.message.includes("fetch")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setLoginError(message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setRegError("");
    
    if (regPassword !== confirmPassword) {
      setRegError("Passwords do not match");
      return;
    }
    if (regPassword.length < 6) {
      setRegError("Password must be at least 6 characters");
      return;
    }

    setIsLoading(true);
    try {
      // Validates the email is free + emails a one-time code.
      await sendOtp(regEmail);
      setRegisterStep("otp");
    } catch (err: unknown) {
      let message = "Registration failed";
      if (err instanceof TypeError && err.message.includes("fetch")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else if (err instanceof Error) {
        message = err.message;
      }
      setRegError(message);
    } finally {
      setIsLoading(false);
    }
  };

  const handleVerifyRegister = async (code: string) => {
    await register({ full_name: fullName, org_name: orgName, email: regEmail, password: regPassword, otp_code: code });
    setEmail(regEmail);
    setLoginError("");
    setRegisterStep("form");
    setActiveTab("login");
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white overflow-x-hidden" style={{ scrollPaddingTop: '50px' }}>
      {/* Animated Background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-[#FF6B00]/10 rounded-full blur-[150px]" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-[#3b82f6]/10 rounded-full blur-[150px]" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-purple-500/5 rounded-full blur-[200px]" />
      </div>

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-6 py-4 max-w-7xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-sm">
            DS
          </div>
          <span className="text-xl font-bold tracking-tight">DotStark AI</span>
        </div>
        <nav className="hidden md:flex items-center gap-8">
          <button onClick={() => document.getElementById('features')?.scrollIntoView({ behavior: 'smooth' })} className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer">Features</button>
          <button onClick={() => document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' })} className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer">How it Works</button>
          <button onClick={() => document.getElementById('pricing')?.scrollIntoView({ behavior: 'smooth' })} className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer">Pricing</button>
        </nav>
        <div className="flex items-center gap-3">
          <button 
            onClick={() => setActiveTab("login")}
            className="px-4 py-2 text-sm font-medium text-zinc-300 hover:text-white transition-colors"
          >
            Sign In
          </button>
          <button 
            onClick={() => setActiveTab("register")}
            className="px-4 py-2 text-sm font-semibold bg-[#FF6B00] hover:bg-[#e55f00] text-white rounded-lg transition-colors"
          >
            Get Started
          </button>
        </div>
      </header>

      {/* Hero Section */}
      <section className="relative z-10 px-6 py-8 md:py-10 max-w-7xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-20 items-center">
          {/* Left Content */}
          <div className="text-center lg:text-left">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-800/50 border border-zinc-700 text-xs text-zinc-400 mb-6">
              <Zap className="w-3 h-3 text-[#FF6B00]" />
              AI-Powered Booking Assistant
            </div>
            <h1 className="text-4xl md:text-5xl lg:text-6xl font-bold leading-tight mb-6">
              Your 24/7 Booking Agent{" "}
              <span className="text-[#FF6B00]">That Never Sleeps</span>
            </h1>
            <p className="text-lg text-zinc-400 mb-8 max-w-xl mx-auto lg:mx-0">
              Automatically handle appointments, answer questions, and confirm bookings. 
              Integrate with your website and WhatsApp in minutes.
            </p>
            <div className="flex flex-col sm:flex-row items-center gap-4 justify-center lg:justify-start">
              <button 
                onClick={() => setActiveTab("register")}
                className="inline-flex items-center gap-2 px-6 py-3 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all hover:scale-105"
              >
                Start Free Trial
                <ArrowRight className="w-4 h-4" />
              </button>
              <button className="inline-flex items-center gap-2 px-6 py-3 border border-zinc-700 hover:border-zinc-600 text-zinc-300 font-medium rounded-lg transition-colors">
                <MessageSquare className="w-4 h-4" />
                See Demo
              </button>
            </div>
          </div>

          {/* Right - Auth Card */}
          <div className="flex justify-center lg:justify-end">
            <div className="w-full max-w-md bg-zinc-900/80 backdrop-blur-xl border border-zinc-800 rounded-2xl p-1">
              {/* Tab Switcher with animated indicator */}
              <div className="relative flex p-1 bg-zinc-800/50 rounded-xl mb-4">
                {/* Animated sliding indicator with glow */}
                <div 
                  className="absolute top-0.5 bottom-0.5 bg-gradient-to-r from-[#FF6B00] to-orange-600 rounded-lg shadow-lg shadow-orange-500/20 transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)]"
                  style={{ 
                    width: 'calc(50% - 4px)',
                    left: activeTab === 'login' ? '4px' : 'calc(50% + 0px)',
                    transform: activeTab === 'register' ? 'scale(1.02)' : 'scale(1)',
                  }}
                />
                <button
                  onClick={() => setActiveTab("login")}
                  className={`flex-1 py-2.5 text-sm font-semibold rounded-lg transition-all duration-300 relative z-10 ${
                    activeTab === "login" 
                      ? "text-white scale-105" 
                      : "text-zinc-400 hover:text-white"
                  }`}
                >
                  Sign In
                </button>
                <button
                  onClick={() => setActiveTab("register")}
                  className={`flex-1 py-2.5 text-sm font-semibold rounded-lg transition-all duration-300 relative z-10 ${
                    activeTab === "register" 
                      ? "text-white scale-105" 
                      : "text-zinc-400 hover:text-white"
                  }`}
                >
                  Create Account
                </button>
              </div>

              {/* Forms */}
              <div className="px-6 pb-6" style={{ minHeight: activeTab === 'login' ? '240px' : '340px' }}>
                {activeTab === "login" ? (
                  <form onSubmit={handleLogin} className="space-y-4">
                    {loginError && (
                      <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                        {loginError}
                      </div>
                    )}
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                        Email
                      </label>
                      <input
                        type="email"
                        id="login-email"
                        name="email"
                        autoComplete="email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        placeholder="name@company.com"
                        required
                        className="w-full px-4 py-3 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                        Password
                      </label>
                      <div className="relative">
                        <input
                          type={showPassword ? "text" : "password"}
                          id="login-password"
                          name="password"
                          autoComplete="current-password"
                          value={password}
                          onChange={(e) => setPassword(e.target.value)}
                          placeholder="Enter your password"
                          required
                          className="w-full px-4 py-3 pr-10 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                        />
                        <button
                          type="button"
                          onClick={() => setShowPassword(!showPassword)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                        >
                          {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                        </button>
                      </div>
                    </div>
                    <button
                      type="submit"
                      disabled={isLoading}
                      className="w-full py-3 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                      {isLoading ? "Signing in..." : "Sign In"}
                    </button>
                    <div className="text-center pt-1">
                      <Link
                        href="/forgot-password"
                        className="text-xs text-zinc-400 hover:text-[#FF6B00] transition-colors"
                      >
                        Forgot password?
                      </Link>
                    </div>
                  </form>
                ) : registerStep === "otp" ? (
                    <OtpStep
                      email={regEmail}
                      title="Verify Your Email"
                      subtitle="We emailed a 6-digit code to"
                      submitLabel="Verify & Create Account"
                      onVerify={handleVerifyRegister}
                      onResend={async () => {
                        await sendOtp(regEmail);
                      }}
                      onBack={() => setRegisterStep("form")}
                    />
                ) : (
                  <form onSubmit={handleRegister} className="space-y-4">
                    {regError && (
                      <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
                        {regError}
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                          Full Name
                        </label>
                        <input
                          type="text"
                          value={fullName}
                          onChange={(e) => setFullName(e.target.value)}
                          placeholder="John Doe"
                          required
                          className="w-full px-4 py-3 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                          Organization
                        </label>
                        <input
                          type="text"
                          value={orgName}
                          onChange={(e) => setOrgName(e.target.value)}
                          placeholder="Acme Inc"
                          required
                          className="w-full px-4 py-3 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                        Work Email
                      </label>
                      <input
                        type="email"
                        value={regEmail}
                        onChange={(e) => setRegEmail(e.target.value)}
                        placeholder="name@company.com"
                        required
                        className="w-full px-4 py-3 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                          Password
                        </label>
                        <div className="relative">
                          <input
                            type={showRegPassword ? "text" : "password"}
                            value={regPassword}
                            onChange={(e) => setRegPassword(e.target.value)}
                            placeholder="Min. 6 chars"
                            required
                            className="w-full px-4 py-3 pr-10 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                          />
                          <button
                            type="button"
                            onClick={() => setShowRegPassword(!showRegPassword)}
                            aria-label={showRegPassword ? "Hide password" : "Show password"}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                          >
                            {showRegPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                          </button>
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-zinc-400 mb-1.5 uppercase tracking-wider">
                          Confirm
                        </label>
                        <div className="relative">
                          <input
                            type={showConfirmPassword ? "text" : "password"}
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            placeholder="Repeat"
                            required
                            className="w-full px-4 py-3 pr-10 bg-zinc-800/50 border border-zinc-700 rounded-lg text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#FF6B00] focus:ring-1 focus:ring-[#FF6B00]/20 transition-all"
                          />
                          <button
                            type="button"
                            onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                            aria-label={showConfirmPassword ? "Hide password" : "Show password"}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                          >
                            {showConfirmPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                          </button>
                        </div>
                      </div>
                    </div>
                    <button
                      type="submit"
                      disabled={isLoading}
                      className="w-full py-3 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all disabled:opacity-50 flex items-center justify-center gap-2"
                    >
                      {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
                      {isLoading ? "Sending code..." : "Create Free Account"}
                    </button>
                    <p className="text-xs text-zinc-500 text-center">
                      No credit card required. 14-day free trial.
                    </p>
                  </form>
                )}
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
              { step: "03", title: "Embed Widget", desc: "Add our snippet to your website and go live." }
            ].map((item, index) => (
              <div key={index} className="relative">
                <div className="text-6xl font-bold text-zinc-600 mb-4">{item.step}</div>
                <h3 className="text-xl font-semibold mb-2">{item.title}</h3>
                <p className="text-zinc-400">{item.desc}</p>
                {index < 2 && (
                  <ArrowRight className="hidden md:block absolute -right-4 top-8 text-zinc-700" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="relative z-10 px-6 py-16">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl md:text-4xl font-bold mb-8">
            Ready to Automate Your Bookings?
          </h2>
          <button 
            onClick={() => { window.scrollTo({ top: 0, behavior: 'smooth' }); setActiveTab("register"); }}
            className="inline-flex items-center gap-2 px-8 py-4 bg-[#FF6B00] hover:bg-[#e55f00] text-white font-semibold rounded-lg transition-all hover:scale-105"
          >
            Start Free Trial
            <ArrowRight className="w-5 h-5" />
          </button>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 px-6 py-8 border-t border-zinc-800">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-xs">
              DS
            </div>
            <span className="text-sm text-zinc-500">© 2026 DotStark AI. All rights reserved.</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-zinc-500">
            <a href="#" className="hover:text-white transition-colors">Privacy</a>
            <a href="#" className="hover:text-white transition-colors">Terms</a>
            <a href="#" className="hover:text-white transition-colors">Contact</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
