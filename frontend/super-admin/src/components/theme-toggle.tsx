"use client";

import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

const STORAGE_KEY = "super_admin_theme";

function getInitialTheme(): "dark" | "light" {
  if (typeof window === "undefined") return "dark";
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {}
  return "dark";
}

export function ThemeToggle({ collapsed }: { collapsed?: boolean }) {
  const [theme, setThemeState] = useState<"dark" | "light">("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const t = getInitialTheme();
    setThemeState(t);
    document.documentElement.classList.toggle("dark", t === "dark");
    document.documentElement.classList.toggle("light", t === "light");
    setMounted(true);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setThemeState(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    document.documentElement.classList.toggle("light", next === "light");
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {}
  };

  if (!mounted) return null;

  if (collapsed) {
    return (
      <button
        onClick={toggle}
        title={theme === "dark" ? "Switch to White" : "Switch to Dark"}
        className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
      >
        {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
      </button>
    );
  }

  return (
    <button
      onClick={toggle}
      className="flex w-full items-center gap-2 rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 transition-colors"
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
      {theme === "dark" ? "Switch to White" : "Switch to Dark"}
    </button>
  );
}
