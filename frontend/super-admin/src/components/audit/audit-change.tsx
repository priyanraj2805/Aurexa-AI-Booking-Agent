const TONES = {
  red: { badge: "bg-red-900/60 text-red-300", value: "text-red-300" },
  green: { badge: "bg-emerald-900/60 text-emerald-300", value: "text-emerald-300" },
  blue: { badge: "bg-blue-900/60 text-blue-300", value: "text-blue-300" },
  purple: { badge: "bg-purple-900/60 text-purple-300", value: "text-purple-300" },
  amber: { badge: "bg-amber-900/60 text-amber-300", value: "text-amber-300" },
  slate: { badge: "bg-slate-800 text-slate-300", value: "text-slate-300" },
} as const;

type Tone = keyof typeof TONES;

const ACTIONS: Record<string, { label: string; tone: Tone }> = {
  "org.suspend": { label: "Organization suspended", tone: "red" },
  "org.restore": { label: "Organization restored", tone: "green" },
  "org.delete": { label: "Organization deleted", tone: "red" },
  "org.create": { label: "Organization created", tone: "green" },
  "org.update": { label: "Organization updated", tone: "blue" },
  "org.config.update": { label: "Configuration updated", tone: "blue" },
  "config.update": { label: "Configuration updated", tone: "blue" },
  "user.disable": { label: "User disabled", tone: "red" },
  "user.enable": { label: "User enabled", tone: "green" },
  "impersonation.start": { label: "Impersonation started", tone: "purple" },
  "impersonation.end": { label: "Impersonation ended", tone: "purple" },
  "peek.consent.request": { label: "Peek consent requested", tone: "purple" },
  "peek.consent.confirmed": { label: "Peek consent confirmed", tone: "purple" },
  "peek.consent.revoked": { label: "Peek consent revoked", tone: "amber" },
  "support.conversation.peek": { label: "Conversation peeked", tone: "purple" },
  "support.ticket.created": { label: "Support ticket created", tone: "blue" },
  "support.ticket.updated": { label: "Support ticket updated", tone: "blue" },
  "auth.stepup": { label: "Step-up auth challenged", tone: "amber" },
  "admin.login": { label: "Admin signed in", tone: "blue" },
  "notifications.create": { label: "Broadcast created", tone: "blue" },
  "notifications.send": { label: "Broadcast sent", tone: "blue" },
  "notifications.review": { label: "Broadcast reviewed", tone: "blue" },
  "incidents.create": { label: "Incident created", tone: "amber" },
  "incidents.update": { label: "Incident updated", tone: "amber" },
};

function humanizeKey(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function formatValue(v: unknown): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object" && v !== null) return JSON.stringify(v);
  if (v === undefined) return "null";
  return String(v);
}

function ValueChip({ value, tone }: { value: unknown; tone: Tone }) {
  const className =
    typeof value === "boolean"
      ? value
        ? "text-emerald-400"
        : "text-red-400"
      : TONES[tone].value;
  return <span className={`font-mono ${className}`}>{formatValue(value)}</span>;
}

function DiffList({
  oldValues,
  newValues,
  tone,
}: {
  oldValues?: unknown;
  newValues?: unknown;
  tone: Tone;
}) {
  const oldMap = isRecord(oldValues) ? oldValues : {};
  const newMap = isRecord(newValues) ? newValues : {};
  const keys = Array.from(
    new Set([...Object.keys(oldMap), ...Object.keys(newMap)])
  );
  const changed = keys.filter(
    (k) =>
      !Object.prototype.hasOwnProperty.call(oldMap, k) ||
      !Object.prototype.hasOwnProperty.call(newMap, k) ||
      oldMap[k] !== newMap[k]
  );

  if (changed.length === 0) return <span className="text-slate-500">—</span>;

  return (
    <div className="space-y-0.5">
      {changed.map((k) => {
        const hasOld = Object.prototype.hasOwnProperty.call(oldMap, k);
        const hasNew = Object.prototype.hasOwnProperty.call(newMap, k);
        return (
          <div key={k} className="text-xs">
            <span className="text-slate-500">{humanizeKey(k)}:</span>{" "}
            {hasOld && hasNew ? (
              <span className="text-slate-400">
                <ValueChip value={oldMap[k]} tone={tone} />
                <span className="mx-1 text-slate-600">→</span>
                <ValueChip value={newMap[k]} tone={tone} />
              </span>
            ) : hasNew ? (
              <span className="text-emerald-400">
                + <ValueChip value={newMap[k]} tone={tone} />
              </span>
            ) : (
              <span className="text-red-400">
                − <ValueChip value={oldMap[k]} tone={tone} />
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function AuditAction({ action }: { action: string }) {
  const meta = ACTIONS[action] ?? {
    label: action.replace(/\./g, " "),
    tone: "slate" as Tone,
  };
  return (
    <div className="flex flex-col items-start gap-1">
      <span
        className={`rounded px-2 py-0.5 text-xs font-medium ${TONES[meta.tone].badge}`}
      >
        {meta.label}
      </span>
      <span className="font-mono text-[10px] text-slate-600">{action}</span>
    </div>
  );
}

export function AuditChanges({
  action,
  oldValues,
  newValues,
}: {
  action: string;
  oldValues?: unknown;
  newValues?: unknown;
}) {
  const tone = ACTIONS[action]?.tone ?? "slate";
  return (
    <DiffList oldValues={oldValues} newValues={newValues} tone={tone} />
  );
}