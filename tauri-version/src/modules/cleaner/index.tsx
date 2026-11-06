import { listen } from "@tauri-apps/api/event";
import { save } from "@tauri-apps/plugin-dialog";
import { Download, EyeOff, FolderOpen, Loader2, RefreshCw, ScanSearch, Search, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Bar, Button, Card, Confirm, Empty, ErrorBox, Input, Tabs, useToast, cx } from "../../components/ui";
import { call, CGroup, CItem, CleanReport, CleanSettings, inTauri, ScanResult } from "../../lib/api";
import { ago, bytes } from "../../lib/format";
import { TreemapTab } from "./Treemap";
import { SettingsTab } from "./Settings";

type Tab = "clean" | "tree" | "settings";
type Phase = "idle" | "scanning" | "cleaning";

export const GROUPS: Record<CGroup, { label: string; color: string }> = {
  project: { label: "Build / cài đặt trong project", color: "#6d7cff" },
  cache: { label: "Cache", color: "#34d399" },
  docker: { label: "Docker", color: "#38bdf8" },
  large: { label: "Tệp lớn", color: "#fbbf24" },
  dupe: { label: "Tệp trùng lặp", color: "#f472b6" },
  system: { label: "Hệ thống (cần root)", color: "#a78bfa" },
};
const ORDER: CGroup[] = ["project", "cache", "docker", "large", "dupe", "system"];

export default function DiskCleaner({ active }: { active: boolean }) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("clean");
  const [res, setRes] = useState<ScanResult | null>(null);
  const [settings, setSettings] = useState<CleanSettings | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [msg, setMsg] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [level, setLevel] = useState<"all" | "safe" | "review">("all");
  const [group, setGroup] = useState<CGroup | "all">("all");
  const [confirm, setConfirm] = useState(false);
  const [report, setReport] = useState<CleanReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reloadSettings = useCallback(() => call<CleanSettings>("cleaner_settings_get").then(setSettings).catch((e) => setError(e.message)), []);
  useEffect(() => { reloadSettings(); }, [reloadSettings]);
  useEffect(() => {
    if (!inTauri()) return;
    let un: (() => void) | undefined;
    listen<string>("cleaner://progress", (e) => setMsg(e.payload)).then((u) => (un = u));
    return () => un?.();
  }, []);

  const ignored = useMemo(() => new Set(settings?.ignored ?? []), [settings]);
  const items = useMemo(() => (res?.items ?? []).filter((i) => !ignored.has(i.id)), [res, ignored]);
  const visible = useMemo(() => {
    const t = q.trim().toLowerCase();
    return items.filter((i) => (level === "all" || i.level === level) && (group === "all" || i.group === group) && (!t || i.title.toLowerCase().includes(t) || i.note.toLowerCase().includes(t) || i.paths.some((p) => p.toLowerCase().includes(t))));
  }, [items, q, level, group]);
  const chosen = useMemo(() => items.filter((i) => sel.has(i.id)), [items, sel]);
  const chosenBytes = chosen.reduce((a, i) => a + i.size, 0);
  const totalBytes = items.reduce((a, i) => a + i.size, 0);
  const busy = phase !== "idle";

  const scan = async () => {
    setPhase("scanning"); setMsg("Đang bắt đầu quét…"); setError(null); setReport(null);
    try {
      const r = await call<ScanResult | null>("cleaner_scan");
      if (r) {
        setRes(r);
        setSel(new Set(r.items.filter((i) => i.level === "safe" && i.kind !== "root" && !ignored.has(i.id)).map((i) => i.id)));
      } else toast("Đã huỷ quét");
    } catch (e) { setError((e as Error).message); }
    setPhase("idle");
  };

  const clean = async () => {
    setConfirm(false); setPhase("cleaning"); setError(null);
    try {
      const rep = await call<CleanReport>("cleaner_clean", { ids: chosen.map((i) => i.id) });
      setReport(rep);
      const gone = new Set(rep.done);
      setRes((r) => r && { ...r, items: r.items.filter((i) => !gone.has(i.id)) });
      setSel((s) => new Set([...s].filter((id) => !gone.has(id))));
      call<{ total: number; free: number }>("cleaner_disk").then((d) => setRes((r) => r && { ...r, disk: d }));
      reloadSettings();
      toast(rep.freed ? `Đã giải phóng ${bytes(rep.freed)}` : `Đã xử lý ${rep.done.length} mục`);
    } catch (e) { setError((e as Error).message); }
    setPhase("idle");
  };

  const exportCsv = async () => {
    try {
      const path = await save({ defaultPath: "disk-cleaner.csv", filters: [{ name: "CSV", extensions: ["csv"] }] });
      if (!path) return;
      await call("cleaner_export_csv", { path, ids: visible.map((i) => i.id) });
      toast(`Đã xuất ${visible.length} mục`);
    } catch (e) { toast((e as Error).message, "err"); }
  };

  const ignore = async (i: CItem) => {
    try {
      setSettings(await call<CleanSettings>("cleaner_ignore", { id: i.id, title: i.title, ignore: true }));
      setSel((s) => { const n = new Set(s); n.delete(i.id); return n; });
    } catch (e) { toast((e as Error).message, "err"); }
  };

  const toggle = (ids: string[], on: boolean) => setSel((s) => { const n = new Set(s); ids.forEach((id) => (on ? n.add(id) : n.delete(id))); return n; });
  const used = res ? res.disk.total - res.disk.free : 0;
  const hasTrash = chosen.some((i) => (i.group === "large" || i.group === "dupe")) && settings?.trash_large;

  return (
    <div className="h-full flex flex-col">
      <div className="px-5 pt-4">
        <div className="flex items-baseline gap-3 mb-2">
          <h1 className="text-lg font-semibold">DiskCleaner</h1>
          {res && <span className="text-xs text-muted">Quét {ago(res.scanned_at)} · ổ / còn trống {bytes(res.disk.free)} / {bytes(res.disk.total)}</span>}
        </div>
        <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ id: "clean", label: "Dọn dẹp", count: items.length || undefined }, { id: "tree", label: "Dung lượng thư mục" }, { id: "settings", label: "Cài đặt" }]} />
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        {tab === "clean" && (
          <div className="p-5 space-y-4">
            <Card className="p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                {res && (
                  <div className="flex-1 min-w-56">
                    <div className="flex justify-between text-xs text-muted mb-1"><span>Đã dùng {bytes(used)}</span><span>{((used / res.disk.total) * 100).toFixed(0)}%</span></div>
                    <Bar value={(used / res.disk.total) * 100} />
                  </div>
                )}
                <div className="flex gap-2 ml-auto">
                  {phase === "scanning" ? (
                    <Button variant="danger" onClick={() => call("cleaner_cancel")}><X size={14} />Huỷ quét</Button>
                  ) : (
                    <Button variant={res ? "soft" : "primary"} onClick={scan} disabled={busy}>{res ? <RefreshCw size={14} /> : <ScanSearch size={14} />}{res ? "Quét lại" : "Quét máy"}</Button>
                  )}
                  <Button variant="danger" onClick={() => setConfirm(true)} disabled={busy || !chosen.length}><Sparkles size={14} />Dọn {chosen.length ? `${chosen.length} mục · ${bytes(chosenBytes)}` : ""}</Button>
                </div>
              </div>
              {busy && <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /><span className="truncate">{msg}</span></div>}
              {res && !busy && <div className="text-sm"><b>{bytes(totalBytes)}</b> <span className="text-muted">có thể dọn · đã chọn</span> <b>{bytes(chosenBytes)}</b></div>}
              <div className="text-xs text-muted">Quét chỉ đọc, không xoá gì. Xoá chỉ xảy ra sau khi bạn xác nhận.</div>
            </Card>

            {error && <ErrorBox>{error}</ErrorBox>}
            {report && (
              <Card className="p-3 text-sm space-y-1">
                <div>Đã dọn <b>{report.done.length}</b> mục{report.freed > 0 && <> · giải phóng <b>{bytes(report.freed)}</b></>}.</div>
                {report.trashed > 0 && <div className="text-warn">{bytes(report.trashed)} tệp lớn đã vào thùng rác — dọn thùng rác để lấy lại dung lượng.</div>}
                {report.errors.map((e, i) => <div key={i} className="text-bad break-words">✗ {e}</div>)}
              </Card>
            )}

            {!res && !busy && <Empty icon={<ScanSearch size={32} />} title="Chưa quét" hint="Bấm “Quét máy” để tìm thư mục build, cache, Docker, tệp lớn và tệp trùng lặp có thể dọn." />}

            {res && (
              <>
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => setGroup("all")} className={cx("px-2.5 py-1 rounded-full text-xs border cursor-pointer", group === "all" ? "border-accent text-accent bg-accent/10" : "border-line text-muted hover:text-fg")}>Tất cả · {items.length}</button>
                  {ORDER.map((g) => {
                    const gi = items.filter((i) => i.group === g);
                    if (!gi.length) return null;
                    return (
                      <button key={g} onClick={() => setGroup(group === g ? "all" : g)} className={cx("px-2.5 py-1 rounded-full text-xs border cursor-pointer flex items-center gap-1.5", group === g ? "border-accent bg-accent/10" : "border-line text-muted hover:text-fg")}>
                        <span className="w-2 h-2 rounded-full" style={{ background: GROUPS[g].color }} />{GROUPS[g].label} · {bytes(gi.reduce((a, i) => a + i.size, 0))}
                      </button>
                    );
                  })}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative w-64"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm mục…" className="!pl-8" /></div>
                  {(["all", "safe", "review"] as const).map((l) => <Button key={l} size="sm" variant={level === l ? "primary" : "soft"} onClick={() => setLevel(l)}>{l === "all" ? "Mọi mức" : l === "safe" ? "An toàn" : "Xem lại"}</Button>)}
                  <Button size="sm" onClick={() => setSel(new Set(items.filter((i) => i.level === "safe" && i.kind !== "root").map((i) => i.id)))}>Chọn mục an toàn</Button>
                  <Button size="sm" variant="ghost" onClick={() => setSel(new Set())}>Bỏ chọn</Button>
                  <Button size="sm" variant="ghost" className="ml-auto" onClick={exportCsv} disabled={!visible.length}><Download size={13} />Xuất CSV</Button>
                </div>

                {ORDER.map((g) => {
                  const rows = visible.filter((i) => i.group === g);
                  if (!rows.length) return null;
                  const all = rows.every((r) => sel.has(r.id));
                  return (
                    <Card key={g} className="overflow-hidden">
                      <div className="flex items-center gap-3 px-3 py-2 bg-panel2 border-b border-line">
                        <input type="checkbox" checked={all} onChange={(e) => toggle(rows.map((r) => r.id), e.target.checked)} />
                        <span className="w-2.5 h-2.5 rounded-full" style={{ background: GROUPS[g].color }} />
                        <span className="font-medium text-sm">{GROUPS[g].label}</span>
                        <span className="text-xs text-muted">{rows.length} mục · {bytes(rows.reduce((a, r) => a + r.size, 0))}</span>
                      </div>
                      {rows.map((i) => (
                        <div key={i.id} className="group flex items-start gap-3 px-3 py-2.5 border-b border-line/50 last:border-0 hover:bg-panel2/60">
                          <input type="checkbox" className="mt-1" checked={sel.has(i.id)} onChange={(e) => toggle([i.id], e.target.checked)} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2"><span className="font-medium text-sm truncate" title={i.paths.join("\n") || i.cmd || ""}>{i.title}</span><Badge tone={i.level === "safe" ? "ok" : "warn"}>{i.level === "safe" ? "An toàn" : "Xem lại"}</Badge></div>
                            <div className="text-xs text-muted mt-0.5">{i.note}</div>
                            {i.rebuild && <div className="text-xs text-muted">Tạo lại: <code className="font-mono">{i.rebuild}</code></div>}
                            {i.cmd && <div className="text-xs text-muted">Lệnh: <code className="font-mono">{i.cmd}</code></div>}
                          </div>
                          <div className="text-sm tabular-nums font-medium w-20 text-right">{bytes(i.size)}</div>
                          <div className="flex opacity-0 group-hover:opacity-100">
                            {i.paths.length > 0 && <button title="Mở trong trình quản lý tệp" onClick={() => call("cleaner_open", { path: i.paths[0] }).catch((e) => toast(e.message, "err"))} className="p-1.5 rounded hover:bg-line text-muted hover:text-fg cursor-pointer"><FolderOpen size={14} /></button>}
                            <button title="Ẩn mục này (không hiện ở lần quét sau)" onClick={() => ignore(i)} className="p-1.5 rounded hover:bg-line text-muted hover:text-fg cursor-pointer"><EyeOff size={14} /></button>
                          </div>
                        </div>
                      ))}
                    </Card>
                  );
                })}
                {!visible.length && <Empty title={items.length ? "Không có mục khớp bộ lọc" : "Máy sạch rồi 🎉"} />}
              </>
            )}
          </div>
        )}
        {tab === "tree" && <TreemapTab active={active} />}
        {tab === "settings" && settings && <SettingsTab settings={settings} onChange={setSettings} />}
      </div>

      <Confirm
        open={confirm}
        title="Xác nhận dọn"
        body={
          <div className="space-y-2">
            <div>Dọn <b>{chosen.length}</b> mục, giải phóng khoảng <b>{bytes(chosenBytes)}</b>.</div>
            <div>{hasTrash ? "Tệp lớn và tệp trùng sẽ được chuyển vào thùng rác; các mục còn lại bị xoá vĩnh viễn." : "Các mục này bị xoá vĩnh viễn (không vào thùng rác)."}{chosen.some((i) => i.kind === "root") && " Mục hệ thống sẽ hiện hộp thoại nhập mật khẩu."}</div>
            <div className="max-h-48 overflow-auto text-xs font-mono bg-panel2 rounded p-2 space-y-0.5">
              {chosen.slice(0, 50).map((i) => <div key={i.id} className="truncate">• {i.title} ({bytes(i.size)})</div>)}
              {chosen.length > 50 && <div>…và {chosen.length - 50} mục khác</div>}
            </div>
          </div>
        }
        onNo={() => setConfirm(false)}
        onYes={clean}
      />
    </div>
  );
}

