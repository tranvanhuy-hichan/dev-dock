import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorBox, useToast } from "../../components/ui";
import { call, TreeData } from "../../lib/api";
import { bytes } from "../../lib/format";
import { squarify } from "./squarify";

export function TreemapTab({ active }: { active: boolean }) {
  const toast = useToast();
  const [parts, setParts] = useState<string[]>([]);
  const [data, setData] = useState<TreeData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const [dim, setDim] = useState({ w: 0, h: 0 });
  const gen = useRef(0);

  const rel = parts.join("/");
  const load = useCallback(async (r: string) => {
    const g = ++gen.current;
    setLoading(true); setErr(null);
    try { const d = await call<TreeData>("cleaner_tree", { rel: r }); if (g === gen.current) setData(d); }
    catch (e) { if (g === gen.current) setErr((e as Error).message); }
    if (g === gen.current) setLoading(false);
  }, []);
  useEffect(() => { if (active) load(rel); }, [active, rel, load]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setDim({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data]);

  const entries = useMemo(() => (data?.entries ?? []).filter((e) => e.size > 0), [data]);
  const rects = useMemo(() => squarify(entries.map((e) => e.size), 0, 0, dim.w, dim.h), [entries, dim]);

  return (
    <div className="h-full flex flex-col p-5 gap-3">
      <div className="flex items-center gap-1 text-sm flex-wrap">
        <button onClick={() => setParts([])} className="text-accent hover:underline cursor-pointer">~</button>
        {parts.map((p, i) => (<span key={i} className="flex items-center gap-1"><span className="text-muted">/</span><button onClick={() => setParts(parts.slice(0, i + 1))} className="text-accent hover:underline cursor-pointer">{p}</button></span>))}
        {data && <span className="ml-3 text-muted">{bytes(data.total)} tổng · bấm vào ô thư mục để đào sâu, chuột phải để mở</span>}
        {loading && <span className="ml-2 text-muted">đang đo…</span>}
      </div>
      {err && <ErrorBox>{err}</ErrorBox>}
      <div ref={box} className="flex-1 min-h-64 relative rounded-lg overflow-hidden bg-panel border border-line">
        {data && !entries.length && <Empty title="Thư mục trống" />}
        {rects.map(([i, x, y, w, h]) => {
          const e = entries[i];
          return (
            <div
              key={e.name}
              title={`${e.name} — ${bytes(e.size)}${e.dir ? "" : " (tệp)"}`}
              onClick={() => e.dir && setParts([...parts, e.name])}
              onContextMenu={(ev) => { ev.preventDefault(); call("cleaner_open", { path: `~/${[...parts, e.name].join("/")}` }).catch((er) => toast(er.message, "err")); }}
              className={"absolute overflow-hidden border border-bg " + (e.dir ? "cursor-pointer hover:brightness-125" : "")}
              style={{ left: x, top: y, width: w, height: h, background: `hsl(${(i * 47 + 230) % 360} ${e.dir ? 45 : 20}% ${e.dir ? 32 : 26}%)` }}
            >
              {w > 60 && h > 32 && <div className="p-1.5 text-white/90 leading-tight"><div className="text-xs font-medium truncate">{e.name}{e.dir ? "/" : ""}</div><div className="text-[11px] opacity-70">{bytes(e.size)}</div></div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
