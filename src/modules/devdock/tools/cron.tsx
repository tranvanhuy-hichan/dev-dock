import cronstrue from "cronstrue";
import { CronExpressionParser } from "cron-parser";
import { useMemo, useState } from "react";
import { Button, Card, ErrorBox, Input, Select } from "../../../components/ui";
import { Row } from "./shared";

const PRESETS: [string, string][] = [
  ["Mỗi phút", "* * * * *"], ["Mỗi 5 phút", "*/5 * * * *"], ["Mỗi giờ", "0 * * * *"],
  ["Hằng ngày 00:00", "0 0 * * *"], ["Ngày thường 9:00", "0 9 * * 1-5"], ["Thứ Hai 8:30", "30 8 * * 1"], ["Đầu tháng", "0 0 1 * *"],
];

export default function Cron() {
  const [expr, setExpr] = useState("*/15 9-17 * * 1-5");
  const [tz, setTz] = useState("local");

  const r = useMemo(() => {
    try {
      const human = cronstrue.toString(expr, { locale: "vi", use24HourTimeFormat: true });
      const it = CronExpressionParser.parse(expr, tz === "local" ? {} : { tz });
      const next = Array.from({ length: 10 }, () => it.next().toDate());
      return { human, next, error: null };
    } catch (e) { return { human: "", next: [] as Date[], error: (e as Error).message }; }
  }, [expr, tz]);

  const fields = expr.trim().split(/\s+/);
  const labels = fields.length >= 6 ? ["giây", "phút", "giờ", "ngày", "tháng", "thứ"] : ["phút", "giờ", "ngày", "tháng", "thứ"];

  return (
    <div className="h-full overflow-auto space-y-3 pr-1">
      <Input value={expr} onChange={(e) => setExpr(e.target.value)} className="font-mono !text-lg !py-2" placeholder="* * * * *" />
      <div className="flex gap-2 text-xs text-muted font-mono">
        {labels.map((l, i) => <div key={l} className="flex-1 text-center"><div className="text-fg text-sm">{fields[i] ?? "·"}</div>{l}</div>)}
      </div>
      <Row>
        {PRESETS.map(([n, e]) => <Button key={e} size="sm" onClick={() => setExpr(e)}>{n}</Button>)}
        <Select value={tz} onChange={(e) => setTz(e.target.value)}>
          <option value="local">Múi giờ máy</option><option value="UTC">UTC</option><option value="Asia/Ho_Chi_Minh">Asia/Ho_Chi_Minh</option>
        </Select>
      </Row>
      {r.error ? <ErrorBox>{r.error}</ErrorBox> : (
        <>
          <Card className="p-4 text-base">{r.human}</Card>
          <Card className="p-3">
            <div className="text-xs text-muted mb-2">10 lần chạy tiếp theo</div>
            {r.next.map((d, i) => (
              <div key={i} className="flex justify-between text-sm py-0.5 font-mono">
                <span>{d.toLocaleString("vi-VN", { dateStyle: "full", timeStyle: "medium", timeZone: tz === "local" ? undefined : tz })}</span>
                <span className="text-muted">{rel(d)}</span>
              </div>
            ))}
          </Card>
        </>
      )}
    </div>
  );
}

function rel(d: Date): string {
  const s = Math.round((d.getTime() - Date.now()) / 1000);
  if (s < 60) return `${s}s nữa`;
  if (s < 3600) return `${Math.round(s / 60)} phút nữa`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} giờ nữa`;
  return `${(s / 86400).toFixed(1)} ngày nữa`;
}
