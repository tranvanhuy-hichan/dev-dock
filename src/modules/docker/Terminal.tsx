import { listen } from "@tauri-apps/api/event";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";
import { call } from "../../lib/api";

const dec = (b64: string) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

export default function TerminalView({ id, cmd, visible }: { id: string; cmd: string[]; visible: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<{ fit: () => void } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const el = host.current!;
    const term = new XTerm({
      fontFamily: 'ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace', fontSize: 13, cursorBlink: true, scrollback: 5000,
      theme: { background: "#0d0f14", foreground: "#e5e7ee", cursor: "#6d7cff", selectionBackground: "#6d7cff55" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    let alive = true;
    let opened = false;
    let unlisten: (() => void) | undefined;

    const safeFit = () => {
      if (el.clientWidth < 20 || el.clientHeight < 20) return;
      try { fit.fit(); } catch { /* chưa có kích thước */ }
      if (opened) call("pty_resize", { id, cols: term.cols, rows: term.rows }).catch(() => {});
    };
    fitRef.current = { fit: safeFit };

    (async () => {
      try {
        unlisten = await listen<{ id: string; data: string }>("pty://data", (e) => { if (e.payload.id === id) term.write(dec(e.payload.data)); });
        if (!alive) { unlisten(); return; }
        safeFit();
        await call("pty_open", { id, cmd, cols: term.cols || 80, rows: term.rows || 24 });
        opened = true;
        if (!alive) { call("pty_close", { id }).catch(() => {}); return; }
        safeFit();
      } catch (e) { if (alive) setErr((e as Error).message); }
    })();

    const sub = term.onData((d) => { if (opened) call("pty_write", { id, data: d }).catch(() => {}); });
    const ro = new ResizeObserver(safeFit);
    ro.observe(el);
    return () => {
      alive = false;
      ro.disconnect(); sub.dispose(); unlisten?.();
      if (opened) call("pty_close", { id }).catch(() => {});
      term.dispose();
    };
    // cmd cố định theo id
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => { if (visible) setTimeout(() => fitRef.current?.fit(), 30); }, [visible]);

  return (
    <div className="h-full bg-[#0d0f14] rounded-md overflow-hidden relative">
      {err && <div className="absolute inset-x-0 top-0 z-10 bg-bad/20 text-bad text-sm p-2">{err}</div>}
      <div ref={host} className="h-full" />
    </div>
  );
}
