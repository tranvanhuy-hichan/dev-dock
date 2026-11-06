import { useState } from "react";
import { Button, ErrorBox, Select, Tabs } from "../../../components/ui";
import { Pane, Row } from "./shared";

function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v as object).sort(([a], [b]) => a.localeCompare(b)).map(([k, x]) => [k, sortKeys(x)]));
  return v;
}

function jsonError(e: unknown, src: string): string {
  const msg = (e as Error).message;
  const m = /position (\d+)/.exec(msg);
  if (!m) return msg;
  const pos = +m[1];
  const before = src.slice(0, pos);
  return `${msg}\n→ dòng ${before.split("\n").length}, cột ${pos - before.lastIndexOf("\n")}`;
}

function parseXml(src: string): Document {
  const doc = new DOMParser().parseFromString(src, "application/xml");
  const err = doc.querySelector("parsererror");
  if (err) throw new Error(err.textContent?.trim().split("\n")[0] ?? "XML không hợp lệ");
  return doc;
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escAttr = (s: string) => esc(s).replace(/"/g, "&quot;");

function ser(n: Node, ind: string, step: string, minify: boolean): string {
  const nl = minify ? "" : "\n";
  switch (n.nodeType) {
    case 9: return Array.from(n.childNodes).map((c) => ser(c, ind, step, minify)).join(nl);
    case 7: return `${ind}<?${(n as ProcessingInstruction).target} ${(n as ProcessingInstruction).data}?>`;
    case 8: return `${ind}<!--${(n as Comment).data}-->`;
    case 4: return `${ind}<![CDATA[${(n as CDATASection).data}]]>`;
    case 3: { const t = (n.nodeValue ?? "").trim(); return t ? ind + esc(t) : ""; }
    case 1: {
      const el = n as Element;
      const attrs = Array.from(el.attributes).map((a) => ` ${a.name}="${escAttr(a.value)}"`).join("");
      const kids = Array.from(el.childNodes).filter((c) => c.nodeType !== 3 || (c.nodeValue ?? "").trim());
      if (!kids.length) return `${ind}<${el.tagName}${attrs}/>`;
      if (kids.length === 1 && kids[0].nodeType === 3) return `${ind}<${el.tagName}${attrs}>${esc(kids[0].nodeValue!.trim())}</${el.tagName}>`;
      const inner = kids.map((c) => ser(c, ind + step, step, minify)).filter(Boolean).join(nl);
      return `${ind}<${el.tagName}${attrs}>${nl}${inner}${nl}${ind}</${el.tagName}>`;
    }
    default: return "";
  }
}

export function formatXml(src: string, indent: string, minify: boolean): string {
  const doc = parseXml(src);
  const decl = /^\s*<\?xml[^>]*\?>/.exec(src)?.[0];
  const body = ser(doc, "", minify ? "" : indent, minify);
  return decl ? decl + (minify ? "" : "\n") + body : body;
}

export default function JsonXml() {
  const [mode, setMode] = useState<"json" | "xml">("json");
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [indent, setIndent] = useState("2");
  const ind = indent === "tab" ? "\t" : " ".repeat(+indent);

  const run = (kind: "format" | "minify" | "sort" | "validate") => {
    try {
      if (!input.trim()) { setOutput(""); setErr(null); return; }
      if (mode === "json") {
        let v = JSON.parse(input);
        if (kind === "sort") v = sortKeys(v);
        if (kind === "validate") { setOutput("✓ JSON hợp lệ"); setErr(null); return; }
        setOutput(kind === "minify" ? JSON.stringify(v) : JSON.stringify(v, null, ind));
      } else {
        if (kind === "validate") { parseXml(input); setOutput("✓ XML hợp lệ"); setErr(null); return; }
        setOutput(formatXml(input, ind, kind === "minify"));
      }
      setErr(null);
    } catch (e) {
      setOutput("");
      setErr(mode === "json" ? jsonError(e, input) : (e as Error).message);
    }
  };

  return (
    <div className="h-full flex flex-col gap-3">
      <Tabs tabs={[{ id: "json", label: "JSON" }, { id: "xml", label: "XML" }]} value={mode} onChange={(m) => { setMode(m); setErr(null); setOutput(""); }} />
      <Row>
        <Button variant="primary" onClick={() => run("format")}>Format</Button>
        <Button onClick={() => run("minify")}>Minify</Button>
        {mode === "json" && <Button onClick={() => run("sort")}>Sắp xếp key</Button>}
        <Button onClick={() => run("validate")}>Validate</Button>
        <Select value={indent} onChange={(e) => setIndent(e.target.value)}>
          <option value="2">2 spaces</option><option value="4">4 spaces</option><option value="tab">Tab</option>
        </Select>
        <Button variant="ghost" onClick={() => { setInput(output || input); setOutput(""); }} disabled={!output}>Dùng output làm input</Button>
      </Row>
      {err && <ErrorBox>{err}</ErrorBox>}
      <div className="grid grid-cols-2 gap-3 flex-1 min-h-0">
        <Pane title="Input" value={input} onChange={setInput} placeholder={mode === "json" ? '{"hello":"world"}' : "<root><a>1</a></root>"} className="h-full" />
        <Pane title="Output" value={output} readOnly className="h-full" />
      </div>
    </div>
  );
}
