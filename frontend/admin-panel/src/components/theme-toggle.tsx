"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

interface DocumentWithViewTransition extends Document {
  startViewTransition?: (update: () => void) => void;
}

function switchThemeWithTransition(setTheme: (t: string) => void, next: string) {
  const transitionDocument = document as DocumentWithViewTransition;
  // View Transitions API — GPU-composited cross-fade, no stutter
  if (
    typeof document !== "undefined" &&
    typeof transitionDocument.startViewTransition === "function" &&
    // Respect reduced motion preference
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    transitionDocument.startViewTransition(() => {
      setTheme(next);
    });
    return;
  }

  // CSS fallback for browsers without View Transitions API
  const root = document.documentElement;
  root.classList.add("theme-transitioning");
  setTheme(next);
  // Use requestAnimationFrame to let the new styles paint before removing class
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      setTimeout(() => root.classList.remove("theme-transitioning"), 400);
    });
  });
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => setMounted(true), []);

  const handleToggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    switchThemeWithTransition(setTheme, next);
  };

  if (!mounted) {
    return (
      <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground">
        <Sun className="size-4 opacity-0" />
      </Button>
    );
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-8 w-8 text-muted-foreground hover:text-foreground theme-toggle-btn"
      onClick={handleToggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <Sun className="size-4 theme-icon" />
      ) : (
        <Moon className="size-4 theme-icon" />
      )}
    </Button>
  );
}
