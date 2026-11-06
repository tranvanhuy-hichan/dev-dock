import { Check, Copy } from "lucide-react";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" | "soft"; size?: "sm" | "md" };
export function Button({ variant = "soft", size = "md", className, ...p }: BtnProps) {
  const v = {
    primary: "bg-accent text-accentfg hover:brightness-110",
    soft: "bg-panel2 hover:bg-line border border-line",
    ghost: "hover:bg-panel2 text-muted hover:text-fg",
    danger: "bg-bad/15 text-bad hover:bg-bad/25 border border-bad/30",
  }[variant];
  const s = size === "sm" ? "px-2 py-1 text-xs gap-1" : "px-3 py-1.5 text-sm gap-1.5";
  return (
    <button
      {...p}
      className={cx("inline-flex items-center justify-center rounded-md font-medium transition disabled:opacity-40 disabled:pointer-events-none cursor-pointer select-none", v, s, className)}
    />
  );
}

export const Input = ({ className, ...p }: React.InputHTMLAttributes<HTMLInputElement>) => (
  <input {...p} className={cx("bg-panel2 border border-line rounded-md px-2.5 py-1.5 text-sm outline-none focus:border-accent w-full placeholder:text-muted/70", className)} />
);

export const TextArea = ({ className, ...p }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
  <textarea
    spellCheck={false}
    {...p}
    className={cx("bg-panel2 border border-line rounded-md px-2.5 py-2 text-sm font-mono outline-none focus:border-accent w-full resize-none placeholder:text-muted/70", className)}
  />
);

export const Select = ({ className, ...p }: React.SelectHTMLAttributes<HTMLSelectElement>) => (
  <select {...p} className={cx("bg-panel2 border border-line rounded-md px-2 py-1.5 text-sm outline-none focus:border-accent", className)} />
);

export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => (
  <div {...p} className={cx("bg-panel border border-line rounded-lg", className)} />
);

export function Badge({ tone = "muted", children }: { tone?: "muted" | "ok" | "warn" | "bad" | "accent"; children: ReactNode }) {
  const t = { muted: "bg-panel2 text-muted", ok: "bg-ok/15 text-ok", warn: "bg-warn/15 text-warn", bad: "bg-bad/15 text-bad", accent: "bg-accent/15 text-accent" }[tone];
  return <span className={cx("inline-block px-1.5 py-0.5 rounded text-[11px] font-medium whitespace-nowrap", t)}>{children}</span>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; icon?: ReactNode; count?: number }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cx("px-3 py-2 text-sm flex items-center gap-1.5 -mb-px border-b-2 cursor-pointer transition", value === t.id ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg")}
        >
          {t.icon}
          {t.label}
          {t.count !== undefined && <span className="text-[11px] text-muted">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy", size = "sm" }: { text: string | (() => string); label?: string; size?: "sm" | "md" }) {
  const [ok, setOk] = useState(false);
  const t = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(t.current), []);
  return (
    <Button
      size={size}
      variant="ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(typeof text === "function" ? text() : text);
          setOk(true);
          clearTimeout(t.current);
          t.current = setTimeout(() => setOk(false), 1200);
        } catch { /* clipboard không khả dụng */ }
      }}
    >
      {ok ? <Check size={13} className="text-ok" /> : <Copy size={13} />}
      {label}
    </Button>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <div className="text-xs text-muted mb-1 flex justify-between"><span>{label}</span>{hint}</div>
      {children}
    </label>
  );
}

export function Empty({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: ReactNode }) {
  return (
    <div className="h-full min-h-40 flex flex-col items-center justify-center text-center text-muted gap-2 p-6">
      {icon}
      <div className="text-fg font-medium">{title}</div>
      {hint && <div className="text-sm max-w-md">{hint}</div>}
    </div>
  );
}

export const ErrorBox = ({ children }: { children: ReactNode }) => (
  <div className="text-bad bg-bad/10 border border-bad/30 rounded-md px-3 py-2 text-sm whitespace-pre-wrap break-words">{children}</div>
);

export function Bar({ value, tone }: { value: number; tone?: "ok" | "warn" | "bad" | "accent" }) {
  const v = Math.max(0, Math.min(100, value));
  const auto = tone ?? (v > 85 ? "bad" : v > 65 ? "warn" : "accent");
  const c = { ok: "bg-ok", warn: "bg-warn", bad: "bg-bad", accent: "bg-accent" }[auto];
  return (
    <div className="h-1.5 rounded bg-panel2 overflow-hidden">
      <div className={cx("h-full rounded transition-[width] duration-500", c)} style={{ width: `${v}%` }} />
    </div>
  );
}

// ---- Toast ----
type ToastFn = (msg: string, kind?: "ok" | "err") => void;
const ToastCtx = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; kind: "ok" | "err" }[]>([]);
  const seq = useRef(0);
  const push = useCallback<ToastFn>((msg, kind = "ok") => {
    const id = ++seq.current;
    setItems((x) => [...x, { id, msg, kind }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), kind === "err" ? 6000 : 2500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md">
        {items.map((i) => (
          <div key={i.id} className={cx("px-3 py-2 rounded-md text-sm border shadow-lg bg-panel whitespace-pre-wrap break-words", i.kind === "err" ? "border-bad/50 text-bad" : "border-line")}>
            {i.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function Confirm({ open, title, body, onYes, onNo, danger = true }: { open: boolean; title: string; body?: ReactNode; onYes: () => void; onNo: () => void; danger?: boolean }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={onNo}>
      <Card className="p-5 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
        <div className="font-semibold mb-1">{title}</div>
        {body && <div className="text-sm text-muted mb-4">{body}</div>}
        <div className="flex justify-end gap-2">
          <Button onClick={onNo}>Huỷ</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onYes} autoFocus>Xác nhận</Button>
        </div>
      </Card>
    </div>
  );
}
