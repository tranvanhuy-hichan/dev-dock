import { useEffect, useMemo, useState } from "react";
import { Button, Card, CopyButton, Field, Input } from "../../../components/ui";
import { Row } from "./shared";

export default function Timestamp() {
  const [now, setNow] = useState(Date.now());
  const [input, setInput] = useState(String(Math.floor(Date.now() / 1000)));
  const [dt, setDt] = useState("");
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  const parsed = useMemo(() => {
    const t = input.trim();
    if (!t) return null;
    let ms: number;
    if (/^-?\d+(\.\d+)?$/.test(t)) { const n = Number(t); ms = Math.abs(n) >= 1e11 ? n : n * 1000; }
    else ms = Date.parse(t);
    return isNaN(ms) ? "invalid" as const : new Date(ms);
  }, [input]);

  const fromDt = dt ? new Date(dt) : null;
  const rows = (d: Date): [string, string][] => [
    ["Unix (giây)", String(Math.floor(d.getTime() / 1000))], ["Unix (ms)", String(d.getTime())],
    ["ISO 8601 (UTC)", d.toISOString()], ["Giờ máy", d.toLocaleString("vi-VN", { dateStyle: "full", timeStyle: "long" })],
    ["UTC", d.toUTCString()],
  ];

  return (
    <div className="h-full overflow-auto space-y-3 pr-1">
      <Card className="p-3 flex items-center justify-between">
        <div><div className="text-xs text-muted">Hiện tại</div><div className="font-mono">{Math.floor(now / 1000)} <span className="text-muted">/</span> {now}</div></div>
        <Row><CopyButton text={String(Math.floor(now / 1000))} label="giây" /><CopyButton text={String(now)} label="ms" /><Button size="sm" onClick={() => setInput(String(Math.floor(Date.now() / 1000)))}>Dùng</Button></Row>
      </Card>
      <Field label="Timestamp hoặc chuỗi ngày (tự nhận biết giây/ms)"><Input value={input} onChange={(e) => setInput(e.target.value)} className="font-mono" /></Field>
      {parsed === "invalid" && <div className="text-bad text-sm">Không hiểu định dạng này</div>}
      {parsed instanceof Date && <Out rows={rows(parsed)} />}
      <Field label="Chọn ngày giờ → timestamp"><Input type="datetime-local" step={1} value={dt} onChange={(e) => setDt(e.target.value)} /></Field>
      {fromDt && !isNaN(fromDt.getTime()) && <Out rows={rows(fromDt)} />}
    </div>
  );
}

const Out = ({ rows }: { rows: [string, string][] }) => (
  <Card className="p-2">
    {rows.map(([k, v]) => (
      <div key={k} className="flex items-center gap-3 px-2 py-1 hover:bg-panel2 rounded">
        <span className="w-32 shrink-0 text-xs text-muted">{k}</span><code className="flex-1 text-sm font-mono break-all">{v}</code><CopyButton text={v} />
      </div>
    ))}
  </Card>
);
