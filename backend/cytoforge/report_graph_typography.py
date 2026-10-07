"""Physical-point graph fonts and measured publication layout."""

from __future__ import annotations

import io
import math

import matplotlib
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.backends.backend_svg import FigureCanvasSVG
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.path import Path
from matplotlib.textpath import TextToPath

from .report_svg import clean_text

MM_PER_PT = 25.4 / 72
FAMILIES = {"sans": "DejaVu Sans", "serif": "DejaVu Serif", "mono": "DejaVu Sans Mono"}


def text_style(definition, role, size, color):
    typography = definition.graph_options.typography
    style = getattr(typography, role, None) if typography else None
    return {
        "fontsize": style.font_size_pt if style and style.font_size_pt is not None else size,
        "fontfamily": FAMILIES[style.font_family or "sans"] if style else "DejaVu Sans",
        "fontweight": style.font_weight or "normal" if style else "normal",
        "fontstyle": style.font_style or "normal" if style else "normal",
        "color": style.color or color if style else color,
        "parse_math": False,
    }


def text_width(value, style):
    font = FontProperties(
        family=style["fontfamily"],
        weight=style["fontweight"],
        style=style["fontstyle"],
        size=style["fontsize"],
    )
    return RendererAgg(1, 1, 72).get_text_width_height_descent(clean_text(value), font, False)[0]


def plot_layout(definition, width, height, layer_count, y_labels=(), *, three_d=False):
    rows = math.ceil(layer_count / 2)
    legend = text_style(definition, "legend", 5.8, "#233449")
    stats = text_style(definition, "statistics", 5.8, "#233449")
    row_mm = max(3.5, max(legend["fontsize"], stats["fontsize"]) * 1.5 * MM_PER_PT)
    if any(layer.backgate_id for layer in [definition, *definition.overlays]):
        row_mm *= 2
    axis = text_style(definition, "axis_labels", 6 if three_d else 7, "#233449")
    tick = text_style(definition, "tick_labels", 4.5 if three_d else 6, "#52677e")
    bottom = (
        max(13, (axis["fontsize"] * 1.5 + tick["fontsize"] * 1.4) * MM_PER_PT + 3) + rows * row_mm
    )
    left, right, top = (
        (width * 0.03, width * 0.03, 3) if three_d else (width * 0.16, width * 0.04, 3)
    )
    typography = definition.graph_options.typography
    if typography and (typography.axis_labels or typography.tick_labels):
        if three_d:
            left = right = max(left, axis["fontsize"] * 1.5 * MM_PER_PT)
            top = max(top, tick["fontsize"] * 1.4 * MM_PER_PT)
        else:
            labels_width = max((text_width(value, tick) for value in y_labels), default=0)
            left = max(left, (labels_width + axis["fontsize"] * 1.5) * MM_PER_PT + 4)
            right = max(right, tick["fontsize"] * 2 * MM_PER_PT)
            top = max(top, tick["fontsize"] * MM_PER_PT)
    if height - bottom - top < 12 or width - left - right < 12:
        raise ValueError(
            "Increase this figure's width/height or reduce its fonts to fit the axes and legend"
        )
    return (
        [
            left / width,
            bottom / height,
            (width - left - right) / width,
            (height - bottom - top) / height,
        ],
        rows,
        row_mm,
    )


def draw_legend(fig, definition, manifest, width, height, rows, row_mm):
    typography = definition.graph_options.typography
    styled = typography and (typography.legend or typography.statistics)
    backgates = any(layer.backgate_id for layer in [definition, *definition.overlays])
    for index, item in enumerate(manifest):
        column, row = index % 2, index // 2
        label = clean_text(item["label"])
        suffix = " · control" if item.get("locked_control") else ""
        count = f" · n={item['population_count']:,}{suffix}"
        x = 0.05 + column * 0.49
        y = 0.035 + (rows - row - 1) * row_mm / height
        if backgates:
            if backgate := item.get("backgate"):
                backgate_label = "Backgate: " + clean_text(backgate["name"])
                backgate_count = (
                    f" · {backgate['count']:,} finite; {backgate['displayed_count']:,} shown"
                )
                style = text_style(definition, "legend", 5.8, backgate["color"])
                statistics = text_style(definition, "statistics", 5.8, backgate["color"])
                capacity = width * 0.43 / MM_PER_PT
                count_width = text_width(backgate_count, statistics)
                if count_width + text_width("…", style) > capacity:
                    raise ValueError(
                        "Increase figure width or reduce statistics fonts "
                        "to fit the backgate legend"
                    )
                while backgate_label and text_width(backgate_label, style) + count_width > capacity:
                    backgate_label = (
                        backgate_label[:-2].rstrip("…") + "…" if len(backgate_label) > 1 else ""
                    )
                fig.text(x, y, backgate_label, **style)
                fig.text(
                    x + text_width(backgate_label, style) * MM_PER_PT / width,
                    y,
                    backgate_count,
                    **statistics,
                )
            y += row_mm / 2 / height
        if not styled:
            capacity = max(10, int(width * 0.6))
            if len(label) > capacity:
                label = label[: capacity - 1] + "…"
            fig.text(x, y, label + count, fontsize=5.8, color=item["color"], parse_math=False)
            continue
        name_style = text_style(definition, "legend", 5.8, item["color"])
        count_style = text_style(definition, "statistics", 5.8, item["color"])
        available_pt = width * 0.43 / MM_PER_PT
        count_pt = text_width(count, count_style)
        if count_pt + text_width("…", name_style) > available_pt:
            raise ValueError(
                "Increase this figure's width or reduce its statistics font to fit its legend"
            )
        while label and text_width(label, name_style) + count_pt > available_pt:
            label = label[:-2].rstrip("…") + "…" if len(label) > 1 else ""
        fig.text(x, y, label, **name_style)
        fig.text(x + text_width(label, name_style) * MM_PER_PT / width, y, count, **count_style)


def typography_manifest(definition):
    settings = definition.graph_options.typography
    if not settings or not settings.model_dump():
        return {}
    return {
        "typography": settings.model_dump(),
        "typography_units": "pt",
        "font_families": FAMILIES,
    }


def caption_style(element):
    result = dict(
        size=element.font_size_pt,
        color=element.color,
        family=element.font_family,
        weight=element.font_weight,
        style=element.font_style,
    )
    if element.kind == "plot" and element.plot:
        typography = element.plot.graph_options.typography
        style = typography.title if typography else None
        if style:
            for source, target in [
                ("font_size_pt", "size"),
                ("color", "color"),
                ("font_weight", "weight"),
                ("font_style", "style"),
            ]:
                value = getattr(style, source)
                if value is not None:
                    result[target] = value
            if style.font_family is not None:
                result["family"] = FAMILIES[style.font_family]
            if style.font_weight is not None:
                result["weight"] = 700 if style.font_weight == "bold" else 400
    return result


def styled_caption(element):
    settings = (
        element.plot.graph_options.typography if element.kind == "plot" and element.plot else None
    )
    return bool(settings and settings.title and settings.title.model_dump())


def caption_lines(value, width, style):
    font = {
        "fontsize": style["size"],
        "fontfamily": style["family"],
        "fontweight": style["weight"],
        "fontstyle": style["style"],
    }
    result = []
    for paragraph in clean_text(value).split("\n"):
        line = ""
        for word in paragraph.split():
            if text_width(word, font) * MM_PER_PT > width:
                if line:
                    result.append(line)
                    line = ""
                for character in word:
                    if line and text_width(line + character, font) * MM_PER_PT > width:
                        result.append(line)
                        line = character
                    else:
                        line += character
                continue
            candidate = f"{line} {word}".strip()
            if line and text_width(candidate, font) * MM_PER_PT > width:
                result.append(line)
                line = word
            else:
                line = candidate
        result.append(line)
    return result


def caption_descent(rows, style):
    font = FontProperties(
        family=style["family"], weight=style["weight"], style=style["style"], size=style["size"]
    )
    converter = TextToPath()
    descent = 0
    for row in rows:
        vertices, codes = converter.get_text_path(font, row, ismath=False)
        if len(vertices):
            descent = max(descent, -Path(vertices, codes).get_extents().ymin)
    return descent * style["size"] / converter.FONT_SCALE * MM_PER_PT


def caption_figure(value, element, width, height):
    # Explicit graph title fonts become glyph paths, so PDF/SVG recipients need no installed fonts.
    from .report_plots import _MPL_LOCK

    style = caption_style(element)
    line_mm = style["size"] * MM_PER_PT * element.line_spacing
    rows = caption_lines(value, width, style)
    visible = min(len(rows), max(0, int(height / line_mm)))
    while visible and visible * line_mm + caption_descent(rows[:visible], style) > height:
        visible -= 1
    x = 0 if element.align == "left" else 0.5 if element.align == "center" else 1
    with (
        _MPL_LOCK,
        matplotlib.rc_context({"svg.fonttype": "path", "svg.hashsalt": "cytoforge-caption"}),
    ):
        figure = Figure(figsize=(width / 25.4, height / 25.4), facecolor="none")
        canvas = FigureCanvasSVG(figure)
        for index, row in enumerate(rows[:visible]):
            y = 1 - (index + 1) * line_mm / height
            figure.text(
                x,
                1 - (index + 1) * line_mm / height,
                row,
                ha=element.align,
                fontsize=style["size"],
                fontfamily=style["family"],
                fontweight=style["weight"],
                fontstyle=style["style"],
                color=style["color"],
                parse_math=False,
            )
            if element.text_decoration != "none":
                font = dict(
                    fontsize=style["size"],
                    fontfamily=style["family"],
                    fontweight=style["weight"],
                    fontstyle=style["style"],
                )
                length = text_width(row, font) * MM_PER_PT / width
                start = (
                    x
                    if element.align == "left"
                    else x - length / 2
                    if element.align == "center"
                    else x - length
                )
                offset = -0.12 if element.text_decoration == "underline" else 0.3
                baseline = y + offset * style["size"] * MM_PER_PT / height
                figure.add_artist(
                    Line2D(
                        [start, start + length],
                        [baseline, baseline],
                        transform=figure.transFigure,
                        color=style["color"],
                        linewidth=max(0.4, style["size"] * 0.05),
                    )
                )
        output = io.StringIO()
        canvas.print_svg(output, metadata={"Date": None, "Creator": "CytoForge"})
        figure.clear()
    return output.getvalue(), max(0, len(rows) - visible)
