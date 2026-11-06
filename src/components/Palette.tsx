import { useEffect, useMemo, useRef, useState } from "react";
import { cx } from "./ui";

export interface PaletteItem { id: string; label: string; hint?: string; run: () => void }

export function Palette({ open, items, onClose }: { open: boolean; items: PaletteItem[]; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setQ(""); setSel(0); setTimeout(() => ref.current?.focus(), 0); } }, [open]);
  const list = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? items.filter((i) => i.label.toLowerCase().includes(t)) : items;
  }, [q, items]);
  useEffect(() => setSel(0), [q]);
  if (!open) return null;

  const go = (i?: PaletteItem) => { if (i) { onClose(); i.run(); } };
  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-start justify-center pt-[15vh]" onClick={onClose}>
      <div className="w-full max-w-lg bg-panel border border-line rounded-xl shadow-2xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <input
          ref={ref}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(list.length - 1, s + 1)); }
            else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
            else if (e.key === "Enter") go(list[sel]);
          }}
          placeholder="Gõ lệnh hoặc tên tool…"
          className="w-full bg-transparent px-4 py-3 outline-none border-b border-line"
        />
        <div className="max-h-80 overflow-auto p-1">
          {list.length === 0 && <div className="px-3 py-6 text-center text-muted text-sm">Không có kết quả</div>}
          {list.map((i, idx) => (
            <button key={i.id} onClick={() => go(i)} onMouseMove={() => setSel(idx)} className={cx("w-full flex justify-between items-center px-3 py-2 rounded-md text-sm text-left cursor-pointer", idx === sel && "bg-accent/15")}>
              <span>{i.label}</span>
              {i.hint && <span className="text-xs text-muted">{i.hint}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
