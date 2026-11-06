import { ComponentType, lazy, Suspense, useEffect } from "react";
import { cx } from "../../components/ui";
import { useLocalState } from "../../lib/hooks";
import { TOOL_ITEMS } from "./registry";

const COMPONENTS: Record<string, ComponentType> = {
  json: lazy(() => import("./tools/json")),
  jwt: lazy(() => import("./tools/jwt")),
  base64: lazy(() => import("./tools/base64")),
  hash: lazy(() => import("./tools/hash")),
  regex: lazy(() => import("./tools/regex")),
  uuid: lazy(() => import("./tools/uuid")),
  cron: lazy(() => import("./tools/cron")),
  url: lazy(() => import("./tools/url")),
  time: lazy(() => import("./tools/time")),
};

export default function DevDock({ tool, onToolChange }: { tool: string | null; onToolChange: (t: string | null) => void }) {
  const [saved, setSaved] = useLocalState<string>("devdock.tool", "json");
  const current = tool && COMPONENTS[tool] ? tool : saved;
  useEffect(() => { if (tool && COMPONENTS[tool]) { setSaved(tool); onToolChange(null); } }, [tool, setSaved, onToolChange]);
  const Active = COMPONENTS[current] ?? COMPONENTS.json;

  return (
    <div className="h-full flex">
      <div className="w-56 shrink-0 border-r border-line p-2 space-y-0.5 overflow-auto">
        <div className="px-2 py-2 text-xs uppercase tracking-wider text-muted">Công cụ</div>
        {TOOL_ITEMS.map((t) => (
          <button key={t.id} onClick={() => setSaved(t.id)} className={cx("w-full text-left px-3 py-2 rounded-md text-sm cursor-pointer transition", current === t.id ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel2 hover:text-fg")}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-w-0 p-5">
        <Suspense fallback={<div className="text-muted">Đang tải…</div>}>
          <Active key={current} />
        </Suspense>
      </div>
    </div>
  );
}
