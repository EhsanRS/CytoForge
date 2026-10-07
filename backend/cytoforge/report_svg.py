"""Self-contained publication SVG primitives; no user HTML or external resources."""

from __future__ import annotations

import copy
import json
import math
import re
from xml.etree import ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def clean_text(value):
    return "".join(
        c
        if ord(c) in {9, 10, 13}
        or 32 <= ord(c) <= 0xD7FF
        or 0xE000 <= ord(c) <= 0xFFFD
        or 0x10000 <= ord(c) <= 0x10FFFF
        else "�"
        for c in str(value)
    )


def node(tag, parent=None, **attributes):
    values = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("A report coordinate is not finite")
            value = format(value, ".12g")
        values[key.replace("_", "-")] = clean_text(value)
    return (
        ET.SubElement(parent, f"{{{SVG}}}{tag}", values)
        if parent is not None
        else ET.Element(f"{{{SVG}}}{tag}", values)
    )


def label(parent, x, y, value, size=3, color="#233449", **attributes):
    element = node(
        "text",
        parent,
        **dict(x=x, y=y, fill=color, font_size=size, font_family="sans-serif") | attributes,
    )
    element.text = clean_text(value)
    return element


def lines(value, width, size, family="sans-serif"):
    """Preserve paragraphs and break long words; layout is identical in every export."""
    capacity = max(1, int(width / (size * (0.62 if family == "monospace" else 0.56))))
    output = []
    for paragraph in clean_text(value).split("\n"):
        if not paragraph:
            output.append("")
            continue
        current = ""
        for word in paragraph.split():
            while len(word) > capacity:
                if current:
                    output.append(current)
                    current = ""
                output.append(word[:capacity])
                word = word[capacity:]
            candidate = f"{current} {word}".strip()
            if len(candidate) > capacity:
                output.append(current)
                current = word
            else:
                current = candidate
        if current:
            output.append(current)
    return output


def paragraph(
    parent,
    value,
    width,
    height,
    size=3,
    color="#233449",
    align="left",
    family="sans-serif",
    weight=400,
    *,
    style="normal",
    decoration="none",
    line_spacing=1.35,
):
    rows = lines(value, width, size, family)
    line_height = size * line_spacing
    visible = max(0, int(height / line_height))
    x = 0 if align == "left" else width / 2 if align == "center" else width
    anchor = "start" if align == "left" else "middle" if align == "center" else "end"
    for i, value in enumerate(rows[:visible]):
        label(
            parent,
            x,
            (i + 1) * line_height,
            value,
            size,
            color,
            text_anchor=anchor,
            font_family=family,
            font_weight=weight,
            font_style=style,
            text_decoration=decoration,
        )
    return max(0, len(rows) - visible)


def placeholder(width, height, message):
    svg = node("svg", viewBox=f"0 0 {width} {height}", width=width, height=height)
    node(
        "rect", svg, width=width, height=height, fill="#fff4f3", stroke="#b54340", stroke_width=0.4
    )
    label(svg, 3, 6, "Unavailable report source", 3.5, "#a23230", font_weight=700)
    group = node("g", svg, transform="translate(3 9)")
    paragraph(group, message, max(1, width - 6), max(1, height - 11), 2.7, "#a23230")
    return svg


def embed(parent, payload, width, height, prefix, light=False):
    inner = ET.fromstring(payload) if isinstance(payload, (str, bytes)) else copy.deepcopy(payload)
    allowed = {
        "svg",
        "g",
        "defs",
        "clipPath",
        "marker",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polygon",
        "polyline",
        "text",
        "tspan",
        "title",
        "desc",
        "use",
        "linearGradient",
        "stop",
    }
    identifiers = {
        element.get("id"): f"{prefix}-{element.get('id')}"
        for element in inner.iter()
        if element.get("id")
    }
    palette = {
        "#101827": "#ffffff",
        "#101b28": "#ffffff",
        "#172532": "#eef3f7",
        "#182334": "#eef3f7",
        "#243449": "#d7e1e9",
        "#283951": "#d7e1e9",
        "#bccbe1": "#52677e",
        "#e1ebf5": "#233449",
        "#f0f5ff": "#233449",
    }
    for parent_element in list(inner.iter()):
        for child in list(parent_element):
            if child.tag.rsplit("}", 1)[-1] not in allowed:
                parent_element.remove(child)
    for element in inner.iter():
        if element.tag.rsplit("}", 1)[-1] not in allowed:
            raise ValueError("Unsupported publication SVG element")
        if element.text:
            element.text = clean_text(element.text)
        # Matplotlib emits presentation properties as inline CSS. Convert its
        # supported properties to SVG attributes before rewriting local IDs.
        style = element.attrib.pop("style", "")
        presentation = {
            "fill",
            "fill-opacity",
            "stroke",
            "stroke-width",
            "stroke-opacity",
            "stroke-dasharray",
            "stroke-linejoin",
            "stroke-linecap",
            "opacity",
            "font-size",
            "font-family",
            "font-weight",
            "font-style",
            "text-anchor",
        }
        for declaration in style.split(";"):
            property_name, _, value = declaration.partition(":")
            if property_name.strip() in presentation:
                element.set(property_name.strip(), value.strip())
        for key, value in list(element.attrib.items()):
            local = key.rsplit("}", 1)[-1]
            if local.lower().startswith("on") or local == "style":
                del element.attrib[key]
                continue
            if local == "id":
                element.set(key, identifiers[value])
            elif local == "href":
                # HTML parses xlink:href specially, but arbitrary XML prefixes
                # (such as ns1:href) are plain attributes. SVG2 href works both
                # inline in Electron and as a standalone XML image.
                del element.attrib[key]
                if value.startswith("#") and value[1:] in identifiers:
                    element.set("href", f"#{identifiers[value[1:]]}")
            elif "url(" in value:
                reference = re.fullmatch(r"url\(#([^)]*)\)", value)
                if reference and reference[1] in identifiers:
                    element.set(key, f"url(#{identifiers[reference[1]]})")
                else:
                    del element.attrib[key]
            elif light and local in {"fill", "stroke"} and value.lower() in palette:
                element.set(key, palette[value.lower()])
    if "viewBox" not in inner.attrib:
        original_width = float(inner.get("width", width))
        original_height = float(inner.get("height", height))
        inner.set("viewBox", f"0 0 {original_width} {original_height}")
    inner.set("width", str(width))
    inner.set("height", str(height))
    inner.set("x", "0")
    inner.set("y", "0")
    inner.set("preserveAspectRatio", "xMidYMid meet")
    parent.append(inner)
    return inner


def serialize(svg):
    return ET.tostring(svg, encoding="unicode")


def metadata(svg, value):
    element = node("metadata", svg)
    element.text = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
