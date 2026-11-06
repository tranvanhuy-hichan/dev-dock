/** Thuật toán squarified treemap. `sizes` đã sắp giảm dần (> 0). Trả về [index, x, y, w, h] phủ kín hình chữ nhật. */
export type Rect = [number, number, number, number, number];

const worst = (areas: number[], side: number, total: number) =>
  Math.max((side * side * Math.max(...areas)) / (total * total), (total * total) / (side * side * Math.min(...areas)));

export function squarify(sizes: number[], x: number, y: number, w: number, h: number): Rect[] {
  const total = sizes.reduce((a, b) => a + b, 0);
  if (total <= 0 || w <= 0 || h <= 0) return [];
  const areas = sizes.map((s, i) => [i, (s / total) * w * h] as const);
  const out: Rect[] = [];
  while (areas.length) {
    const side = Math.min(w, h);
    const row: (readonly [number, number])[] = [];
    let tot = 0, best = Infinity;
    while (areas.length) {
      const cand = [...row.map((r) => r[1]), areas[0][1]];
      const wr = worst(cand, side, tot + areas[0][1]);
      if (row.length && wr > best) break;
      row.push(areas.shift()!);
      tot += row[row.length - 1][1];
      best = wr;
    }
    if (w >= h) {
      const rw = tot / h;
      let cy = y;
      for (const [i, a] of row) { const nh = a / rw; out.push([i, x, cy, rw, nh]); cy += nh; }
      x += rw; w -= rw;
    } else {
      const rh = tot / w;
      let cx = x;
      for (const [i, a] of row) { const nw = a / rh; out.push([i, cx, y, nw, rh]); cx += nw; }
      y += rh; h -= rh;
    }
  }
  return out;
}
