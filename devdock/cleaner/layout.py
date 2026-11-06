"""Thuật toán squarified treemap (không phụ thuộc GTK để dễ kiểm thử)."""


def _worst(areas, side, total):
    return max(side * side * max(areas) / (total * total), total * total / (side * side * min(areas)))


def squarify(sizes, x, y, w, h):
    """sizes: danh sách dung lượng đã sắp giảm dần (> 0). Trả về [(index, x, y, w, h)] phủ kín hình chữ nhật."""
    total = float(sum(sizes))
    if total <= 0 or w <= 0 or h <= 0:
        return []
    areas = [(i, s / total * w * h) for i, s in enumerate(sizes)]
    out = []
    while areas:
        side = min(w, h)
        row, tot, best = [], 0.0, float('inf')
        while areas:
            cand = [a for _, a in row] + [areas[0][1]]
            worst = _worst(cand, side, tot + areas[0][1])
            if row and worst > best:
                break
            row.append(areas.pop(0))
            tot += row[-1][1]
            best = worst
        if w >= h:
            rw = tot / h
            cy = y
            for i, a in row:
                nh = a / rw
                out.append((i, x, cy, rw, nh))
                cy += nh
            x += rw
            w -= rw
        else:
            rh = tot / w
            cx = x
            for i, a in row:
                nw = a / rh
                out.append((i, cx, y, nw, rh))
                cx += nw
            y += rh
            h -= rh
    return out
