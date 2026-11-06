import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";
import { Cloud, CloudDownload, CloudUpload, FolderClosed, Hash, KeyRound, Plus, Search, Star, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge, Button, Card, Confirm, CopyButton, Empty, ErrorBox, Field, Input, Select, TextArea, useToast, cx } from "../../components/ui";
import { call, Facets, Filter, Snippet, SnippetInput } from "../../lib/api";
import { ago } from "../../lib/format";
import { LANGS, langExt, langLabel } from "./languages";

const blank = (): SnippetInput => ({ id: null, title: "", language: "javascript", code: "", description: "", tags: [], folder: "", favorite: false });
const toInput = (s: Snippet): SnippetInput => ({ id: s.id, title: s.title, language: s.language, code: s.code, description: s.description, tags: s.tags, folder: s.folder, favorite: s.favorite });
const same = (a: SnippetInput, b: SnippetInput) => JSON.stringify(a) === JSON.stringify(b);

export default function CodeVault() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>({});
  const [query, setQuery] = useState("");
  const [list, setList] = useState<Snippet[]>([]);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<SnippetInput>(blank());
  const [saved, setSaved] = useState<SnippetInput>(blank());
  const [gistId, setGistId] = useState<string | null>(null);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [del, setDel] = useState(false);
  const [dark, setDark] = useState(true);
  const [gistOpen, setGistOpen] = useState(false);
  const [hasToken, setHasToken] = useState(false);
  const [busy, setBusy] = useState(false);

  const dirty = !same(draft, saved);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  useEffect(() => {
    const el = document.documentElement;
    const f = () => setDark(el.dataset.theme !== "light");
    f();
    const mo = new MutationObserver(f);
    mo.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);

  useEffect(() => { const t = setTimeout(() => setFilter((f) => ({ ...f, query })), 180); return () => clearTimeout(t); }, [query]);

  const reload = useCallback(async () => {
    try {
      const [l, f] = await Promise.all([call<Snippet[]>("vault_list", { filter }), call<Facets>("vault_facets")]);
      setList(l); setFacets(f); setError(null);
    } catch (e) { setError((e as Error).message); }
  }, [filter]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { call<boolean>("gist_has_token").then(setHasToken).catch(() => {}); }, [gistOpen]);

  const guard = (fn: () => void) => (dirty ? setPending(() => fn) : fn());
  const open = (s: Snippet) => guard(() => { const i = toInput(s); setDraft(i); setSaved(i); setGistId(s.gist_id); });
  const create = () => guard(() => { const b = { ...blank(), folder: filter.folder ?? "", tags: filter.tag ? [filter.tag] : [] }; setDraft(b); setSaved(b); setGistId(null); });

  const save = useCallback(async () => {
    const d = draftRef.current;
    if (!d.title.trim()) { toast("Cần nhập tiêu đề", "err"); return; }
    try {
      const s = await call<Snippet>("vault_save", { input: d });
      const i = toInput(s);
      setDraft(i); setSaved(i); setGistId(s.gist_id);
      toast("Đã lưu"); reload();
    } catch (e) { toast((e as Error).message, "err"); }
  }, [reload, toast]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "s" && document.querySelector("[data-vault]")?.contains(document.activeElement)) { e.preventDefault(); save(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [save]);

  const toggleFav = async (s: Snippet) => {
    try { await call("vault_favorite", { id: s.id, favorite: !s.favorite }); if (draft.id === s.id) { setDraft((d) => ({ ...d, favorite: !s.favorite })); setSaved((d) => ({ ...d, favorite: !s.favorite })); } reload(); }
    catch (e) { toast((e as Error).message, "err"); }
  };

  const pushGist = async () => {
    if (!hasToken) { setGistOpen(true); return; }
    if (dirty || draft.id == null) { toast("Hãy lưu snippet trước khi đồng bộ", "err"); return; }
    setBusy(true);
    try { const s = await call<Snippet>("gist_push", { id: draft.id }); setGistId(s.gist_id); toast("Đã đẩy lên GitHub Gist"); reload(); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  };
  const pullGist = async () => {
    if (!hasToken) { setGistOpen(true); return; }
    setBusy(true);
    try { const n = await call<number>("gist_pull"); toast(n ? `Đã nhập ${n} snippet từ Gist` : "Không có gist mới"); reload(); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  };

  const exts = useMemo(() => [...langExt(draft.language), EditorView.lineWrapping], [draft.language]);
  const sel = (patch: Partial<Filter>) => setFilter((f) => ({ query: f.query, ...patch }));
  const active = (k: keyof Filter) => filter[k];

  const Nav = ({ on, onClick, icon, label, count }: { on: boolean; onClick: () => void; icon: React.ReactNode; label: string; count?: number }) => (
    <button onClick={onClick} className={cx("w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm cursor-pointer", on ? "bg-accent/15 text-accent" : "text-muted hover:bg-panel2 hover:text-fg")}>
      {icon}<span className="flex-1 text-left truncate">{label}</span>{count !== undefined && <span className="text-xs opacity-70">{count}</span>}
    </button>
  );

  return (
    <div className="h-full flex" data-vault>
      {/* Sidebar */}
      <div className="w-52 shrink-0 border-r border-line p-2 overflow-auto space-y-4">
        <div className="space-y-0.5">
          <Nav on={!filter.folder && !filter.tag && !filter.favorites_only && !filter.language} onClick={() => sel({})} icon={<Hash size={14} />} label="Tất cả" count={facets?.total} />
          <Nav on={!!filter.favorites_only} onClick={() => sel({ favorites_only: true })} icon={<Star size={14} />} label="Yêu thích" count={facets?.favorites} />
        </div>
        {!!facets?.folders.length && <div><div className="px-2 text-xs uppercase tracking-wider text-muted mb-1">Thư mục</div>{facets.folders.map(([n, c]) => <Nav key={n} on={active("folder") === n} onClick={() => sel({ folder: n })} icon={<FolderClosed size={14} />} label={n} count={c} />)}</div>}
        {!!facets?.languages.length && <div><div className="px-2 text-xs uppercase tracking-wider text-muted mb-1">Ngôn ngữ</div>{facets.languages.map(([n, c]) => <Nav key={n} on={active("language") === n} onClick={() => sel({ language: n })} icon={<span className="w-3.5" />} label={langLabel(n)} count={c} />)}</div>}
        {!!facets?.tags.length && <div><div className="px-2 text-xs uppercase tracking-wider text-muted mb-1">Tag</div>{facets.tags.map(([n, c]) => <Nav key={n} on={active("tag") === n} onClick={() => sel({ tag: n })} icon={<Hash size={14} />} label={n} count={c} />)}</div>}
        <div className="border-t border-line pt-3 space-y-1">
          <div className="px-2 text-xs uppercase tracking-wider text-muted mb-1">GitHub Gist</div>
          <Button size="sm" className="w-full" onClick={pullGist} disabled={busy}><CloudDownload size={13} />Nhập từ Gist</Button>
          <Button size="sm" variant="ghost" className="w-full" onClick={() => setGistOpen(true)}><KeyRound size={13} />{hasToken ? "Đổi token" : "Cấu hình token"}</Button>
        </div>
      </div>

      {/* List */}
      <div className="w-72 shrink-0 border-r border-line flex flex-col">
        <div className="p-2 flex gap-2">
          <div className="relative flex-1"><Search size={14} className="absolute left-2.5 top-2.5 text-muted" /><Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm snippet…" className="!pl-8" /></div>
          <Button variant="primary" onClick={create} title="Snippet mới"><Plus size={16} /></Button>
        </div>
        <div className="flex-1 overflow-auto px-2 pb-2 space-y-1">
          {error && <ErrorBox>{error}</ErrorBox>}
          {list.map((s) => (
            <div key={s.id} onClick={() => open(s)} className={cx("group p-2.5 rounded-md cursor-pointer border", draft.id === s.id ? "bg-accent/10 border-accent/40" : "border-transparent hover:bg-panel2")}>
              <div className="flex items-start gap-1">
                <div className="flex-1 min-w-0 font-medium text-sm truncate">{s.title}</div>
                <button onClick={(e) => { e.stopPropagation(); toggleFav(s); }} className={cx("cursor-pointer", s.favorite ? "text-warn" : "text-muted opacity-0 group-hover:opacity-100")}><Star size={14} fill={s.favorite ? "currentColor" : "none"} /></button>
              </div>
              <div className="flex items-center gap-1.5 mt-1 text-xs text-muted">
                <Badge tone="accent">{langLabel(s.language)}</Badge>
                {s.gist_id && <Cloud size={12} />}
                <span className="ml-auto">{ago(s.updated)}</span>
              </div>
              {s.tags.length > 0 && <div className="mt-1 text-[11px] text-muted truncate">{s.tags.map((t) => "#" + t).join(" ")}</div>}
            </div>
          ))}
          {!list.length && !error && <Empty title={query || filter.folder || filter.tag || filter.language ? "Không tìm thấy" : "Chưa có snippet"} hint={query ? undefined : "Bấm + để tạo snippet đầu tiên."} />}
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 min-w-0 flex flex-col p-4 gap-3">
        <div className="flex gap-2 items-center">
          <Input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Tiêu đề snippet" className="!text-base !font-medium" />
          <Select value={draft.language} onChange={(e) => setDraft({ ...draft, language: e.target.value })}>{LANGS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}</Select>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Input value={draft.folder} onChange={(e) => setDraft({ ...draft, folder: e.target.value })} placeholder="Thư mục (vd. Work)" />
          <Input value={draft.tags.join(", ")} onChange={(e) => setDraft({ ...draft, tags: e.target.value.split(",").map((t) => t.trimStart()) })} onBlur={() => setDraft((d) => ({ ...d, tags: d.tags.map((t) => t.trim()).filter(Boolean) }))} placeholder="Tag, cách nhau bằng dấu phẩy" />
        </div>
        <TextArea rows={2} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Mô tả (tuỳ chọn)" className="!font-sans" />
        <Card className="flex-1 min-h-0 overflow-auto">
          <CodeMirror value={draft.code} onChange={(v) => setDraft((d) => ({ ...d, code: v }))} extensions={exts} theme={dark ? oneDark : "light"} height="100%" style={{ height: "100%" }} basicSetup={{ foldGutter: false }} />
        </Card>
        <div className="flex items-center gap-2">
          <Button variant="primary" onClick={save} disabled={!dirty && draft.id != null}>{draft.id == null ? "Tạo snippet" : "Lưu"}{dirty && " •"}</Button>
          <CopyButton text={() => draft.code} label="Copy code" size="md" />
          {draft.id != null && (
            <>
              <Button variant="ghost" onClick={() => { const n = !draft.favorite; setDraft({ ...draft, favorite: n }); if (!dirty) { call("vault_favorite", { id: draft.id, favorite: n }).then(() => { setSaved((s) => ({ ...s, favorite: n })); reload(); }).catch((e) => toast(e.message, "err")); } }}><Star size={14} fill={draft.favorite ? "currentColor" : "none"} className={draft.favorite ? "text-warn" : ""} />Yêu thích</Button>
              <Button variant="ghost" onClick={pushGist} disabled={busy}><CloudUpload size={14} />{gistId ? "Cập nhật Gist" : "Đẩy lên Gist"}</Button>
              {gistId && <a href={`https://gist.github.com/${gistId}`} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">Mở Gist ↗</a>}
              <Button variant="danger" className="ml-auto" onClick={() => setDel(true)}><Trash2 size={14} />Xoá</Button>
            </>
          )}
        </div>
      </div>

      <Confirm open={!!pending} title="Bỏ thay đổi chưa lưu?" body="Snippet đang sửa có thay đổi chưa được lưu." onNo={() => setPending(null)} onYes={() => { const p = pending; setPending(null); p?.(); }} />
      <DeleteDialog open={del} hasGist={!!gistId && hasToken} onClose={() => setDel(false)} onDelete={async (remote) => {
        try { await call("vault_delete", { id: draft.id, deleteRemote: remote }); const b = blank(); setDraft(b); setSaved(b); setGistId(null); toast("Đã xoá"); reload(); }
        catch (e) { toast((e as Error).message, "err"); }
        setDel(false);
      }} />
      <GistDialog open={gistOpen} hasToken={hasToken} onClose={() => setGistOpen(false)} />
    </div>
  );
}

function DeleteDialog({ open, hasGist, onClose, onDelete }: { open: boolean; hasGist: boolean; onClose: () => void; onDelete: (remote: boolean) => void }) {
  const [remote, setRemote] = useState(false);
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="p-5 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
        <div className="font-semibold mb-3">Xoá snippet này?</div>
        {hasGist && <label className="flex items-center gap-2 text-sm mb-4"><input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} />Xoá cả gist trên GitHub</label>}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Huỷ</Button><Button variant="danger" onClick={() => onDelete(remote)} autoFocus>Xoá</Button></div>
      </Card>
    </div>
  );
}

function GistDialog({ open, hasToken, onClose }: { open: boolean; hasToken: boolean; onClose: () => void }) {
  const toast = useToast();
  const [token, setToken] = useState("");
  useEffect(() => { if (open) setToken(""); }, [open]);
  if (!open) return null;
  const save = async (t: string) => {
    try { await call("gist_set_token", { token: t }); toast(t ? "Đã lưu token" : "Đã xoá token"); onClose(); }
    catch (e) { toast((e as Error).message, "err"); }
  };
  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <Card className="p-5 w-full max-w-md space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="font-semibold">GitHub token cho Gist</div>
        <p className="text-sm text-muted">Tạo <i>Personal access token</i> (classic) có quyền <code>gist</code> tại GitHub → Settings → Developer settings. Token lưu cục bộ trong cơ sở dữ liệu của app.</p>
        <Field label={hasToken ? "Token mới (đã có token)" : "Token"}><Input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="ghp_…" autoFocus /></Field>
        <div className="flex justify-between">
          <Button variant="danger" onClick={() => save("")} disabled={!hasToken}>Xoá token</Button>
          <div className="flex gap-2"><Button onClick={onClose}>Đóng</Button><Button variant="primary" onClick={() => save(token)} disabled={!token.trim()}>Lưu</Button></div>
        </div>
      </Card>
    </div>
  );
}
