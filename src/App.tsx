import { Code2, Command, Container, Eraser, Gauge, Moon, Sun, Wrench } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Palette, PaletteItem } from "./components/Palette";
import { cx, ToastProvider } from "./components/ui";
import { useLocalState } from "./lib/hooks";
import logo from "./logo.svg";

const DevDock = lazy(() => import("./modules/devdock"));
const SysMonitor = lazy(() => import("./modules/sysmon"));
const CodeVault = lazy(() => import("./modules/vault"));
const DockerDesk = lazy(() => import("./modules/docker"));
const DiskCleaner = lazy(() => import("./modules/cleaner"));

export const MODULES = [
  { id: "devdock", label: "DevDock Tools", icon: Wrench, key: "1" },
  { id: "sysmon", label: "SysMonitor", icon: Gauge, key: "2" },
  { id: "vault", label: "CodeVault", icon: Code2, key: "3" },
  { id: "docker", label: "DockerDesk", icon: Container, key: "4" },
  { id: "cleaner", label: "DiskCleaner", icon: Eraser, key: "5" },
] as const;
type ModId = (typeof MODULES)[number]["id"];

export default function App() {
  const [mod, setMod] = useLocalState<ModId>("module", "devdock");
  const [theme, setTheme] = useLocalState<"dark" | "light">("theme", "dark");
  const [pal, setPal] = useState(false);
  const [visited, setVisited] = useState<Set<ModId>>(new Set([mod]));
  const [tool, setTool] = useState<string | null>(null);

  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => { setVisited((v) => (v.has(mod) ? v : new Set(v).add(mod))); }, [mod]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPal((p) => !p); }
      else if ((e.ctrlKey || e.metaKey) && /^[1-5]$/.test(e.key)) { e.preventDefault(); setMod(MODULES[+e.key - 1].id); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [setMod]);

  const items = useMemo<PaletteItem[]>(
    () => [
      ...MODULES.map((m) => ({ id: m.id, label: `Mở ${m.label}`, hint: `Ctrl+${m.key}`, run: () => setMod(m.id) })),
      ...TOOL_ITEMS.map((t) => ({ id: "tool:" + t.id, label: `Tool: ${t.label}`, hint: "DevDock", run: () => { setMod("devdock"); setTool(t.id); } })),
      { id: "theme", label: theme === "dark" ? "Chuyển sang giao diện sáng" : "Chuyển sang giao diện tối", run: () => setTheme(theme === "dark" ? "light" : "dark") },
    ],
    [theme, setMod, setTheme],
  );

  return (
    <ToastProvider>
      <div className="h-full flex">
        <aside className="w-16 lg:w-52 shrink-0 bg-panel border-r border-line flex flex-col">
          <div className="h-14 flex items-center gap-2 px-4 font-semibold tracking-tight">
            <img src={logo} alt="DevDock" className="w-7 h-7 shrink-0" />
            <span className="hidden lg:inline">DevDock</span>
          </div>
          <nav className="flex-1 px-2 space-y-1">
            {MODULES.map((m) => (
              <button
                key={m.id}
                onClick={() => setMod(m.id)}
                title={m.label}
                className={cx("w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm cursor-pointer transition", mod === m.id ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel2 hover:text-fg")}
              >
                <m.icon size={18} className="shrink-0" />
                <span className="hidden lg:inline flex-1 text-left">{m.label}</span>
                <kbd className="hidden lg:inline text-[10px] opacity-50">^{m.key}</kbd>
              </button>
            ))}
          </nav>
          <div className="p-2 space-y-1 border-t border-line">
            <button onClick={() => setPal(true)} className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted hover:bg-panel2 hover:text-fg cursor-pointer" title="Command palette (Ctrl+K)">
              <Command size={18} className="shrink-0" />
              <span className="hidden lg:inline flex-1 text-left">Lệnh nhanh</span>
              <kbd className="hidden lg:inline text-[10px] opacity-50">^K</kbd>
            </button>
            <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted hover:bg-panel2 hover:text-fg cursor-pointer">
              {theme === "dark" ? <Sun size={18} className="shrink-0" /> : <Moon size={18} className="shrink-0" />}
              <span className="hidden lg:inline">{theme === "dark" ? "Sáng" : "Tối"}</span>
            </button>
          </div>
        </aside>
        <main className="flex-1 min-w-0 relative">
          <Suspense fallback={<div className="p-6 text-muted">Đang tải…</div>}>
            {/* Giữ module đã mở ở trạng thái mounted để không mất state (vd. terminal) */}
            {visited.has("devdock") && <Pane on={mod === "devdock"}><DevDock tool={tool} onToolChange={setTool} /></Pane>}
            {visited.has("sysmon") && <Pane on={mod === "sysmon"}><SysMonitor active={mod === "sysmon"} /></Pane>}
            {visited.has("vault") && <Pane on={mod === "vault"}><CodeVault /></Pane>}
            {visited.has("docker") && <Pane on={mod === "docker"}><DockerDesk active={mod === "docker"} /></Pane>}
            {visited.has("cleaner") && <Pane on={mod === "cleaner"}><DiskCleaner active={mod === "cleaner"} /></Pane>}
          </Suspense>
        </main>
      </div>
      <Palette open={pal} items={items} onClose={() => setPal(false)} />
    </ToastProvider>
  );
}

const Pane = ({ on, children }: { on: boolean; children: React.ReactNode }) => (
  <div className={cx("absolute inset-0", !on && "hidden")}>{children}</div>
);

import { TOOL_ITEMS } from "./modules/devdock/registry";
