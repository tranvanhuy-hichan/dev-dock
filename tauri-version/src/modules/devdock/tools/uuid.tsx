import { useCallback, useEffect, useState } from "react";
import { Button, Card, CopyButton, Input, Select } from "../../../components/ui";
import { Row } from "./shared";

function uuidv7(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  const ts = BigInt(Date.now());
  for (let i = 0; i < 6; i++) b[i] = Number((ts >> BigInt(8 * (5 - i))) & 0xffn);
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const ULID_CHARS = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
function ulid(): string {
  let t = Date.now(), ts = "";
  for (let i = 0; i < 10; i++) { ts = ULID_CHARS[t % 32] + ts; t = Math.floor(t / 32); }
  const r = crypto.getRandomValues(new Uint8Array(16));
  return ts + Array.from(r, (x) => ULID_CHARS[x % 32]).join("");
}

const GEN = { v4: () => crypto.randomUUID(), v7: uuidv7, ulid } as const;

export default function Uuid() {
  const [kind, setKind] = useState<keyof typeof GEN>("v4");
  const [count, setCount] = useState(5);
  const [upper, setUpper] = useState(false);
  const [nodash, setNodash] = useState(false);
  const [list, setList] = useState<string[]>([]);

  const gen = useCallback(() => setList(Array.from({ length: Math.max(1, Math.min(1000, count)) }, GEN[kind])), [kind, count]);
  useEffect(gen, [gen]);

  const fmt = (s: string) => { const r = nodash ? s.replace(/-/g, "") : s; return upper ? r.toUpperCase() : kind === "ulid" ? r : r.toLowerCase(); };
  const all = list.map(fmt).join("\n");

  return (
    <div className="h-full flex flex-col gap-3">
      <Row>
        <Select value={kind} onChange={(e) => setKind(e.target.value as keyof typeof GEN)}>
          <option value="v4">UUID v4 (random)</option><option value="v7">UUID v7 (time-ordered)</option><option value="ulid">ULID</option>
        </Select>
        <Input type="number" min={1} max={1000} value={count} onChange={(e) => setCount(+e.target.value)} className="!w-24" />
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={upper} onChange={(e) => setUpper(e.target.checked)} /> Chữ hoa</label>
        {kind !== "ulid" && <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={nodash} onChange={(e) => setNodash(e.target.checked)} /> Bỏ dấu gạch</label>}
        <Button variant="primary" onClick={gen}>Tạo mới</Button>
        <CopyButton text={all} label="Copy tất cả" size="md" />
      </Row>
      <Card className="flex-1 overflow-auto p-2">
        {list.map((u, i) => (
          <div key={i} className="flex items-center justify-between px-2 py-1 hover:bg-panel2 rounded group">
            <code className="font-mono text-sm">{fmt(u)}</code>
            <span className="opacity-0 group-hover:opacity-100"><CopyButton text={fmt(u)} /></span>
          </div>
        ))}
      </Card>
    </div>
  );
}
