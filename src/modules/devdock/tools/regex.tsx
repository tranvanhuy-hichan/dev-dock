import { ReactNode, useMemo, useState } from "react";
import { Badge, Card, ErrorBox, Field, Input, TextArea } from "../../../components/ui";

const FLAGS = [["g", "global"], ["i", "ignore case"], ["m", "multiline"], ["s", "dotAll"], ["u", "unicode"], ["y", "sticky"]] as const;

export default function Regex() {
  const [pattern, setPattern] = useState("(\\w+)@(\\w+)\\.com");
  const [flags, setFlags] = useState("g");
  const [text, setText] = useState("hello@example.com, dev@makeai.com");
  const [repl, setRepl] = useState("$2:$1");

  const r = useMemo(() => {
    if (!pattern) return { matches: [] as RegExpExecArray[], error: null as string | null, re: null as RegExp | null };
    try {
      const re = new RegExp(pattern, flags.includes("g") ? flags : flags + "g");
      const matches: RegExpExecArray[] = [];
      let m: RegExpExecArray | null;
      while ((m = re.exec(text)) && matches.length < 1000) {
        matches.push(m);
        if (m[0] === "") re.lastIndex++;
      }
      return { matches, error: null, re };
    } catch (e) { return { matches: [], error: (e as Error).message, re: null }; }
  }, [pattern, flags, text]);

  const highlighted = useMemo(() => {
    const out: ReactNode[] = [];
    let last = 0;
    r.matches.forEach((m, i) => {
      if (m[0] === "") return;
      out.push(text.slice(last, m.index));
      out.push(<mark key={i} className="bg-accent/40 text-fg rounded-sm">{m[0]}</mark>);
      last = m.index + m[0].length;
    });
    out.push(text.slice(last));
    return out;
  }, [r.matches, text]);

  const replaced = useMemo(() => {
    if (!r.re) return "";
    try { return text.replace(new RegExp(r.re.source, r.re.flags), repl); } catch { return ""; }
  }, [r.re, text, repl]);

  const toggle = (f: string) => setFlags(flags.includes(f) ? flags.replace(f, "") : flags + f);

  return (
    <div className="h-full overflow-auto space-y-3 pr-1">
      <div className="flex gap-2 items-center">
        <span className="text-muted font-mono">/</span>
        <Input value={pattern} onChange={(e) => setPattern(e.target.value)} className="font-mono" placeholder="pattern" />
        <span className="text-muted font-mono">/{flags}</span>
      </div>
      <div className="flex flex-wrap gap-3">
        {FLAGS.map(([f, n]) => (
          <label key={f} className="flex items-center gap-1.5 text-sm" title={n}><input type="checkbox" checked={flags.includes(f)} onChange={() => toggle(f)} /> <code>{f}</code> <span className="text-muted text-xs">{n}</span></label>
        ))}
      </div>
      {r.error && <ErrorBox>{r.error}</ErrorBox>}
      <Field label="Văn bản kiểm tra"><TextArea rows={6} value={text} onChange={(e) => setText(e.target.value)} /></Field>
      <Card className="p-3">
        <div className="text-xs text-muted mb-1 flex gap-2 items-center">Kết quả <Badge tone={r.matches.length ? "ok" : "muted"}>{r.matches.length} match</Badge></div>
        <pre className="text-sm font-mono whitespace-pre-wrap break-words">{highlighted}</pre>
      </Card>
      {r.matches.length > 0 && (
        <Card className="p-3 space-y-2">
          <div className="text-xs text-muted">Chi tiết</div>
          {r.matches.slice(0, 100).map((m, i) => (
            <div key={i} className="text-xs font-mono border-b border-line last:border-0 pb-1">
              <span className="text-muted">#{i + 1} @{m.index}</span> <b>{JSON.stringify(m[0])}</b>
              {m.slice(1).map((g, gi) => <div key={gi} className="pl-4 text-muted">${gi + 1}: <span className="text-fg">{g === undefined ? "undefined" : JSON.stringify(g)}</span></div>)}
              {m.groups && Object.entries(m.groups).map(([k, v]) => <div key={k} className="pl-4 text-muted">&lt;{k}&gt;: <span className="text-fg">{JSON.stringify(v)}</span></div>)}
            </div>
          ))}
        </Card>
      )}
      <Field label="Replace với ($1, $<name>, $&)"><Input value={repl} onChange={(e) => setRepl(e.target.value)} className="font-mono" /></Field>
      {r.re && <Card className="p-3"><pre className="text-sm font-mono whitespace-pre-wrap break-words">{replaced}</pre></Card>}
    </div>
  );
}
