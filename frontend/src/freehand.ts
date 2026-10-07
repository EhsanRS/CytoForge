export type TracePoint = [number, number];
export const FREEHAND_SPACING = 2;
export const FREEHAND_LIMIT = 2000;

// Sample the visible stroke at fixed CSS-pixel intervals. Saved vertices are
// mapped into the captured scientific coordinate basis without simplification.
export class FreehandTrace {
  pixels: TracePoint[];
  vertices: TracePoint[];
  length = 0;
  exceeded = false;
  private last: TracePoint;
  private remainder = 0;

  constructor(
    first: TracePoint,
    private worldAt: (point: TracePoint) => TracePoint,
  ) {
    this.last = first;
    this.pixels = [first];
    this.vertices = [worldAt(first)];
  }

  append(point: TracePoint) {
    if (this.exceeded || !point.every(Number.isFinite)) return;
    const [a, b] = this.last;
    const dx = point[0] - a,
      dy = point[1] - b;
    const distance = Math.hypot(dx, dy);
    if (!distance) return;
    this.length += distance;
    let offset = FREEHAND_SPACING - this.remainder;
    while (offset <= distance) {
      if (this.vertices.length >= FREEHAND_LIMIT) {
        this.exceeded = true;
        return;
      }
      const pixel: TracePoint = [
        a + (dx * offset) / distance,
        b + (dy * offset) / distance,
      ];
      this.pixels.push(pixel);
      this.vertices.push(this.worldAt(pixel));
      offset += FREEHAND_SPACING;
    }
    this.remainder = distance - (offset - FREEHAND_SPACING);
    this.last = point;
  }

  nearStart(point: TracePoint) {
    return (
      this.length >= 24 &&
      this.vertices.length >= 3 &&
      Math.hypot(point[0] - this.pixels[0][0], point[1] - this.pixels[0][1]) <=
        8
    );
  }

  hasArea() {
    if (this.vertices.length < 3) return false;
    const [a, b] = this.pixels[0];
    const other = this.pixels.find(([x, y]) => Math.hypot(x - a, y - b) >= 4);
    if (!other) return false;
    return this.pixels.some(
      ([x, y]) =>
        Math.abs((other[0] - a) * (y - b) - (other[1] - b) * (x - a)) >= 4,
    );
  }
}
