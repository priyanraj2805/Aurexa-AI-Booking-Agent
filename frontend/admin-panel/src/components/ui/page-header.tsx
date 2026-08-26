"use client";

// TradeBotX-style page header: title + muted subtitle on the left, action
// cluster on the right. Used across dashboard pages for a consistent frame.
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
      <div className="min-w-0">
        <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
          {title}
        </h1>
        {subtitle && (
          <p className="text-muted-foreground text-xs sm:text-sm mt-1.5">{subtitle}</p>
        )}
      </div>
      {actions && (
        <div className="flex flex-wrap items-center gap-2 shrink-0">{actions}</div>
      )}
    </div>
  );
}
