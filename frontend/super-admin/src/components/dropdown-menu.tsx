"use client";

import { createContext, useContext, useState, useRef, useEffect, ReactNode } from "react";

type MenuCtx = { open: boolean; setOpen: (v: boolean) => void };
const Ctx = createContext<MenuCtx>({ open: false, setOpen: () => {} });

type SubCtx = { open: boolean; setOpen: (v: boolean) => void };
const SubContext = createContext<SubCtx>({ open: false, setOpen: () => {} });

export function DropdownMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  return (
    <Ctx.Provider value={{ open, setOpen }}>
      <div ref={containerRef} className="relative">
        {children}
      </div>
    </Ctx.Provider>
  );
}

export function DropdownMenuTrigger({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { open, setOpen } = useContext(Ctx);
  return (
    <button onClick={() => setOpen(!open)} className={className}>
      {children}
    </button>
  );
}

export function DropdownMenuContent({
  children,
  align = "start",
  side = "top",
  className,
}: {
  children: ReactNode;
  align?: "start" | "end";
  side?: "top" | "bottom";
  className?: string;
}) {
  const { open } = useContext(Ctx);
  if (!open) return null;
  const alignCls = align === "end" ? "right-0" : "left-0";
  const sideCls = side === "top" ? "bottom-full mb-1" : "top-full mt-1";
  return (
    <div
      className={`absolute z-50 min-w-[13rem] rounded-md border border-slate-700 bg-slate-900 p-1 shadow-xl ${alignCls} ${sideCls} ${className ?? ""}`}
    >
      {children}
    </div>
  );
}

export function DropdownMenuItem({
  children,
  onClick,
  active,
  destructive,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
  destructive?: boolean;
  className?: string;
}) {
  const { setOpen } = useContext(Ctx);
  return (
    <button
      onClick={() => {
        onClick?.();
        setOpen(false);
      }}
      className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors cursor-pointer ${
        destructive
          ? "text-red-400 hover:bg-red-950"
          : active
            ? "bg-slate-800 text-white font-medium"
            : "text-slate-300 hover:bg-slate-800"
      } ${className ?? ""}`}
    >
      {children}
    </button>
  );
}

export function DropdownMenuSeparator() {
  return <div className="my-1 h-px bg-slate-700" />;
}

export function DropdownMenuLabel({ children }: { children: ReactNode }) {
  return <div className="px-2 py-1.5 text-xs text-slate-500">{children}</div>;
}

export function DropdownMenuSub({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <SubContext.Provider value={{ open, setOpen }}>
      <div
        className="relative"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
      >
        {children}
      </div>
    </SubContext.Provider>
  );
}

export function DropdownMenuSubTrigger({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { open, setOpen } = useContext(SubContext);
  return (
    <button
      onClick={() => setOpen(!open)}
      className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-slate-300 hover:bg-slate-800 cursor-pointer ${className ?? ""}`}
    >
      {children}
      <svg className="ml-auto size-3" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 2l4 4-4 4"/></svg>
    </button>
  );
}

export function DropdownMenuSubContent({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const { open } = useContext(SubContext);
  if (!open) return null;
  return (
    <div
      className={`absolute left-full top-0 ml-1 min-w-[10rem] rounded-md border border-slate-700 bg-slate-900 p-1 shadow-xl z-50 ${className ?? ""}`}
    >
      {children}
    </div>
  );
}

export function DropdownSubItem({
  children,
  onClick,
  active,
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
  className?: string;
}) {
  const { setOpen: closeSub } = useContext(SubContext);
  const { setOpen: closeParent } = useContext(Ctx);
  return (
    <button
      onClick={() => {
        onClick?.();
        closeSub(false);
        closeParent(false);
      }}
      className={`flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors cursor-pointer ${
        active
          ? "bg-slate-800 text-white font-medium"
          : "text-slate-300 hover:bg-slate-800"
      } ${className ?? ""}`}
    >
      {children}
    </button>
  );
}