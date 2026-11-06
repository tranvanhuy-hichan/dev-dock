import { open } from "@tauri-apps/plugin-dialog";
import { Download, FileCode2, FolderPlus, Play, RotateCw, ScrollText, Search, Square, SquareTerminal, Trash2, X } from "lucide-react";
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, Confirm, Empty, ErrorBox, Input, Tabs, useToast, cx } from "../../components/ui";
import { call, ContainerInfo, DockerStatus, ImageInfo } from "../../lib/api";
import { ago, bytes } from "../../lib/format";
import { usePoll } from "../../lib/hooks";
import TerminalView from "./Terminal";

type Tab = "containers" | "images" | "compose" | "terminal";
interface Term { id: string; title: string; cmd: string[] }
let termSeq = 0;
const SHELL_IN = ["sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh"];

export default function DockerDesk({ active }: { active: boolean }) {
  const [tab, setTab] = useState<Tab>("containers");
  const [terms, setTerms] = useState<Term[]>([]);
  const [curTerm, setCurTerm] = useState<string | null>(null);
  const status = usePoll(() => call<DockerStatus>("docker_status"), 5000, active);
  const ok = status.data?.available ?? false;
  const info = status.data?.info;

  const openTerm = (title: string, cmd: string[]) => {
    const id = `t${++termSeq}`;
    setTerms((t) => [...t, { id, title, cmd }]);
    setCurTerm(id); setTab("terminal");
  };
  const closeTerm = (id: string) => {
    setTerms((t) => { const n = t.filter((x) => x.id !== id); setCurTerm((c) => (c === id ? n.at(-1)?.id ?? null : c)); return n; });
  };

  return (
    <div className="h-full flex flex-col">
      <div className="px-5 pt-4">
        <div className="flex items-baseline gap-3 mb-2">
          <h1 className="text-lg font-semibold">DockerDesk</h1>
          {info && <span className="text-xs text-muted">Docker {info.version} · {info.running}/{info.containers} container chạy · {info.images} image · {info.cpus} CPU · {bytes(info.mem_total)} RAM</span>}
          {status.data && <Badge tone={ok ? "ok" : "bad"}>{ok ? "Kết nối" : "Ngắt kết nối"}</Badge>}
        </div>
        <Tabs<Tab> value={tab} onChange={setTab} tabs={[
          { id: "containers", label: "Containers" }, { id: "images", label: "Images" }, { id: "compose", label: "Compose" },
          { id: "terminal", label: "Terminal", icon: <SquareTerminal size={14} />, count: terms.length || undefined },
        ]} />
      </div>
      <div className="flex-1 min-h-0 relative">
        {tab !== "terminal" && !ok && status.data && (
          <div className="p-5"><Empty title="Không kết nối được Docker" hint={<>{status.data.error}<br />Hãy chắc chắn Docker đang chạy và user thuộc nhóm <code>docker</code> (<code>sudo usermod -aG docker $USER</code>).</>} /></div>
        )}
        {ok && tab === "containers" && <Containers active={active} onTerm={openTerm} />}
        {ok && tab === "images" && <Images active={active} />}
        {tab === "compose" && <Compose />}
        <div className={cx("absolute inset-0 flex flex-col p-5 gap-2", tab !== "terminal" && "hidden")}>
          <div className="flex items-center gap-1 flex-wrap">
            {terms.map((t) => (
              <div key={t.id} className={cx("flex items-center gap-1 pl-3 pr-1 py-1 rounded-md text-sm cursor-pointer border", curTerm === t.id ? "bg-accent/15 border-accent/40" : "border-line hover:bg-panel2")} onClick={() => setCurTerm(t.id)}>
                {t.title}<button onClick={(e) => { e.stopPropagation(); closeTerm(t.id); }} className="p-0.5 rounded hover:bg-line cursor-pointer"><X size={13} /></button>
              </div>
            ))}
            <Button size="sm" onClick={() => openTerm("Shell", [])}>+ Terminal mới</Button>
          </div>
          <div className="flex-1 min-h-0 relative">
            {terms.length === 0 && <Empty icon={<SquareTerminal size={28} />} title="Chưa có terminal" hint="Mở shell máy hoặc chọn “Shell” ở một container để exec vào trong." />}
            {terms.map((t) => (
              <div key={t.id} className={cx("absolute inset-0", curTerm !== t.id && "invisible")}>
                <TerminalView id={t.id} cmd={t.cmd} visible={active && tab === "terminal" && curTerm === t.id} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const stateTone = (s: string) => (s === "running" ? "ok" : s === "paused" || s === "restarting" ? "warn" : s === "exited" || s === "dead" ? "bad" : "muted") as "ok" | "warn" | "bad" | "muted";

function Containers({ active, onTerm }: { active: boolean; onTerm: (title: string, cmd: string[]) => void }) {
  const toast = useToast();
  const [all, setAll] = useState(true);
  const [q, setQ] = useState("");
  const [logs, setLogs] = useState<ContainerInfo | null>(null);
  const [rm, setRm] = useState<ContainerInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { data, error, refresh } = usePoll(() => call<ContainerInfo[]>("docker_containers", { all }), 3000, active, [all]);

  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data ?? []).filter((c) => !t || c.name.toLowerCase().includes(t) || c.image.toLowerCase().includes(t) || (c.project ?? "").toLowerCase().includes(t));
  }, [data, q]);

  const act = async (c: ContainerInfo, action: string) => {
    setBusy(c.id);
    try { await call("docker_action", { id: c.id, action }); refresh(); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(null);
  };

  return (
    <div className="h-full flex">
      <div className="flex-1 min-w-0 overflow-auto p-5 space-y-3">
        <div className="flex items-center gap-3">
          <div className="relative max-w-sm flex-1"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm container, image, project…" className="!pl-8" /></div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />Hiện cả container đã dừng</label>
          <Button variant="ghost" onClick={refresh}><RotateCw size={14} /></Button>
        </div>
        {error && <ErrorBox>{error}</ErrorBox>}
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-muted border-b border-line"><th className="px-3 py-2 font-medium">Tên</th><th className="px-3 py-2 font-medium">Image</th><th className="px-3 py-2 font-medium">Trạng thái</th><th className="px-3 py-2 font-medium">Cổng</th><th /></tr></thead>
            <tbody>
              {rows.map((c) => {
                const running = c.state === "running";
                return (
                  <tr key={c.id} className={cx("border-b border-line/50 hover:bg-panel2", logs?.id === c.id && "bg-accent/5")}>
                    <td className="px-3 py-2"><div className="font-medium">{c.name}</div><div className="text-xs text-muted font-mono">{c.id.slice(0, 12)}{c.project && <> · <span className="text-accent">{c.project}</span></>}</div></td>
                    <td className="px-3 py-2 max-w-48 truncate text-muted" title={c.image}>{c.image}</td>
                    <td className="px-3 py-2"><Badge tone={stateTone(c.state)}>{c.state}</Badge><div className="text-xs text-muted mt-0.5">{c.status}</div></td>
                    <td className="px-3 py-2 text-xs font-mono text-muted max-w-48">{c.ports.slice(0, 3).join(", ")}{c.ports.length > 3 && ` +${c.ports.length - 3}`}</td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-0.5">
                        {running ? <IconBtn title="Dừng" onClick={() => act(c, "stop")} disabled={busy === c.id}><Square size={14} /></IconBtn> : <IconBtn title="Khởi động" onClick={() => act(c, "start")} disabled={busy === c.id}><Play size={14} /></IconBtn>}
                        <IconBtn title="Restart" onClick={() => act(c, "restart")} disabled={busy === c.id}><RotateCw size={14} /></IconBtn>
                        <IconBtn title="Logs" onClick={() => setLogs(c)}><ScrollText size={14} /></IconBtn>
                        {running && <IconBtn title="Shell trong container" onClick={() => onTerm(c.name, ["docker", "exec", "-it", c.id, ...SHELL_IN])}><SquareTerminal size={14} /></IconBtn>}
                        <IconBtn title="Xoá" onClick={() => setRm(c)} danger><Trash2 size={14} /></IconBtn>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length && <Empty title="Không có container" />}
        </Card>
      </div>
      {logs && <LogPanel c={logs} onClose={() => setLogs(null)} />}
      <Confirm open={!!rm} title={`Xoá container ${rm?.name}?`} body="Container sẽ bị buộc dừng và xoá. Dữ liệu trong volume ẩn danh có thể mất." onNo={() => setRm(null)} onYes={() => { const c = rm!; setRm(null); if (logs?.id === c.id) setLogs(null); act(c, "remove"); }} />
    </div>
  );
}

function IconBtn({ children, title, onClick, disabled, danger }: { children: ReactNode; title: string; onClick: () => void; disabled?: boolean; danger?: boolean }) {
  return <button title={title} onClick={onClick} disabled={disabled} className={cx("p-1.5 rounded-md cursor-pointer disabled:opacity-30 hover:bg-line", danger ? "text-bad" : "text-muted hover:text-fg")}>{children}</button>;
}

function LogPanel({ c, onClose }: { c: ContainerInfo; onClose: () => void }) {
  const [follow, setFollow] = useState(true);
  const [tail, setTail] = useState(300);
  const { data, error } = usePoll(() => call<string>("docker_logs", { id: c.id, tail }), 2000, true, [c.id, tail]);
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [data, follow]);
  return (
    <div className="w-[46%] min-w-80 border-l border-line flex flex-col">
      <div className="flex items-center gap-2 p-2 border-b border-line">
        <span className="font-medium text-sm truncate flex-1">Logs · {c.name}</span>
        <select value={tail} onChange={(e) => setTail(+e.target.value)} className="bg-panel2 border border-line rounded text-xs px-1 py-0.5">{[100, 300, 1000, 5000].map((n) => <option key={n} value={n}>{n} dòng</option>)}</select>
        <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />Follow</label>
        <button onClick={onClose} className="p-1 rounded hover:bg-line cursor-pointer"><X size={15} /></button>
      </div>
      {error && <ErrorBox>{error}</ErrorBox>}
      <pre ref={box} className="flex-1 overflow-auto p-3 text-xs font-mono whitespace-pre-wrap break-all">{data ?? "Đang tải…"}{data === "" && <span className="text-muted">(chưa có log)</span>}</pre>
    </div>
  );
}

function Images({ active }: { active: boolean }) {
  const toast = useToast();
  const { data, error, refresh } = usePoll(() => call<ImageInfo[]>("docker_images"), 5000, active);
  const [pull, setPull] = useState("");
  const [pulling, setPulling] = useState(false);
  const [rm, setRm] = useState<ImageInfo | null>(null);
  const doPull = async () => {
    if (!pull.trim()) return;
    setPulling(true);
    try { const r = await call<string>("docker_pull", { image: pull.trim() }); toast(r || "Đã pull"); setPull(""); refresh(); }
    catch (e) { toast((e as Error).message, "err"); }
    setPulling(false);
  };
  return (
    <div className="h-full overflow-auto p-5 space-y-3">
      <div className="flex gap-2 max-w-xl">
        <Input value={pull} onChange={(e) => setPull(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doPull()} placeholder="Pull image, vd. nginx:alpine" className="font-mono" />
        <Button variant="primary" onClick={doPull} disabled={pulling || !pull.trim()}><Download size={14} />{pulling ? "Đang pull…" : "Pull"}</Button>
      </div>
      {error && <ErrorBox>{error}</ErrorBox>}
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted border-b border-line"><th className="px-3 py-2 font-medium">Image</th><th className="px-3 py-2 font-medium">ID</th><th className="px-3 py-2 font-medium">Kích thước</th><th className="px-3 py-2 font-medium">Tạo</th><th className="px-3 py-2 font-medium">Dùng bởi</th><th /></tr></thead>
          <tbody>
            {(data ?? []).map((i) => (
              <tr key={i.id} className="border-b border-line/50 hover:bg-panel2">
                <td className="px-3 py-2">{i.tags.length ? i.tags.map((t) => <div key={t} className="font-medium">{t}</div>) : <span className="text-muted">&lt;none&gt;</span>}</td>
                <td className="px-3 py-2 font-mono text-xs text-muted">{i.id.replace("sha256:", "").slice(0, 12)}</td>
                <td className="px-3 py-2 tabular-nums">{bytes(i.size)}</td>
                <td className="px-3 py-2 text-muted">{ago(i.created)}</td>
                <td className="px-3 py-2">{i.containers > 0 ? <Badge tone="ok">{i.containers} container</Badge> : <Badge>không dùng</Badge>}</td>
                <td className="px-3 py-2 text-right"><IconBtn title="Xoá image" danger onClick={() => setRm(i)}><Trash2 size={14} /></IconBtn></td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && !data.length && <Empty title="Chưa có image" />}
      </Card>
      <Confirm open={!!rm} title="Xoá image?" body={rm?.tags.join(", ") || rm?.id.slice(7, 19)} onNo={() => setRm(null)} onYes={async () => {
        const i = rm!; setRm(null);
        try { await call("docker_remove_image", { id: i.id, force: false }); refresh(); }
        catch (e) { toast((e as Error).message, "err"); }
      }} />
    </div>
  );
}

function Compose() {
  const toast = useToast();
  const [files, setFiles] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [out, setOut] = useState<{ title: string; text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState("");

  useEffect(() => { call<string[]>("compose_list").then((l) => { setFiles(l); setSel((s) => s ?? l[0] ?? null); }).catch((e) => toast(e.message, "err")); }, [toast]);

  const add = async (path: string) => {
    try { const l = await call<string[]>("compose_add", { path }); setFiles(l); setSel(path); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  const pick = async () => {
    try {
      const p = await open({ multiple: false, filters: [{ name: "Compose", extensions: ["yml", "yaml"] }] });
      if (typeof p === "string") add(p);
    } catch { toast("Không mở được hộp thoại chọn file — hãy nhập đường dẫn thủ công", "err"); }
  };
  const run = async (action: string) => {
    if (!sel) return;
    setBusy(true); setOut({ title: `docker compose ${action}`, text: "Đang chạy…", ok: true });
    try { const text = await call<string>("compose_run", { file: sel, action }); setOut({ title: `docker compose ${action}`, text: text || "(không có output)", ok: true }); }
    catch (e) { setOut({ title: `docker compose ${action}`, text: (e as Error).message, ok: false }); }
    setBusy(false);
  };

  return (
    <div className="h-full flex">
      <div className="w-72 shrink-0 border-r border-line p-3 space-y-2 overflow-auto">
        <Button className="w-full" onClick={pick}><FolderPlus size={14} />Thêm file compose</Button>
        <div className="flex gap-1"><Input value={manual} onChange={(e) => setManual(e.target.value)} placeholder="/đường/dẫn/docker-compose.yml" className="!text-xs" /><Button onClick={() => { if (manual.trim()) { add(manual.trim()); setManual(""); } }}>+</Button></div>
        {files.map((f) => (
          <div key={f} onClick={() => setSel(f)} className={cx("group flex items-center gap-2 p-2 rounded-md cursor-pointer border", sel === f ? "bg-accent/10 border-accent/40" : "border-transparent hover:bg-panel2")}>
            <FileCode2 size={16} className="text-muted shrink-0" />
            <div className="min-w-0 flex-1"><div className="text-sm font-medium truncate">{f.split(/[\\/]/).slice(-2, -1)[0] ?? f}</div><div className="text-[11px] text-muted truncate" title={f}>{f}</div></div>
            <button onClick={(e) => { e.stopPropagation(); call<string[]>("compose_remove", { path: f }).then((l) => { setFiles(l); if (sel === f) setSel(l[0] ?? null); }); }} className="opacity-0 group-hover:opacity-100 text-muted hover:text-bad cursor-pointer"><X size={14} /></button>
          </div>
        ))}
        {!files.length && <div className="text-sm text-muted px-1">Chưa có project nào. Thêm file <code>docker-compose.yml</code> để quản lý.</div>}
      </div>
      <div className="flex-1 min-w-0 p-4 flex flex-col gap-3">
        {sel ? (
          <>
            <div className="flex flex-wrap gap-2 items-center">
              <Button variant="primary" onClick={() => run("up")} disabled={busy}><Play size={14} />Up -d</Button>
              <Button onClick={() => run("stop")} disabled={busy}><Square size={14} />Stop</Button>
              <Button onClick={() => run("restart")} disabled={busy}><RotateCw size={14} />Restart</Button>
              <Button variant="danger" onClick={() => run("down")} disabled={busy}>Down</Button>
              <Button onClick={() => run("pull")} disabled={busy}><Download size={14} />Pull</Button>
              <Button variant="ghost" onClick={() => run("ps")} disabled={busy}>ps</Button>
              <Button variant="ghost" onClick={() => run("logs")} disabled={busy}>logs</Button>
            </div>
            <Card className="flex-1 min-h-0 overflow-auto p-3">
              {out ? (<><div className={cx("text-xs mb-2 font-mono", out.ok ? "text-muted" : "text-bad")}>$ {out.title}</div><pre className="text-xs font-mono whitespace-pre-wrap break-all">{out.text}</pre></>) : <div className="text-sm text-muted">Chọn một hành động để chạy trên <code>{sel}</code>.</div>}
            </Card>
          </>
        ) : <Empty title="Chọn hoặc thêm một compose project" />}
      </div>
    </div>
  );
}
