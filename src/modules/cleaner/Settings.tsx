import { useEffect, useState } from "react";
import { Button, Card, Field, Input, useToast } from "../../components/ui";
import { call, CleanSettings } from "../../lib/api";
import { bytes } from "../../lib/format";

export function SettingsTab({ settings, onChange }: { settings: CleanSettings; onChange: (s: CleanSettings) => void }) {
  const toast = useToast();
  const [s, setS] = useState(settings);
  useEffect(() => setS(settings), [settings]);
  const save = async (n: CleanSettings) => {
    setS(n);
    try { onChange(await call<CleanSettings>("cleaner_settings_set", { settings: n })); }
    catch (e) { toast((e as Error).message, "err"); setS(settings); }
  };
  const sw = (k: "trash_large" | "scan_large" | "dup_scan", title: string, sub: string) => (
    <label className="flex items-start gap-3 py-2 cursor-pointer"><input type="checkbox" className="mt-1" checked={s[k]} onChange={(e) => save({ ...s, [k]: e.target.checked })} /><span><span className="text-sm block">{title}</span><span className="text-xs text-muted">{sub}</span></span></label>
  );
  const num = (k: "min_mb" | "large_mb" | "dup_mb", title: string) => (
    <Field label={title}><Input type="number" min={1} value={s[k]} onChange={(e) => setS({ ...s, [k]: Math.max(1, +e.target.value || 1) })} onBlur={() => s[k] !== settings[k] && save(s)} /></Field>
  );
  const ign = Object.entries(s.ignored_titles);
  return (
    <div className="p-5 space-y-4 max-w-2xl">
      <Card className="p-4">
        <div className="font-medium mb-1">Quét</div>
        {sw("scan_large", "Tìm tệp lớn", "40 tệp lớn nhất trong thư mục home (bỏ qua thư mục ẩn)")}
        {sw("dup_scan", "Tìm tệp trùng lặp", "So kích thước, hash 64 KB đầu rồi hash toàn bộ; luôn giữ bản cũ nhất")}
        <div className="grid grid-cols-3 gap-3 mt-2">{num("min_mb", "Bỏ qua mục nhỏ hơn (MB)")}{num("large_mb", "Tệp lớn từ (MB)")}{num("dup_mb", "Tệp trùng từ (MB)")}</div>
      </Card>
      <Card className="p-4">
        <div className="font-medium mb-1">Khi dọn</div>
        {sw("trash_large", "Chuyển tệp lớn / tệp trùng vào thùng rác", "Cache và thư mục build luôn bị xoá vĩnh viễn để giải phóng dung lượng thật")}
      </Card>
      <Card className="p-4">
        <div className="font-medium mb-2">Mục đã ẩn</div>
        {ign.length === 0 && <div className="text-sm text-muted">Chưa ẩn mục nào.</div>}
        {ign.map(([id, title]) => (
          <div key={id} className="flex items-center justify-between py-1 text-sm"><span className="truncate">{title}</span>
            <Button size="sm" onClick={async () => onChange(await call<CleanSettings>("cleaner_ignore", { id, title, ignore: false }))}>Hiện lại</Button></div>
        ))}
      </Card>
      <Card className="p-4 text-sm">Đã dọn <b>{s.clean_count}</b> mục · tổng giải phóng <b>{bytes(s.total_freed)}</b></Card>
    </div>
  );
}
