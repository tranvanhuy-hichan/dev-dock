import { ReactNode } from "react";
import { CopyButton, TextArea } from "../../../components/ui";

export function Pane({ title, value, onChange, readOnly, placeholder, actions, rows, className }: {
  title: string; value: string; onChange?: (v: string) => void; readOnly?: boolean; placeholder?: string; actions?: ReactNode; rows?: number; className?: string;
}) {
  return (
    <div className={"flex flex-col min-h-0 " + (className ?? "")}>
      <div className="flex items-center justify-between mb-1 h-7">
        <span className="text-xs text-muted">{title}</span>
        <div className="flex items-center gap-1">{actions}{readOnly && <CopyButton text={value} />}</div>
      </div>
      <TextArea value={value} onChange={(e) => onChange?.(e.target.value)} readOnly={readOnly} placeholder={placeholder} rows={rows} className="flex-1 min-h-32" />
    </div>
  );
}

export const Row = ({ children, className }: { children: ReactNode; className?: string }) => (
  <div className={"flex flex-wrap items-center gap-2 " + (className ?? "")}>{children}</div>
);

export const enc = new TextEncoder();
export const dec = new TextDecoder();

export function utf8ToB64(s: string, urlSafe = false): string {
  const b = enc.encode(s);
  let bin = "";
  for (let i = 0; i < b.length; i += 0x8000) bin += String.fromCharCode(...b.subarray(i, i + 0x8000));
  const r = btoa(bin);
  return urlSafe ? r.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") : r;
}

export function b64ToBytes(s: string): Uint8Array<ArrayBuffer> {
  let t = s.trim().replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(t)) throw new Error("Chuỗi Base64 không hợp lệ");
  while (t.length % 4) t += "=";
  const bin = atob(t);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export const b64ToUtf8 = (s: string) => new TextDecoder("utf-8", { fatal: true }).decode(b64ToBytes(s));
