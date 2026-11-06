import { useMemo, useState } from "react";
import { Button, ErrorBox, Tabs } from "../../../components/ui";
import { b64ToUtf8, utf8ToB64 } from "./shared";
import { Pane, Row } from "./shared";

export default function Base64() {
  const [mode, setMode] = useState<"enc" | "dec">("enc");
  const [urlSafe, setUrlSafe] = useState(false);
  const [input, setInput] = useState("");

  const { out, err } = useMemo(() => {
    if (!input) return { out: "", err: null };
    try { return { out: mode === "enc" ? utf8ToB64(input, urlSafe) : b64ToUtf8(input), err: null }; }
    catch (e) { return { out: "", err: (e as Error).message.includes("encoded data") ? "Không phải UTF-8 hợp lệ" : (e as Error).message }; }
  }, [input, mode, urlSafe]);

  return (
    <div className="h-full flex flex-col gap-3">
      <Tabs tabs={[{ id: "enc", label: "Encode" }, { id: "dec", label: "Decode" }]} value={mode} onChange={(m) => { setMode(m); setInput(""); }} />
      <Row>
        {mode === "enc" && (
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={urlSafe} onChange={(e) => setUrlSafe(e.target.checked)} /> URL-safe (không padding)</label>
        )}
        <Button variant="ghost" onClick={() => { setMode(mode === "enc" ? "dec" : "enc"); setInput(out); }} disabled={!out}>⇄ Đảo chiều</Button>
      </Row>
      {err && <ErrorBox>{err}</ErrorBox>}
      <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
        <Pane title={mode === "enc" ? "Văn bản" : "Base64"} value={input} onChange={setInput} className="h-full" />
        <Pane title={mode === "enc" ? "Base64" : "Văn bản"} value={out} readOnly className="h-full" />
      </div>
    </div>
  );
}
