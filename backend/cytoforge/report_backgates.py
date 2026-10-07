"""Auditable highlights of finite backgate events inside the plotted population."""

HIGHLIGHT_COLOR = "#f0b96a"


def backgate_manifest(workspace, layer, payload):
    identifier = layer.get("backgate_id")
    if identifier is None:
        return {}
    gate = next(g for g in workspace.gates if g.id == identifier)
    count = payload["backgate_count"]
    denominator = payload["finite_count"]
    visible = payload["backgate_visible_count"]
    return {
        "backgate": dict(
            id=identifier,
            name=gate.name,
            color=HIGHLIGHT_COLOR,
            count=count,
            denominator=denominator,
            percent=100 * count / denominator if denominator else None,
            visible_count=visible,
            outside_view=count - visible,
            displayed_count=payload["backgate_displayed_count"],
            sampling=payload["backgate_sampling"],
            scope="Finite intersection with the plotted population; underlying bins unchanged",
            display="rug"
            if payload["y"] is None
            else "cloud"
            if payload["mode"] == "3d"
            else "points",
        )
    }
