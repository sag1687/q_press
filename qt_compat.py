"""Small Qt5/Qt6 compatibility helpers for the QGIS PyQt shim."""


def _alias(container, legacy_name, enum_group, enum_name):
    if hasattr(container, legacy_name) or not hasattr(container, enum_group):
        return
    group = getattr(container, enum_group)
    if hasattr(group, enum_name):
        setattr(container, legacy_name, getattr(group, enum_name))


def ensure_qt_compat(qt):
    """Restore the Qt5-style enum aliases used by QGIS 3 plugins."""
    aliases = (
        ("AlignCenter", "AlignmentFlag", "AlignCenter"),
        ("AlignHCenter", "AlignmentFlag", "AlignHCenter"),
        ("AlignLeft", "AlignmentFlag", "AlignLeft"),
        ("AlignRight", "AlignmentFlag", "AlignRight"),
        ("AlignTop", "AlignmentFlag", "AlignTop"),
        ("AlignVCenter", "AlignmentFlag", "AlignVCenter"),
        ("ArrowCursor", "CursorShape", "ArrowCursor"),
        ("CrossCursor", "CursorShape", "CrossCursor"),
        ("DashLine", "PenStyle", "DashLine"),
        ("ItemIsEditable", "ItemFlag", "ItemIsEditable"),
        ("KeepAspectRatio", "AspectRatioMode", "KeepAspectRatio"),
        ("LeftArrow", "ArrowType", "LeftArrow"),
        ("LeftButton", "MouseButton", "LeftButton"),
        ("RightArrow", "ArrowType", "RightArrow"),
        ("RichText", "TextFormat", "RichText"),
        ("ShiftModifier", "KeyboardModifier", "ShiftModifier"),
        ("SmoothTransformation", "TransformationMode", "SmoothTransformation"),
        ("TextWordWrap", "TextFlag", "TextWordWrap"),
        ("UserRole", "ItemDataRole", "UserRole"),
        ("WindowModal", "WindowModality", "WindowModal"),
    )
    for legacy_name, enum_group, enum_name in aliases:
        _alias(qt, legacy_name, enum_group, enum_name)
    return qt


def ensure_qfont_compat(qfont):
    if hasattr(qfont, "Weight"):
        if not hasattr(qfont, "Bold") and hasattr(qfont.Weight, "Bold"):
            setattr(qfont, "Bold", qfont.Weight.Bold)
        if not hasattr(qfont, "Normal") and hasattr(qfont.Weight, "Normal"):
            setattr(qfont, "Normal", qfont.Weight.Normal)
    return qfont


def ensure_qimage_compat(qimage):
    if not hasattr(qimage, "Format_ARGB32") and hasattr(qimage, "Format"):
        if hasattr(qimage.Format, "Format_ARGB32"):
            setattr(qimage, "Format_ARGB32", qimage.Format.Format_ARGB32)
    return qimage


def ensure_qpainter_compat(qpainter):
    if hasattr(qpainter, "RenderHint"):
        if not hasattr(qpainter, "Antialiasing") and hasattr(
            qpainter.RenderHint, "Antialiasing"
        ):
            setattr(qpainter, "Antialiasing", qpainter.RenderHint.Antialiasing)
        if not hasattr(qpainter, "TextAntialiasing") and hasattr(
            qpainter.RenderHint, "TextAntialiasing"
        ):
            setattr(
                qpainter,
                "TextAntialiasing",
                qpainter.RenderHint.TextAntialiasing,
            )
    return qpainter


def ensure_qdialog_compat(qdialog):
    if hasattr(qdialog, "DialogCode"):
        if not hasattr(qdialog, "Accepted") and hasattr(
            qdialog.DialogCode, "Accepted"
        ):
            setattr(qdialog, "Accepted", qdialog.DialogCode.Accepted)
        if not hasattr(qdialog, "Rejected") and hasattr(
            qdialog.DialogCode, "Rejected"
        ):
            setattr(qdialog, "Rejected", qdialog.DialogCode.Rejected)
    return qdialog


def ensure_selection_mode_compat(widget_class):
    if not hasattr(widget_class, "SelectionMode"):
        return widget_class
    for name in (
        "NoSelection",
        "SingleSelection",
        "MultiSelection",
        "ExtendedSelection",
    ):
        if not hasattr(widget_class, name) and hasattr(
            widget_class.SelectionMode, name
        ):
            setattr(
                widget_class, name, getattr(widget_class.SelectionMode, name)
            )
    return widget_class
