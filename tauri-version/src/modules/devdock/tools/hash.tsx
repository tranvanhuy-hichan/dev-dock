import { useEffect, useState } from "react";
import { Card, CopyButton, Field, Input, TextArea } from "../../../components/ui";
import { enc } from "./shared";

export function md5(data: Uint8Array): string {
  const K = Array.from({ length: 64 }, (_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0);
  const S = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  const len = data.length;
  const total = (((len + 8) >> 6) + 1) << 6;
  const buf = new Uint8Array(total);
  buf.set(data);
  buf[len] = 0x80;
  const dv = new DataView(buf.buffer);
  dv.setUint32(total - 8, (len * 8) >>> 0, true);
  dv.setUint32(total - 4, Math.floor((len * 8) / 2 ** 32), true);
  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  for (let o = 0; o < total; o += 64) {
    const M = Array.from({ length: 16 }, (_, i) => dv.getUint32(o + i * 4, true));
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F: number, g: number;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      F = (F + A + K[i] + M[g]) >>> 0;
      A = D; D = C; C = B;
      const s = S[(i >> 4) * 4 + (i % 4)];
      B = (B + ((F << s) | (F >>> (32 - s)))) >>> 0;
    }
    a0 = (a0 + A) >>> 0; b0 = (b0 + B) >>> 0; c0 = (c0 + C) >>> 0; d0 = (d0 + D) >>> 0;
  }
  const out = new DataView(new ArrayBuffer(16));
  [a0, b0, c0, d0].forEach((v, i) => out.setUint32(i * 4, v, true));
  return hex(new Uint8Array(out.buffer));
}

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
const ALGS = [["SHA-1", "SHA-1"], ["SHA-256", "SHA-256"], ["SHA-384", "SHA-384"], ["SHA-512", "SHA-512"]] as const;

async function all(data: Uint8Array, secret: string) {
  const res: [string, string][] = [];
  if (!secret) res.push(["MD5", md5(data)]);
  for (const [label, alg] of ALGS) {
    if (secret) {
      const k = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: alg }, false, ["sign"]);
      res.push([`HMAC-${label}`, hex(new Uint8Array(await crypto.subtle.sign("HMAC", k, data as BufferSource)))]);
    } else {
      res.push([label, hex(new Uint8Array(await crypto.subtle.digest(alg, data as BufferSource)))]);
    }
  }
  return res;
}

export default function Hash() {
  const [text, setText] = useState("");
  const [secret, setSecret] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<[string, string][]>([]);
  const [upper, setUpper] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      const data = file ? new Uint8Array(await file.arrayBuffer()) : enc.encode(text);
      const r = await all(data, secret);
      if (alive) setRes(r);
    })();
    return () => { alive = false; };
  }, [text, secret, file]);

  return (
    <div className="h-full overflow-auto space-y-3 pr-1">
      <Field label={file ? `File: ${file.name} (${file.size} bytes)` : "Văn bản"} hint={file && <button className="text-accent cursor-pointer" onClick={() => setFile(null)}>Bỏ file</button>}>
        <TextArea rows={6} value={file ? "" : text} onChange={(e) => setText(e.target.value)} disabled={!!file} placeholder="Nhập văn bản để băm…" />
      </Field>
      <div className="grid md:grid-cols-2 gap-3">
        <Field label="Hoặc chọn file"><Input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
        <Field label="HMAC secret (tuỳ chọn)"><Input value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="để trống = băm thường" /></Field>
      </div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={upper} onChange={(e) => setUpper(e.target.checked)} /> HEX chữ hoa</label>
      <div className="space-y-2">
        {res.map(([name, h]) => {
          const v = upper ? h.toUpperCase() : h;
          return (
            <Card key={name} className="p-3 flex items-center gap-3">
              <span className="w-28 shrink-0 text-xs text-muted font-medium">{name}</span>
              <code className="flex-1 text-xs font-mono break-all">{v}</code>
              <CopyButton text={v} />
            </Card>
          );
        })}
      </div>
    </div>
  );
}
