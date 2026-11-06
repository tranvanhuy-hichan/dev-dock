import { ArrowDown, ArrowUp, Cpu, HardDrive, MemoryStick, Network, Search, X } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { Sparkline } from "../../components/Sparkline";
import { Bar, Badge, Button, Card, Confirm, Empty, ErrorBox, Input, Tabs, useToast } from "../../components/ui";
import { call, ContainerStats, DockerStatus, PortInfo, ProcInfo, Snapshot } from "../../lib/api";
import { bytes, duration, pct, rate } from "../../lib/format";
import { usePoll } from "../../lib/hooks";

type Tab = "overview" | "procs" | "ports" | "disks" | "net" | "docker";
const HIST = 60;

function useHistory(v: number | undefined) {
  const [h, setH] = useState<number[]>([]);
  useEffect(() => { if (v !== undefined) setH((x) => [...x.slice(-(HIST - 1)), v]); }, [v]);
  return h;
}

export default function SysMonitor({ active }: { active: boolean }) {
  const [tab, setTab] = useState<Tab>("overview");
  const snap = usePoll(() => call<Snapshot>("sys_snapshot"), 1000, active);
  const s = snap.data;
  const cpuH = useHistory(s?.cpu_total);
  const memH = useHistory(s ? (s.mem_used / s.mem_total) * 100 : undefined);
  const rxH = useHistory(s?.net_rx_rate);
  const txH = useHistory(s?.net_tx_rate);
  const gpuH = useHistory(s?.gpus[0]?.util);

  return (
    <div className="h-full flex flex-col">
      <div className="px-5 pt-4">
        <div className="flex items-baseline gap-3 mb-2">
          <h1 className="text-lg font-semibold">SysMonitor</h1>
          {s && <span className="text-xs text-muted">{s.host} · {s.os} · uptime {duration(s.uptime)}</span>}
        </div>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: "overview", label: "Tổng quan", icon: <Cpu size={14} /> },
            { id: "procs", label: "Process" },
            { id: "ports", label: "Cổng mạng" },
            { id: "disks", label: "Ổ đĩa", icon: <HardDrive size={14} /> },
            { id: "net", label: "Network", icon: <Network size={14} /> },
            { id: "docker", label: "Docker" },
          ]}
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-5">
        {snap.error && <ErrorBox>{snap.error}</ErrorBox>}
        {s && tab === "overview" && <Overview s={s} cpuH={cpuH} memH={memH} rxH={rxH} txH={txH} gpuH={gpuH} />}
        {s && tab === "disks" && <Disks s={s} />}
        {s && tab === "net" && <Net s={s} rxH={rxH} txH={txH} />}
        {tab === "procs" && <Procs active={active} />}
        {tab === "ports" && <Ports active={active} />}
        {tab === "docker" && <DockerMon active={active} />}
      </div>
    </div>
  );
}

function Metric({ title, icon, value, sub, children }: { title: string; icon: ReactNode; value: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-muted text-xs mb-1">{icon}{title}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted mt-0.5 mb-2 truncate">{sub}</div>}
      {children}
    </Card>
  );
}

function Overview({ s, cpuH, memH, rxH, txH, gpuH }: { s: Snapshot; cpuH: number[]; memH: number[]; rxH: number[]; txH: number[]; gpuH: number[] }) {
  const memPct = (s.mem_used / s.mem_total) * 100;
  const gpu = s.gpus[0];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Metric title="CPU" icon={<Cpu size={14} />} value={pct(s.cpu_total)} sub={`${s.cpu_brand} · ${s.cpu_cores.length} luồng · load ${s.load.map((l) => l.toFixed(2)).join(" ")}`}>
          <Sparkline data={cpuH} max={100} />
        </Metric>
        <Metric title="RAM" icon={<MemoryStick size={14} />} value={pct(memPct)} sub={`${bytes(s.mem_used)} / ${bytes(s.mem_total)}${s.swap_total ? ` · swap ${bytes(s.swap_used)}/${bytes(s.swap_total)}` : ""}`}>
          <Sparkline data={memH} max={100} color="var(--ok)" />
        </Metric>
        <Metric title="Network" icon={<Network size={14} />} value={`↓ ${rate(s.net_rx_rate)}`} sub={`↑ ${rate(s.net_tx_rate)}`}>
          <Sparkline data={rxH} color="var(--ok)" height={28} />
          <Sparkline data={txH} color="var(--warn)" height={28} />
        </Metric>
        {gpu ? (
          <Metric title={`GPU · ${gpu.name}`} icon={<Cpu size={14} />} value={pct(gpu.util, 0)} sub={`VRAM ${bytes(gpu.mem_used)} / ${bytes(gpu.mem_total)} · ${gpu.temp}°C`}>
            <Sparkline data={gpuH} max={100} color="var(--warn)" />
          </Metric>
        ) : (
          <Card className="p-4 text-sm text-muted flex items-center">Không phát hiện GPU NVIDIA (cần <code className="mx-1">nvidia-smi</code>).</Card>
        )}
      </div>
      <Card className="p-4">
        <div className="text-xs text-muted mb-3">Từng nhân CPU</div>
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-x-4 gap-y-3">
          {s.cpu_cores.map((c, i) => (
            <div key={i}><div className="flex justify-between text-xs mb-1"><span className="text-muted">#{i}</span><span className="tabular-nums">{c.toFixed(0)}%</span></div><Bar value={c} /></div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function Disks({ s }: { s: Snapshot }) {
  const rows = [...s.disks].sort((a, b) => a.mount.localeCompare(b.mount));
  if (!rows.length) return <Empty title="Không có ổ đĩa" />;
  return (
    <div className="space-y-3">
      {rows.map((d, i) => {
        const used = d.total - d.available;
        return (
          <Card key={d.mount + i} className="p-4">
            <div className="flex justify-between text-sm mb-2"><span className="font-medium">{d.mount} <span className="text-muted font-normal">{d.name} · {d.fs}</span></span><span className="tabular-nums text-muted">{bytes(used)} / {bytes(d.total)} · trống {bytes(d.available)}</span></div>
            <Bar value={(used / d.total) * 100} />
          </Card>
        );
      })}
    </div>
  );
}

function Net({ s, rxH, txH }: { s: Snapshot; rxH: number[]; txH: number[] }) {
  return (
    <div className="space-y-4">
      <div className="grid md:grid-cols-2 gap-4">
        <Metric title="Download" icon={<ArrowDown size={14} />} value={rate(s.net_rx_rate)}><Sparkline data={rxH} color="var(--ok)" /></Metric>
        <Metric title="Upload" icon={<ArrowUp size={14} />} value={rate(s.net_tx_rate)}><Sparkline data={txH} color="var(--warn)" /></Metric>
      </div>
      <Card className="overflow-hidden">
        <Table head={["Interface", "↓ Rate", "↑ Rate", "Tổng nhận", "Tổng gửi"]} rows={s.net.map((n) => [n.name, rate(n.rx_rate), rate(n.tx_rate), bytes(n.rx_total), bytes(n.tx_total)])} />
      </Card>
    </div>
  );
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-xs text-muted border-b border-line">{head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i} className="border-b border-line/50 last:border-0 hover:bg-panel2">{r.map((c, j) => <td key={j} className="px-3 py-1.5 tabular-nums">{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

type SortKey = "cpu" | "mem" | "pid" | "name";

function Procs({ active }: { active: boolean }) {
  const toast = useToast();
  const { data, error, refresh } = usePoll(() => call<ProcInfo[]>("sys_processes"), 2000, active);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("cpu");
  const [kill, setKill] = useState<ProcInfo | null>(null);

  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    const f = (data ?? []).filter((p) => !t || p.name.toLowerCase().includes(t) || String(p.pid) === t || p.cmd.toLowerCase().includes(t));
    return f.sort((a, b) => (sort === "name" ? a.name.localeCompare(b.name) : sort === "pid" ? a.pid - b.pid : b[sort] - a[sort])).slice(0, 300);
  }, [data, q, sort]);

  const doKill = async (force: boolean) => {
    if (!kill) return;
    try { await call("sys_kill", { pid: kill.pid, force }); toast(`Đã gửi tín hiệu dừng ${kill.name} (${kill.pid})`); refresh(); }
    catch (e) { toast((e as Error).message, "err"); }
    setKill(null);
  };
  const th = (k: SortKey, label: string) => <th onClick={() => setSort(k)} className={"px-3 py-2 font-medium cursor-pointer select-none " + (sort === k ? "text-accent" : "")}>{label}{sort === k && " ↓"}</th>;

  return (
    <div className="space-y-3">
      <div className="relative max-w-sm"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm theo tên, PID, lệnh…" className="!pl-8" /></div>
      {error && <ErrorBox>{error}</ErrorBox>}
      <Card className="overflow-hidden">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-muted border-b border-line">{th("pid", "PID")}{th("name", "Tên")}<th className="px-3 py-2 font-medium">User</th>{th("cpu", "CPU")}{th("mem", "RAM")}<th className="px-3 py-2 font-medium">Trạng thái</th><th /></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.pid} className="border-b border-line/50 hover:bg-panel2 group" title={p.cmd}>
                <td className="px-3 py-1.5 tabular-nums text-muted">{p.pid}</td>
                <td className="px-3 py-1.5 max-w-64 truncate">{p.name}</td>
                <td className="px-3 py-1.5 text-muted">{p.user ?? "—"}</td>
                <td className="px-3 py-1.5 tabular-nums">{p.cpu.toFixed(1)}%</td>
                <td className="px-3 py-1.5 tabular-nums">{bytes(p.mem)}</td>
                <td className="px-3 py-1.5"><Badge>{p.status}</Badge></td>
                <td className="px-2 text-right"><button onClick={() => setKill(p)} className="opacity-0 group-hover:opacity-100 text-bad cursor-pointer" title="Dừng process"><X size={15} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <Empty title="Không có process khớp" />}
      </Card>
      {kill && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={() => setKill(null)}>
          <Card className="p-5 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
            <div className="font-semibold mb-1">Dừng {kill.name}?</div>
            <div className="text-sm text-muted mb-4">PID {kill.pid}. “Dừng” gửi SIGTERM (nhẹ nhàng), “Buộc dừng” gửi SIGKILL.</div>
            <div className="flex justify-end gap-2"><Button onClick={() => setKill(null)}>Huỷ</Button><Button onClick={() => doKill(false)}>Dừng</Button><Button variant="danger" onClick={() => doKill(true)}>Buộc dừng</Button></div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Ports({ active }: { active: boolean }) {
  const toast = useToast();
  const { data, error, refresh } = usePoll(() => call<PortInfo[]>("sys_ports"), 3000, active);
  const [q, setQ] = useState("");
  const [kill, setKill] = useState<PortInfo | null>(null);
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data ?? []).filter((p) => !t || String(p.port).includes(t) || p.process.toLowerCase().includes(t) || String(p.pid) === t);
  }, [data, q]);

  return (
    <div className="space-y-3">
      <div className="relative max-w-sm"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm port, process…" className="!pl-8" /></div>
      {error && <ErrorBox>{error}</ErrorBox>}
      <Card className="overflow-hidden">
        <Table head={["Port", "Giao thức", "Địa chỉ", "PID", "Process", ""]} rows={rows.map((p) => [
          <b key="p">{p.port}</b>, <Badge key="t" tone={p.protocol === "TCP" ? "accent" : "muted"}>{p.protocol}</Badge>, p.address, p.pid, p.process,
          <Button key="k" size="sm" variant="danger" onClick={() => setKill(p)}>Giải phóng</Button>,
        ])} />
        {!rows.length && <Empty title="Không có cổng đang lắng nghe" hint="Một số process của user khác có thể không hiển thị nếu thiếu quyền." />}
      </Card>
      <Confirm
        open={!!kill}
        title={`Giải phóng cổng ${kill?.port}?`}
        body={`Sẽ dừng process ${kill?.process} (PID ${kill?.pid}).`}
        onNo={() => setKill(null)}
        onYes={async () => {
          if (!kill) return;
          try { await call("sys_kill", { pid: kill.pid, force: false }); toast(`Đã dừng ${kill.process}`); setTimeout(refresh, 500); }
          catch (e) { toast((e as Error).message, "err"); }
          setKill(null);
        }}
      />
    </div>
  );
}

function DockerMon({ active }: { active: boolean }) {
  const st = usePoll(() => call<DockerStatus>("docker_status"), 10000, active);
  const ok = st.data?.available ?? false;
  const stats = usePoll(() => call<ContainerStats[]>("docker_stats"), 3000, active && ok);
  if (st.data && !ok) return <Empty title="Docker chưa sẵn sàng" hint={st.data.error} />;
  if (!stats.data) return <div className="text-muted">Đang đọc thống kê container…</div>;
  if (!stats.data.length) return <Empty title="Không có container nào đang chạy" />;
  return (
    <Card className="overflow-hidden">
      <Table head={["Container", "CPU", "RAM", "RAM %", "Net ↓", "Net ↑"]} rows={stats.data.map((c) => [
        c.name, `${c.cpu.toFixed(1)}%`, `${bytes(c.mem_used)} / ${bytes(c.mem_limit)}`,
        <div key="b" className="w-24"><Bar value={c.mem_limit ? (c.mem_used / c.mem_limit) * 100 : 0} /></div>, bytes(c.net_rx), bytes(c.net_tx),
      ])} />
    </Card>
  );
}
