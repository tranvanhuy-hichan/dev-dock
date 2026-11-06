import { useMemo, useState } from "react";
import { Card, ErrorBox, Tabs } from "../../../components/ui";
import { Pane } from "./shared";

export default function UrlTool() {
  const [mode, setMode] = useState<"enc" | "dec" | "parse">("enc");
  const [full, setFull] = useState(false);
  const [input, setInput] = useState("");

  const r = useMemo(() => {
    if (!input) return { out: "", err: null as string | null };
    try {
      if (mode === "enc") return { out: full ? encodeURI(input) : encodeURIComponent(input), err: null };
      if (mode === "dec") return { out: (full ? decodeURI : decodeURIComponent)(input.replace(/\+/g, " ")), err: null };
      return { out: "", err: null };
    } catch (e) { return { out: "", err: (e as Error).message }; }
  }, [input, mode, full]);

  const parsed = useMemo(() => {
    if (mode !== "parse" || !input.trim()) return null;
    try { return new URL(input.trim()); } catch { return "invalid" as const; }
  }, [input, mode]);

  return (
    <div className="h-full flex flex-col gap-3">
      <Tabs tabs={[{ id: "enc", label: "Encode" }, { id: "dec", label: "Decode" }, { id: "parse", label: "Phân tích URL" }]} value={mode} onChange={setMode} />
      {mode !== "parse" && (
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} /> Giữ nguyên ký tự của URL (<code>encodeURI</code>) thay vì <code>encodeURIComponent</code></label>
      )}
      {r.err && <ErrorBox>{r.err}</ErrorBox>}
      {mode === "parse" ? (
        <>
          <Pane title="URL" value={input} onChange={setInput} rows={3} placeholder="https://user:pw@example.com:8080/path?a=1&b=2#frag" className="!flex-none" />
          {parsed === "invalid" && <ErrorBox>URL không hợp lệ (cần có scheme, vd https://)</ErrorBox>}
          {parsed && parsed !== "invalid" && (
            <div className="overflow-auto space-y-3">
              <Card className="p-3 text-sm font-mono space-y-1">
                {([["protocol", parsed.protocol], ["username", parsed.username], ["password", parsed.password], ["host", parsed.hostname], ["port", parsed.port], ["path", parsed.pathname], ["hash", parsed.hash]] as const).map(([k, v]) => (
                  <div key={k} className="flex"><span className="w-24 text-muted">{k}</span><span className="break-all">{v || <i className="text-muted">—</i>}</span></div>
                ))}
              </Card>
              {[...parsed.searchParams].length > 0 && (
                <Card className="p-3 text-sm font-mono">
                  <div className="text-xs text-muted mb-1">Query params</div>
                  {[...parsed.searchParams].map(([k, v], i) => <div key={i} className="flex gap-3"><span className="w-40 shrink-0 text-accent break-all">{k}</span><span className="break-all">{v}</span></div>)}
                </Card>
              )}
            </div>
          )}
        </>
      ) : (
        <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
          <Pane title="Input" value={input} onChange={setInput} className="h-full" />
          <Pane title="Output" value={r.out} readOnly className="h-full" />
        </div>
      )}
    </div>
  );
}
