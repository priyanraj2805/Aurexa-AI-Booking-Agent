"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  ReactNode,
} from "react";

type ToastKind = "success" | "error" | "info";

type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
};

interface ToastContextType {
  toast: (kind: ToastKind, message: string) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    if (timers.current[id]) {
      clearTimeout(timers.current[id]);
      delete timers.current[id];
    }
  }, []);

  const toast = useCallback(
    (kind: ToastKind, message: string) => {
      const id = nextId++;
      setToasts((prev) => [...prev.slice(-4), { id, kind, message }]);
      timers.current[id] = setTimeout(() => dismiss(id), 4500);
    },
    [dismiss]
  );

  const styles: Record<ToastKind, string> = {
    success: "border-green-700 bg-green-950 text-green-200",
    error: "border-red-700 bg-red-950 text-red-200",
    info: "border-slate-700 bg-slate-900 text-slate-200",
  };

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="pointer-events-none fixed inset-x-4 top-4 z-50 mx-auto flex w-auto max-w-sm flex-col gap-2 sm:inset-x-auto sm:right-4 sm:mx-0 sm:w-96">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ${styles[t.kind]}`}
          >
            <span className="flex-1">{t.message}</span>
            <button
              onClick={() => dismiss(t.id)}
              className="text-xs text-slate-400 hover:text-slate-100"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
