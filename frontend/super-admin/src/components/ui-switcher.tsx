"use client";

import { useEffect, useState } from "react";
import { Palette, Shield } from "lucide-react";

export const UI_MODE_STORAGE_KEY = "super_admin_ui_mode";

export function applyUiMode(mode: "classic" | "admin") {
  document.documentElement.setAttribute("data-ui", mode);
  try {
    localStorage.setItem(UI_MODE_STORAGE_KEY, mode);
  } catch {
    // storage unavailable — attribute still applies for the session
  }
}

export function UiSwitcher() {
  const [mode, setMode] = useState<"classic" | "admin">("classic");

  useEffect(() => {
    let stored = "classic";
    try {
      stored = localStorage.getItem(UI_MODE_STORAGE_KEY) ?? "classic";
    } catch {
      // storage unavailable — default to classic
    }
    const m = stored === "admin" ? "admin" : "classic";
    setMode(m);
    document.documentElement.setAttribute("data-ui", m);
  }, []);

  const toggle = () => {
    const next = mode === "admin" ? "classic" : "admin";
    setMode(next);
    applyUiMode(next);
  };

  return (
    <button
      onClick={toggle}
      title={
        mode === "admin"
          ? "Switch to control-plane UI"
          : "Switch to admin-panel UI"
      }
      aria-label={
        mode === "admin"
          ? "Switch to control-plane UI"
          : "Switch to admin-panel UI"
      }
      className="ui-switch inline-flex size-8 shrink-0 items-center justify-center rounded-lg border border-transparent text-sm font-medium text-slate-400 outline-none transition-all select-none hover:bg-slate-800 hover:text-slate-200 active:translate-y-px"
    >
      {mode === "admin" ? (
        <Shield className="size-4" />
      ) : (
        <Palette className="size-4" />
      )}
    </button>
  );
}