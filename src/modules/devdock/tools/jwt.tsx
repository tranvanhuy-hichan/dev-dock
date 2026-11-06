import { useMemo, useState } from "react";
import { Badge, Card, CopyButton, ErrorBox, Field, Input, TextArea } from "../../../components/ui";
import { b64ToBytes, b64ToUtf8, enc } from "./shared";

const TS = ["exp", "iat", "nbf"];
const ALG: Record<string, string> = { HS256: "SHA-256", HS384: "SHA-384", HS512: "SHA-512" };

export default function Jwt() {
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [verified, setVerified] = useState<null | boolean | string>(null);

  const parsed = useMemo(() => {
    const t = token.trim();
    if (!t) return null;
    try {
      const [h, p, s] = t.split(".");
      if (!h || !p || s === undefined) throw new Error("JWT phải có dạng header.payload.signature");
      return { header: JSON.parse(b64ToUtf8(h)), payload: JSON.parse(b64ToUtf8(p)), sig: s, signingInput: `${h}.${p}` };
    } catch (e) {
      return { error: (e as Error).message };
    }
  }, [token]);

  const ok = parsed && !("error" in parsed) ? parsed : null;

  const verify = async () => {
    if (!ok) return;
    const hash = ALG[ok.header.alg];
    if (!hash) { setVerified(`Chưa hỗ trợ verify thuật toán ${ok.header.alg ?? "?"} (chỉ HS256/384/512)`); return; }
    try {
      const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash }, false, ["verify"]);
      setVerified(await crypto.subtle.verify("HMAC", key, b64ToBytes(ok.sig), enc.encode(ok.signingInput)));
    } catch (e) { setVerified((e as Error).message); }
  };

  const now = Date.now() / 1000;
  const exp: number | undefined = ok?.payload?.exp;

  return (
    <div className="h-full overflow-auto space-y-3 pr-1">
      <Field label="JWT token"><TextArea rows={5} value={token} onChange={(e) => { setToken(e.target.value); setVerified(null); }} placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…" /></Field>
      {parsed && "error" in parsed && <ErrorBox>{parsed.error}</ErrorBox>}
      {ok && (
        <>
          <div className="flex gap-2 items-center">
            <Badge tone="accent">{ok.header.alg ?? "no alg"}</Badge>
            {typeof exp === "number" && (exp < now ? <Badge tone="bad">Đã hết hạn</Badge> : <Badge tone="ok">Còn hạn</Badge>)}
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {([["Header", ok.header], ["Payload", ok.payload]] as const).map(([name, obj]) => (
              <Card key={name} className="p-3">
                <div className="flex justify-between items-center mb-1"><span className="text-xs text-muted">{name}</span><CopyButton text={JSON.stringify(obj, null, 2)} /></div>
                <pre className="text-xs font-mono whitespace-pre-wrap break-all">{JSON.stringify(obj, null, 2)}</pre>
              </Card>
            ))}
          </div>
          {TS.some((k) => typeof ok.payload[k] === "number") && (
            <Card className="p-3 text-sm space-y-1">
              {TS.filter((k) => typeof ok.payload[k] === "number").map((k) => (
                <div key={k} className="flex gap-3"><span className="w-10 text-muted font-mono">{k}</span><span>{new Date(ok.payload[k] * 1000).toLocaleString("vi-VN")}</span></div>
              ))}
            </Card>
          )}
          <Card className="p-3 space-y-2">
            <div className="text-xs text-muted">Verify chữ ký (HMAC) — secret chỉ xử lý cục bộ</div>
            <div className="flex gap-2">
              <Input value={secret} onChange={(e) => { setSecret(e.target.value); setVerified(null); }} placeholder="secret" type="password" />
              <button onClick={verify} className="px-3 rounded-md bg-accent text-accentfg text-sm cursor-pointer">Verify</button>
            </div>
            {verified === true && <Badge tone="ok">✓ Chữ ký hợp lệ</Badge>}
            {verified === false && <Badge tone="bad">✗ Chữ ký sai</Badge>}
            {typeof verified === "string" && <ErrorBox>{verified}</ErrorBox>}
          </Card>
        </>
      )}
    </div>
  );
}
