export function Sparkline({ data, max, color = "var(--accent)", height = 48, fill = true }: { data: number[]; max?: number; color?: string; height?: number; fill?: boolean }) {
  const W = 200;
  const m = max ?? Math.max(1, ...data);
  const pts = data.map((v, i) => [(i / Math.max(1, data.length - 1)) * W, height - (Math.min(v, m) / m) * (height - 2) - 1] as const);
  if (pts.length < 2) return <svg viewBox={`0 0 ${W} ${height}`} className="w-full" style={{ height }} />;
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      {fill && <path d={`${line} L${W},${height} L0,${height} Z`} fill={color} opacity={0.15} />}
      <path d={line} fill="none" stroke={color} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
