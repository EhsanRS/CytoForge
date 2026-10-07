import type { ReportElement } from "./types";

export type ReportArrangement =
  | "left"
  | "center"
  | "right"
  | "top"
  | "middle"
  | "bottom"
  | "distribute-horizontal"
  | "distribute-vertical"
  | "space-horizontal"
  | "space-vertical"
  | "group"
  | "ungroup"
  | "front"
  | "back";

interface Bounds {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export function reportObjectBounds(element: ReportElement): Bounds {
  const angle = (element.rotation * Math.PI) / 180;
  const halfWidth =
    (Math.abs(Math.cos(angle)) * element.width_mm +
      Math.abs(Math.sin(angle)) * element.height_mm) /
    2;
  const halfHeight =
    (Math.abs(Math.sin(angle)) * element.width_mm +
      Math.abs(Math.cos(angle)) * element.height_mm) /
    2;
  const x = element.x_mm + element.width_mm / 2;
  const y = element.y_mm + element.height_mm / 2;
  return {
    left: x - halfWidth,
    right: x + halfWidth,
    top: y - halfHeight,
    bottom: y + halfHeight,
  };
}

function union(bounds: Bounds[]): Bounds {
  return {
    left: Math.min(...bounds.map((value) => value.left)),
    right: Math.max(...bounds.map((value) => value.right)),
    top: Math.min(...bounds.map((value) => value.top)),
    bottom: Math.max(...bounds.map((value) => value.bottom)),
  };
}

/** A group on a prototype page moves as one unit, including unselected members. */
export function selectedReportUnits(
  elements: ReportElement[],
  selected: string[],
  page: number,
) {
  const picked = new Set(selected);
  const groups = new Map<string, ReportElement[]>();
  for (const element of elements) {
    if (element.page !== page) continue;
    const key = element.group_id ? `group:${element.group_id}` : element.id;
    const members = groups.get(key) ?? [];
    members.push(element);
    groups.set(key, members);
  }
  return [...groups.values()]
    .filter((members) => members.some((element) => picked.has(element.id)))
    .map((members) => ({
      members,
      locked: members.some((element) => element.position_locked),
      bounds: union(members.map(reportObjectBounds)),
    }));
}

/** Atomic, immutable changes; locked groups and other pages retain their geometry. */
export function arrangeReportElements(
  elements: ReportElement[],
  selected: string[],
  page: number,
  action: ReportArrangement,
  groupId?: string,
): ReportElement[] {
  const units = selectedReportUnits(elements, selected, page);
  const allPicked = new Set(
    units.flatMap((unit) => unit.members.map((item) => item.id)),
  );
  if (!allPicked.size) return elements;
  if (action === "group" || action === "ungroup") {
    if (action === "group" && !groupId)
      throw new Error("A new object group needs an identifier");
    const group = action === "group" ? groupId! : null;
    let changed = false;
    const next = elements.map((element) => {
      if (!allPicked.has(element.id) || element.group_id === group)
        return element;
      changed = true;
      return { ...element, group_id: group };
    });
    return changed ? next : elements;
  }
  if (action === "front" || action === "back") {
    const current = elements.filter((element) => element.page === page);
    const picked = current.filter((element) => allPicked.has(element.id));
    const others = current.filter((element) => !allPicked.has(element.id));
    const ordered =
      action === "front" ? [...others, ...picked] : [...picked, ...others];
    if (ordered.every((element, index) => element === current[index]))
      return elements;
    let index = 0;
    return elements.map((element) =>
      element.page === page ? ordered[index++] : element,
    );
  }

  const moving = units.filter((unit) => !unit.locked);
  const distribution =
    action.startsWith("distribute-") || action.startsWith("space-");
  if (moving.length < (distribution ? 3 : 2)) return elements;
  const vertical = [
    "top",
    "middle",
    "bottom",
    "distribute-vertical",
    "space-vertical",
  ].includes(action);
  const low = vertical ? "top" : "left";
  const high = vertical ? "bottom" : "right";
  const center = (bounds: Bounds) => (bounds[low] + bounds[high]) / 2;
  const span = union(moving.map((unit) => unit.bounds));
  const translations = new Map<string, number>();
  const translate = (unit: (typeof moving)[number], distance: number) => {
    for (const member of unit.members) translations.set(member.id, distance);
  };
  if (distribution) {
    const gaps = action.startsWith("space-");
    const sorted = [...moving].sort((a, b) =>
      gaps
        ? a.bounds[low] - b.bounds[low]
        : center(a.bounds) - center(b.bounds),
    );
    if (gaps) {
      const total = sorted.reduce(
        (sum, unit) => sum + unit.bounds[high] - unit.bounds[low],
        0,
      );
      const gap = (span[high] - span[low] - total) / (sorted.length - 1);
      let cursor = span[low];
      for (const unit of sorted) {
        translate(unit, cursor - unit.bounds[low]);
        cursor += unit.bounds[high] - unit.bounds[low] + gap;
      }
    } else {
      const first = center(sorted[0].bounds);
      const last = center(sorted[sorted.length - 1].bounds);
      sorted.forEach((unit, index) =>
        translate(
          unit,
          first +
            ((last - first) * index) / (sorted.length - 1) -
            center(unit.bounds),
        ),
      );
    }
  } else {
    const midpoint = action === "center" || action === "middle";
    const ending = action === "right" || action === "bottom";
    const target = midpoint ? center(span) : span[ending ? high : low];
    for (const unit of moving)
      translate(
        unit,
        target -
          (midpoint ? center(unit.bounds) : unit.bounds[ending ? high : low]),
      );
  }

  let changed = false;
  const next = elements.map((element) => {
    const distance = translations.get(element.id);
    if (distance === undefined || Math.abs(distance) < 1e-9) return element;
    const key = vertical ? "y_mm" : "x_mm";
    const coordinate = element[key] + distance;
    if (
      !Number.isFinite(coordinate) ||
      coordinate < -1e-9 ||
      coordinate > 1200 + 1e-9
    )
      throw new Error(
        "This arrangement would move an object outside the permitted position range (0–1,200 mm).",
      );
    changed = true;
    return { ...element, [key]: Math.min(1200, Math.max(0, coordinate)) };
  });
  return changed ? next : elements;
}
