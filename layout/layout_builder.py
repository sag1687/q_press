import math
import os
import re
import shutil
import tempfile
import traceback
from datetime import datetime
from qgis.core import (
    QgsProject,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemMapGrid,
    QgsLayoutItemPicture,
    QgsLayoutItemLegend,
    QgsLayoutItemScaleBar,
    QgsLayoutItemMapOverview,
    QgsLayoutItemPage,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutMeasurement,
    QgsUnitTypes,
    QgsTextFormat,
    QgsFillSymbol,
    QgsMessageLog,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMapLayerType,
    QgsPointXY,
    QgsRectangle,
    QgsLayoutFrame,
    QgsLayoutItemAttributeTable,
    QgsLayoutMultiFrame,
    QgsWkbTypes,
    Qgis
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from .chart_generator import build_dashboard_images
from .pdf_exporter import export_to_pdf
from .topographic_profile import DATASET_STACK, OPEN_TOPO_DATA_METHOD, build_topographic_profile_images


def safe_filename(name):
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name)


def _mm(value):
    return QgsLayoutMeasurement(value, QgsUnitTypes.LayoutMillimeters)


def _color_to_rgba(color):
    return f"{color.red()},{color.green()},{color.blue()},{color.alpha()}"


def _set_item_frame(item, width_mm, color):
    item.setFrameEnabled(True)
    item.setFrameStrokeWidth(_mm(width_mm))
    try:
        item.setFrameStrokeColor(color)
    except AttributeError:
        pass


def _add_rectangle(layout, x, y, w, h, fill_color, border_color, border_mm=0.2):
    shape = QgsLayoutItemShape(layout)
    shape.setShapeType(QgsLayoutItemShape.Rectangle)
    shape.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    shape.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    symbol = QgsFillSymbol.createSimple(
        {
            "style": "solid",
            "color": _color_to_rgba(fill_color),
            "outline_color": _color_to_rgba(border_color),
            "outline_width": str(border_mm),
        }
    )
    shape.setSymbol(symbol)
    layout.addLayoutItem(shape)
    return shape


def _add_label(layout, text, x, y, w, h, font_size, bold, color, halign=Qt.AlignLeft, valign=Qt.AlignVCenter):
    item = QgsLayoutItemLabel(layout)
    item.setText(text)
    item.setFont(QFont("Arial", font_size, QFont.Bold if bold else QFont.Normal))
    item.setFontColor(color)
    item.setHAlign(halign)
    item.setVAlign(valign)
    item.setMarginX(1.2)
    item.setMarginY(0.8)
    item.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    item.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(item)
    return item


def _estimated_char_capacity(box_width_mm, font_size, margin_mm=2.4):
    usable = max(box_width_mm - margin_mm, 1.0)
    avg_char_mm = max(font_size * 0.19, 0.9)
    return max(int(usable / avg_char_mm), 4)


def _estimated_long_word_width(word, font_size):
    return len(str(word)) * max(font_size * 0.24, 1.0)


def _wrap_text_for_box(text, box_width_mm, font_size):
    capacity = _estimated_char_capacity(box_width_mm, font_size)
    lines = []
    for paragraph in str(text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= capacity:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return "\n".join(lines), len(lines)


def _fit_text_for_box(text, box_width_mm, box_height_mm, base_size, min_size, bold=False):
    raw = str(text or "")
    min_size = max(int(min_size), 5)
    for size in range(int(base_size), min_size - 1, -1):
        longest_word = max((word for word in raw.replace("\n", " ").split()), key=len, default="")
        if _estimated_long_word_width(longest_word, size) > max(box_width_mm - 2.4, 1.0):
            continue
        wrapped, line_count = _wrap_text_for_box(raw, box_width_mm, size)
        line_height = max(size * 0.42, 2.8)
        if (line_count * line_height) <= max(box_height_mm - 1.0, line_height):
            return wrapped, size, line_count

    wrapped, line_count = _wrap_text_for_box(raw, box_width_mm, min_size)
    max_lines = max(int(max(box_height_mm - 1.0, 1.0) / max(min_size * 0.42, 2.8)), 1)
    lines = wrapped.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = _append_ellipsis_word_safe(lines[-1], _estimated_char_capacity(box_width_mm, min_size))
    return "\n".join(lines), min_size, min(len(lines), max_lines)


def _append_ellipsis_word_safe(text, capacity):
    clean = " ".join(str(text or "").split())
    if len(clean) <= max(capacity - 3, 1):
        return f"{clean}..."
    words = clean.split()
    output = ""
    for word in words:
        candidate = word if not output else f"{output} {word}"
        if len(candidate) > max(capacity - 3, 1):
            break
        output = candidate
    return f"{output}..." if output else "..."


def _add_fitted_label(
    layout,
    text,
    x,
    y,
    w,
    h,
    font_size,
    bold,
    color,
    halign=Qt.AlignLeft,
    valign=Qt.AlignVCenter,
    min_size=7,
):
    fitted_text, fitted_size, _line_count = _fit_text_for_box(text, w, h, font_size, min_size, bold)
    return _add_label(layout, fitted_text, x, y, w, h, fitted_size, bold, color, halign, valign)


def _text_fits_single_line(text, box_width_mm, font_size):
    raw = " ".join(str(text or "").split())
    if not raw:
        return True
    capacity = _estimated_char_capacity(box_width_mm, font_size)
    longest_word = max(raw.split(), key=len, default="")
    return len(raw) <= capacity and _estimated_long_word_width(longest_word, font_size) <= max(box_width_mm - 2.4, 1.0)


def _apply_grid_text_format(grid, font_size, color):
    font = QFont("Arial", font_size)
    try:
        text_format = QgsTextFormat()
        text_format.setFont(font)
        text_format.setColor(color)
        grid.setAnnotationTextFormat(text_format)
    except AttributeError:
        grid.setAnnotationFont(font)
        try:
            grid.setAnnotationFontColor(color)
        except AttributeError:
            pass


def _nice_step(raw_value):
    if raw_value <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw_value))
    fraction = raw_value / (10 ** exponent)
    if fraction < 1.5:
        base = 1
    elif fraction < 3:
        base = 2
    elif fraction < 7:
        base = 5
    else:
        base = 10
    return base * (10 ** exponent)


def _nice_floor_step(raw_value):
    if raw_value <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw_value))
    for exp in range(exponent + 1, exponent - 5, -1):
        for base in (5, 2, 1):
            candidate = base * (10 ** exp)
            if candidate <= raw_value:
                return max(candidate, 1.0)
    return 1.0


def calculate_grid_interval(rect):
    target_lines = 7.0
    width = rect.width()
    if width <= 0:
        return 1000.0
    return _nice_step(width / target_lines)


def _calculate_degree_interval(project_interval):
    interval_deg = project_interval / 111320.0
    steps = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]
    for step in steps:
        if interval_deg <= step:
            return step
    return 10.0


def get_page_dimensions(fmt, orientation):
    formats = {"A4": (297, 210), "A3": (420, 297), "A0": (1189, 841)}
    w, h = formats.get(fmt, (297, 210))
    if orientation == "Portrait":
        return h, w
    return w, h


def get_layout_metrics(fmt):
    if fmt == "A0":
        return {
            "margin": 20.0,
            "gap": 6.0,
            "panel": 400.0,
            "panel_bottom": 230.0,
            "title": 28,
            "meta": 14,
            "small": 12,
            "legend": 18,
            "north": 28.0,
            "scale_w": 120.0,
            "scale_h": 8.0,
        }
    if fmt == "A3":
        return {
            "margin": 12.0,
            "gap": 4.0,
            "panel": 152.0,
            "panel_bottom": 96.0,
            "title": 18,
            "meta": 11,
            "small": 9,
            "legend": 12,
            "north": 16.0,
            "scale_w": 78.0,
            "scale_h": 5.5,
        }
    return {
        "margin": 10.0,
        "gap": 3.0,
        "panel": 112.0,
        "panel_bottom": 82.0,
        "title": 13,
        "meta": 9,
        "small": 8,
        "legend": 10,
        "north": 12.0,
        "scale_w": 62.0,
        "scale_h": 4.5,
    }


def _buffered_rect(rect, distance):
    try:
        return rect.buffered(distance)
    except AttributeError:
        expanded = QgsRectangle(rect)
        expanded.grow(distance)
        return expanded


def _resolve_extent(layer, rect):
    if rect and not rect.isEmpty() and rect.isFinite():
        base = rect
    else:
        base = layer.extent()
    if not base or base.isEmpty() or not base.isFinite():
        return layer.extent()
    padding = max(base.width(), base.height()) * 0.03
    return _buffered_rect(base, padding) if padding > 0 else base


def _format_scale(value):
    return f"{int(round(value)):,}".replace(",", ".")


def _scale_value_text(scale_value):
    try:
        if scale_value and scale_value > 0:
            return f"1:{_format_scale(scale_value)}"
    except Exception:
        pass
    return "1:n/d"


def _format_coord(value):
    abs_v = abs(value)
    if abs_v >= 100000:
        decimals = 0
    elif abs_v >= 1000:
        decimals = 1
    else:
        decimals = 3
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _text(lang, italian, english):
    return english if lang == "en" else italian


def _adaptive_font_size(text, box_width_mm, base_size, min_size):
    if not text:
        return base_size
    # Stima semplice: larghezza media carattere ~0.45 mm per punto
    avg_char_mm = max(base_size * 0.45, 1.0)
    capacity = max(int(box_width_mm / avg_char_mm), 4)
    overflow = max(len(text) - capacity, 0)
    shrink = int(math.ceil(overflow / max(capacity, 1)))
    return max(min_size, base_size - shrink)


def _build_metadata_text(layer_name, crs_authid, extent, scale_value, lang="it"):
    return (
        f"{_text(lang, 'Layer', 'Layer')}: {layer_name}\n"
        f"CRS: {crs_authid}\n"
        f"X min/max: {_format_coord(extent.xMinimum())} / {_format_coord(extent.xMaximum())}\n"
        f"Y min/max: {_format_coord(extent.yMinimum())} / {_format_coord(extent.yMaximum())}\n"
        f"{_text(lang, 'Data', 'Date')}: {datetime.now().strftime('%d/%m/%Y')}"
    )


def _metadata_rows(layer_name, crs_authid, extent, lang="it"):
    return [
        (_text(lang, "Layer", "Layer"), layer_name),
        ("CRS", crs_authid),
        ("X", f"{_format_coord(extent.xMinimum())} / {_format_coord(extent.xMaximum())}"),
        ("Y", f"{_format_coord(extent.yMinimum())} / {_format_coord(extent.yMaximum())}"),
        (_text(lang, "Data", "Date"), datetime.now().strftime("%d/%m/%Y")),
    ]


def _add_section_frame(layout, title, x, y, w, h, font_size, lang="it"):
    if w <= 12.0 or h <= 8.0:
        return None
    ink = QColor(17, 24, 39)
    header = QColor(241, 245, 249)
    _add_rectangle(layout, x, y, w, h, QColor(255, 255, 255), ink, 0.18)
    header_h = min(max(font_size * 0.86, 5.0), max(h * 0.28, 4.5))
    _add_rectangle(layout, x, y, w, header_h, header, ink, 0.0)
    _add_fitted_label(
        layout,
        str(title or "").upper(),
        x + 1.2,
        y,
        max(w - 2.4, 1.0),
        header_h,
        max(font_size, 7),
        True,
        ink,
        Qt.AlignLeft,
        Qt.AlignVCenter,
        min_size=6,
    )
    pad = 1.4
    return x + pad, y + header_h + pad, max(w - (2.0 * pad), 1.0), max(h - header_h - (2.0 * pad), 1.0)


def _add_key_value_table(layout, rows, x, y, w, h, font_size):
    clean_rows = [(str(key), str(value or "")) for key, value in rows if str(value or "").strip()]
    if not clean_rows or w <= 18.0 or h <= 8.0:
        return
    ink = QColor(17, 24, 39)
    muted = QColor(71, 85, 105)
    row_h = h / float(len(clean_rows))
    key_w = min(max(w * 0.30, 18.0), w * 0.42)
    for index, (key, value) in enumerate(clean_rows):
        row_y = y + (index * row_h)
        if index % 2 == 0:
            _add_rectangle(layout, x, row_y, w, row_h, QColor(248, 250, 252), QColor(248, 250, 252), 0.0)
        if index > 0:
            _add_rectangle(layout, x, row_y, w, 0.08, QColor(226, 232, 240), QColor(226, 232, 240), 0.0)
        _add_fitted_label(
            layout,
            key,
            x + 0.8,
            row_y,
            key_w - 1.2,
            row_h,
            max(font_size - 1, 7),
            True,
            muted,
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=6,
        )
        _add_fitted_label(
            layout,
            value,
            x + key_w,
            row_y,
            max(w - key_w - 0.8, 1.0),
            row_h,
            max(font_size, 7),
            False,
            ink,
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=6,
        )


def _compact_attribute_fields(layer, max_fields):
    fields = [field.name() for field in layer.fields() if field.name().lower() != "geometry"]
    priority_tokens = ("id", "name", "nome", "cod", "tipo", "class")
    priority = []
    regular = []
    for field_name in fields:
        lower = field_name.lower()
        if any(token in lower for token in priority_tokens):
            priority.append(field_name)
        else:
            regular.append(field_name)
    ordered = priority + regular
    return ordered[:max(max_fields, 0)], len(fields)


def _value_text(value, max_chars=34):
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return _append_ellipsis_word_safe(text, max_chars)


def _add_compact_attribute_block(layout, layer, features, x, y, w, h, font_size, lang="it", show_title=True):
    if layer.type() != QgsMapLayerType.VectorLayer or not features or w <= 20.0 or h <= 16.0:
        return False

    title_h = max(font_size * 0.75, 4.2) if show_title else 0.0
    line_h = max(font_size * 0.62, 3.6)
    max_lines = int((h - title_h - (1.0 if show_title else 0.0)) / line_h)
    if max_lines < 2:
        return False

    max_fields = max_lines
    fields, total_fields = _compact_attribute_fields(layer, max_fields)
    if not fields:
        return False

    row_font_size = max(font_size - 1, 7)
    feature = features[0]
    raw_rows = []
    for field_name in fields[:max_lines]:
        try:
            value = feature[field_name]
        except Exception:
            value = ""
        full_value = "" if value is None else " ".join(str(value).split())
        full_row = f"{field_name}: {full_value}"
        raw_rows.append((field_name, full_value, full_row))

    complete = len(features) == 1 and total_fields <= len(fields)
    if complete:
        complete = all(_text_fits_single_line(row_text, w, row_font_size) for _field, _value, row_text in raw_rows)

    title = _text(lang, "ATTRIBUTI", "ATTRIBUTES")
    if not complete:
        title = _text(
            lang,
            f"ATTRIBUTI - {len(features)} record (estratto)",
            f"ATTRIBUTES - {len(features)} records (excerpt)",
        )

    if show_title:
        _add_fitted_label(
            layout,
            title,
            x,
            y,
            w,
            title_h,
            max(font_size + 1, 8),
            True,
            QColor(17, 24, 39),
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=max(font_size - 1, 7),
        )
        _add_rectangle(layout, x, y + title_h + 0.6, w, 0.18, QColor(17, 24, 39), QColor(17, 24, 39), 0.0)

    cursor_y = y + title_h + (1.2 if show_title else 0.0)
    row_capacity = max(_estimated_char_capacity(w, row_font_size), 12)
    for field_name, full_value, full_row in raw_rows:
        row_text = full_row if complete else f"{field_name}: {_value_text(full_value, row_capacity)}"
        _add_fitted_label(
            layout,
            row_text,
            x,
            cursor_y,
            w,
            line_h,
            row_font_size,
            False,
            QColor(31, 41, 55),
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=6,
        )
        cursor_y += line_h

    return complete


def _add_ruler_icon(layout, x, y, w, h, ink):
    if w <= 2.0 or h <= 2.0:
        return
    body_h = max(h * 0.42, 2.2)
    body_y = y + ((h - body_h) / 2.0)
    _add_rectangle(layout, x, body_y, w, body_h, QColor(255, 255, 255), ink, 0.16)
    segment_w = w / 4.0
    for index in range(4):
        fill = ink if index % 2 == 0 else QColor(255, 255, 255)
        _add_rectangle(layout, x + (index * segment_w), body_y, segment_w, body_h, fill, ink, 0.08)
    for index in range(5):
        tick_h = body_h * (0.95 if index in (0, 4) else 0.60)
        tick_w = max(w * 0.018, 0.12)
        tick_x = min(max(x + (index * segment_w) - (tick_w / 2.0), x), x + w - tick_w)
        _add_rectangle(layout, tick_x, body_y - tick_h * 0.65, tick_w, tick_h, ink, ink, 0.0)


def _add_scale_indicator(layout, x, y, w, h, scale_value, font_size, lang="it"):
    if w <= 30.0 or h <= 6.0:
        return None
    ink = QColor(17, 24, 39)
    muted = QColor(71, 85, 105)
    bg = QColor(248, 250, 252)
    _add_rectangle(layout, x, y, w, h, bg, ink, 0.18)

    pad = max(min(h * 0.18, 2.0), 1.0)
    icon_w = min(max(h * 1.65, 9.0), max(w * 0.24, 9.0))
    icon_h = max(h - (2.0 * pad), 3.0)
    _add_ruler_icon(layout, x + pad, y + pad, icon_w, icon_h, ink)

    text_x = x + pad + icon_w + max(pad, 1.2)
    text_w = max(w - (text_x - x) - pad, 8.0)
    scale_text = _scale_value_text(scale_value)
    if h >= 10.0 and text_w >= 24.0:
        label_h = min(max(h * 0.40, 3.8), h - 4.0)
        _add_fitted_label(
            layout,
            _text(lang, "SCALA APPLICATA", "APPLIED SCALE"),
            text_x,
            y + 0.3,
            text_w,
            label_h,
            max(font_size - 1, 7),
            True,
            muted,
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=6,
        )
        _add_fitted_label(
            layout,
            scale_text,
            text_x,
            y + label_h,
            text_w,
            h - label_h - 0.4,
            max(font_size + 2, 9),
            True,
            ink,
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=7,
        )
    else:
        _add_fitted_label(
            layout,
            f"{_text(lang, 'Scala', 'Scale')} {scale_text}",
            text_x,
            y,
            text_w,
            h,
            max(font_size, 7),
            True,
            ink,
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=6,
        )
    return scale_text


def _configure_map_item(layout, map_item, extent, map_settings, crs):
    map_item.setExtent(extent)
    map_item.setCrs(crs)

    if map_settings:
        try:
            layers = map_settings.layers()
            if layers:
                map_item.setLayers(layers)
                map_item.setKeepLayerSet(True)
        except Exception:
            pass
        try:
            map_item.setMapRotation(map_settings.rotation())
        except Exception:
            pass

    _set_item_frame(map_item, 0.4, QColor(15, 23, 42))
    try:
        map_item.setBackgroundColor(QColor(255, 255, 255))
    except AttributeError:
        pass
    layout.addLayoutItem(map_item)


def _configure_primary_grid(map_item, interval, font_size):
    grid = QgsLayoutItemMapGrid("Primary Grid", map_item)
    grid.setEnabled(True)
    grid.setIntervalX(interval)
    grid.setIntervalY(interval)
    grid.setStyle(QgsLayoutItemMapGrid.Solid)
    grid.setGridLineColor(QColor(55, 65, 81, 120))
    grid.setGridLineWidth(0.10)
    grid.setFrameStyle(QgsLayoutItemMapGrid.Zebra)
    try:
        grid.setFramePenSize(0.2)
        grid.setFramePenColor(QColor(17, 24, 39))
        grid.setFrameFillColor1(QColor(255, 255, 255))
        grid.setFrameFillColor2(QColor(229, 231, 235))
    except Exception:
        pass
    grid.setAnnotationEnabled(True)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.Decimal)
    grid.setAnnotationPrecision(0 if interval >= 10 else 1)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Bottom)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Right)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Top)
    try:
        grid.setAnnotationFrameDistance(1.5)
    except AttributeError:
        pass
    _apply_grid_text_format(grid, font_size, QColor(30, 41, 59))
    map_item.grids().addGrid(grid)


def _configure_secondary_grid(map_item, project_crs_authid, interval, font_size):
    if project_crs_authid == "EPSG:4326":
        return

    grid = QgsLayoutItemMapGrid("WGS84 Grid", map_item)
    grid.setEnabled(True)
    grid.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    degree_interval = _calculate_degree_interval(interval)
    grid.setIntervalX(degree_interval)
    grid.setIntervalY(degree_interval)
    grid.setStyle(QgsLayoutItemMapGrid.Cross)
    grid.setCrossLength(0.9)
    grid.setGridLineColor(QColor(107, 114, 128, 90))
    grid.setGridLineWidth(0.1)
    grid.setAnnotationEnabled(False)
    grid.setAnnotationFormat(QgsLayoutItemMapGrid.DegreeMinuteSecond)
    grid.setAnnotationPrecision(0)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Left)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Bottom)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Right)
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.ShowAll, QgsLayoutItemMapGrid.Top)
    try:
        grid.setAnnotationFrameDistance(1.5)
    except AttributeError:
        pass
    _apply_grid_text_format(grid, max(font_size - 1, 6), QColor(71, 85, 105))
    map_item.grids().addGrid(grid)


def _selection_rect_for_layer(layer, rect, map_settings):
    if not rect or rect.isEmpty() or not rect.isFinite():
        return None

    source_crs = QgsProject.instance().crs()
    if map_settings:
        try:
            source_crs = map_settings.destinationCrs()
        except Exception:
            source_crs = QgsProject.instance().crs()

    layer_crs = layer.crs()
    if not source_crs.isValid() or not layer_crs.isValid() or source_crs == layer_crs:
        return rect

    try:
        transform = QgsCoordinateTransform(source_crs, layer_crs, QgsProject.instance())
        return transform.transformBoundingBox(rect)
    except Exception:
        return rect


def _features_in_selection(layer, rect, map_settings):
    if layer.type() != QgsMapLayerType.VectorLayer:
        return []

    layer_rect = _selection_rect_for_layer(layer, rect, map_settings)
    if layer_rect is None:
        return []

    request = QgsFeatureRequest().setFilterRect(layer_rect)
    rect_geom = QgsGeometry.fromRect(layer_rect)
    selected = []
    for feature in layer.getFeatures(request):
        geom = feature.geometry()
        if not geom or geom.isEmpty():
            continue
        if geom.intersects(rect_geom):
            selected.append(feature)
    return selected


def _map_crs_from_settings(map_settings):
    if map_settings:
        try:
            crs = map_settings.destinationCrs()
            if crs.isValid():
                return crs
        except Exception:
            pass
    return QgsProject.instance().crs()


def _as_point_xy(point):
    if isinstance(point, QgsPointXY):
        return point
    return QgsPointXY(point)


def _transform_points(points, source_crs, target_crs):
    clean_points = [_as_point_xy(point) for point in points if point is not None]
    if not target_crs or not target_crs.isValid() or source_crs == target_crs:
        return clean_points
    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    return [transform.transform(point) for point in clean_points]


def _distance_xy(p1, p2):
    return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())


def _project_point_to_segment(point, start, end):
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    segment_sq = (dx * dx) + (dy * dy)
    if segment_sq <= 0:
        return start, 0.0, _distance_xy(point, start)
    ratio = ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / segment_sq
    ratio = min(max(ratio, 0.0), 1.0)
    projected = QgsPointXY(start.x() + (dx * ratio), start.y() + (dy * ratio))
    return projected, ratio, _distance_xy(point, projected)


def _polyline_measure(points):
    measures = [0.0]
    for previous, current in zip(points[:-1], points[1:]):
        measures.append(measures[-1] + _distance_xy(previous, current))
    return measures


def _locate_point_on_polyline(points, point):
    if len(points) < 2:
        return 0.0, points[0] if points else point, 0.0
    measures = _polyline_measure(points)
    best = None
    for index, (start, end) in enumerate(zip(points[:-1], points[1:])):
        projected, ratio, distance = _project_point_to_segment(point, start, end)
        measure = measures[index] + (_distance_xy(start, end) * ratio)
        candidate = (distance, measure, projected)
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best[1], best[2], best[0]


def _point_at_measure(points, target_measure):
    if not points:
        return None
    if len(points) == 1:
        return points[0]
    measures = _polyline_measure(points)
    total = measures[-1]
    if target_measure <= 0:
        return points[0]
    if target_measure >= total:
        return points[-1]
    for index, (start, end) in enumerate(zip(points[:-1], points[1:])):
        seg_start = measures[index]
        seg_end = measures[index + 1]
        if seg_start <= target_measure <= seg_end:
            span = max(seg_end - seg_start, 1e-12)
            ratio = (target_measure - seg_start) / span
            return QgsPointXY(
                start.x() + ((end.x() - start.x()) * ratio),
                start.y() + ((end.y() - start.y()) * ratio),
            )
    return points[-1]


def _dedupe_consecutive_points(points):
    clean = []
    for point in points:
        if not clean or _distance_xy(clean[-1], point) > 1e-9:
            clean.append(point)
    return clean


def _substring_polyline(points, start_measure, end_measure):
    if len(points) < 2:
        return points
    reverse_output = start_measure > end_measure
    lower = min(start_measure, end_measure)
    upper = max(start_measure, end_measure)
    measures = _polyline_measure(points)
    total = measures[-1]
    if total <= 0 or abs(upper - lower) <= total * 0.001:
        return points

    lower = min(max(lower, 0.0), total)
    upper = min(max(upper, 0.0), total)
    output = [_point_at_measure(points, lower)]
    for point, measure in zip(points[1:-1], measures[1:-1]):
        if lower < measure < upper:
            output.append(point)
    output.append(_point_at_measure(points, upper))
    output = _dedupe_consecutive_points(output)
    if reverse_output:
        output.reverse()
    return output


def _line_paths_from_geometry(geometry):
    if not geometry or geometry.isEmpty():
        return []
    paths = []
    try:
        if geometry.isMultipart():
            for part in geometry.asMultiPolyline():
                path = [_as_point_xy(point) for point in part]
                if len(path) >= 2:
                    paths.append(path)
        else:
            path = [_as_point_xy(point) for point in geometry.asPolyline()]
            if len(path) >= 2:
                paths.append(path)
    except Exception:
        pass
    return paths


def _profile_trace_from_layer(layer, profile_line, map_settings):
    if not profile_line or layer.type() != QgsMapLayerType.VectorLayer:
        return None
    try:
        if layer.geometryType() != QgsWkbTypes.LineGeometry:
            return None
    except Exception:
        return None

    source_crs = _map_crs_from_settings(map_settings)
    layer_crs = layer.crs()
    profile_map_points = [_as_point_xy(profile_line[0]), _as_point_xy(profile_line[1])]
    profile_layer_points = _transform_points(profile_map_points, source_crs, layer_crs)
    if len(profile_layer_points) < 2:
        return None

    profile_layer_geom = QgsGeometry.fromPolylineXY(profile_layer_points)
    if not profile_layer_geom or profile_layer_geom.isEmpty():
        return None

    line_length = max(profile_layer_geom.length(), 1e-9)
    extent_span = max(layer.extent().width(), layer.extent().height(), 1e-9)
    tolerance = max(line_length * 0.03, extent_span * 0.0005)
    search_rect = _buffered_rect(profile_layer_geom.boundingBox(), tolerance)
    request = QgsFeatureRequest().setFilterRect(search_rect)

    best_feature = None
    best_geometry = None
    best_distance = None
    for feature in layer.getFeatures(request):
        geometry = feature.geometry()
        if not geometry or geometry.isEmpty():
            continue
        distance = geometry.distance(profile_layer_geom)
        if best_distance is None or distance < best_distance:
            best_feature = feature
            best_geometry = geometry
            best_distance = distance

    if best_feature is None or best_distance is None or best_distance > tolerance:
        return None

    best_path = None
    best_path_distance = None
    for path in _line_paths_from_geometry(best_geometry):
        path_geom = QgsGeometry.fromPolylineXY(path)
        distance = path_geom.distance(profile_layer_geom)
        if best_path_distance is None or distance < best_path_distance:
            best_path = path
            best_path_distance = distance
    if not best_path:
        return None

    map_path = _transform_points(best_path, layer_crs, source_crs)
    start_measure, _, _ = _locate_point_on_polyline(map_path, profile_map_points[0])
    end_measure, _, _ = _locate_point_on_polyline(map_path, profile_map_points[1])
    trimmed_path = _substring_polyline(map_path, start_measure, end_measure)
    if len(trimmed_path) < 2:
        trimmed_path = map_path

    return {
        "feature": best_feature,
        "points": trimmed_path,
        "distance": best_distance,
    }


def _feature_title(layer, feature, settings, lang):
    mode = settings.get("topo_profile_title_mode", "field")
    if mode == "single":
        title = settings.get("topo_profile_single_title", "").strip()
        return title or _text(lang, "Profilo topografico", "Topographic profile")

    title_map = settings.get("topo_profile_title_map", {}) or {}
    mapped_title = title_map.get(str(int(feature.id()))) if feature else None
    if mode == "manual" and mapped_title:
        return mapped_title

    field_name = settings.get("topo_profile_title_field", "")
    if feature and field_name:
        try:
            value = feature[field_name]
            if value not in (None, ""):
                return str(value)
        except Exception:
            pass

    if feature:
        for field in layer.fields():
            try:
                value = feature[field.name()]
                if value not in (None, ""):
                    return str(value)
            except Exception:
                pass
        return f"FID {feature.id()}"
    return _text(lang, "Profilo topografico", "Topographic profile")


def _profile_titles_for_trace(layer, trace, settings, lang):
    if trace and trace.get("feature"):
        return [_feature_title(layer, trace.get("feature"), settings, lang)]
    titles = [title for title in settings.get("topo_profile_titles", []) if title]
    if titles:
        return [titles[0]]
    return [_text(lang, "Profilo topografico", "Topographic profile")]


def prepare_topographic_profile_request(layer, rect, map_settings, settings):
    lang = settings.get("language", "it")
    profile_rect = settings.get("topo_profile_rect") or rect
    profile_line = settings.get("topo_profile_line")
    profile_trace = _profile_trace_from_layer(layer, profile_line, map_settings)
    profile_titles = _profile_titles_for_trace(layer, profile_trace, settings, lang)
    return {
        "rect": profile_rect,
        "map_settings": _map_crs_from_settings(map_settings),
        "titles": profile_titles,
        "profile_line": profile_line,
        "profile_points": profile_trace.get("points") if profile_trace else None,
        "language": lang,
        "source": settings.get("topo_profile_source", "online"),
        "raster_layer_id": settings.get("topo_profile_raster_id", ""),
    }


def _map_units_per_meter(map_item):
    crs = map_item.crs() if map_item.crs().isValid() else QgsProject.instance().crs()
    map_units = crs.mapUnits()

    if map_units == QgsUnitTypes.DistanceDegrees:
        center_lat = (map_item.extent().yMinimum() + map_item.extent().yMaximum()) / 2.0
        center_lat = min(max(center_lat, -89.9), 89.9)
        meters_per_degree = 111320.0 * max(math.cos(math.radians(center_lat)), 0.001)
        return 1.0 / meters_per_degree, map_units

    if map_units == QgsUnitTypes.DistanceUnknownUnit:
        return None, map_units

    try:
        units_per_meter = QgsUnitTypes.fromUnitToUnitFactor(QgsUnitTypes.DistanceMeters, map_units)
        if units_per_meter > 0:
            return units_per_meter, map_units
    except Exception:
        pass
    return None, map_units


def _configure_scalebar(scalebar, map_item, small_font, target_width_mm):
    scalebar.setLinkedMap(map_item)
    scalebar.setStyle("Single Box")
    scalebar.setNumberOfSegments(4)
    scalebar.setNumberOfSegmentsLeft(0)
    scalebar.setHeight(2.6 if small_font <= 7 else 3.6)
    scalebar.setLineWidth(0.25)
    scalebar.setLineColor(QColor(15, 23, 42))
    scalebar.setFillColor(QColor(15, 23, 42))
    scalebar.setFillColor2(QColor(255, 255, 255))
    scalebar.setFont(QFont("Arial", max(small_font - 1, 6)))
    scalebar.setFontColor(QColor(15, 23, 42))

    extent_width = max(map_item.extent().width(), 1e-9)
    units_per_meter, map_units = _map_units_per_meter(map_item)

    if units_per_meter is None:
        segment_units = max(extent_width / 5.0, 1.0)
        scalebar.setUnits(map_units)
        scalebar.setMapUnitsPerScaleBarUnit(1.0)
        scalebar.setUnitsPerSegment(max(_nice_floor_step(segment_units), 1.0))
        return

    extent_width_m = extent_width / units_per_meter
    try:
        scale_value = map_item.scale()
    except Exception:
        scale_value = 0

    if scale_value and scale_value > 0:
        target_total_m = scale_value * (target_width_mm / 1000.0)
    else:
        target_total_m = extent_width_m * 0.25

    segment_m = max(_nice_floor_step(target_total_m / 4.0), 1.0)
    total_m = segment_m * 4.0

    if total_m >= 1000.0:
        scalebar.setUnits(QgsUnitTypes.DistanceKilometers)
        scalebar.setMapUnitsPerScaleBarUnit(units_per_meter * 1000.0)
        scalebar.setUnitLabel("km")
        scalebar.setUnitsPerSegment(segment_m / 1000.0)
    else:
        scalebar.setUnits(QgsUnitTypes.DistanceMeters)
        scalebar.setMapUnitsPerScaleBarUnit(units_per_meter)
        scalebar.setUnitLabel("m")
        scalebar.setUnitsPerSegment(segment_m)


def _add_north_arrow(layout, map_item, x, y, size, font_size):
    north_asset = os.path.join(os.path.dirname(__file__), "assets", "north_arrow.svg")
    if os.path.exists(north_asset):
        arrow = QgsLayoutItemPicture(layout)
        arrow.setPicturePath(north_asset)
        arrow.setResizeMode(QgsLayoutItemPicture.Zoom)
        arrow.setLinkedMap(map_item)
        arrow.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
        arrow.attemptResize(QgsLayoutSize(size, size, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(arrow)
    else:
        _add_label(
            layout,
            "N",
            x,
            y,
            size,
            size,
            font_size,
            True,
            QColor(15, 23, 42),
            Qt.AlignHCenter,
            Qt.AlignVCenter,
        )


def _add_map_decorations(layout, map_item, metrics, map_x, map_y, map_h, lang="it"):
    # Freccia nord in alto a sinistra della mappa (stile tecnico).
    arrow_x = map_x + 4.0
    arrow_y = map_y + 4.0
    _add_rectangle(
        layout,
        arrow_x - 1.8,
        arrow_y - 1.8,
        metrics["north"] + 3.6,
        metrics["north"] + 3.6,
        QColor(255, 255, 255, 240),
        QColor(17, 24, 39),
        0.2,
    )
    _add_north_arrow(layout, map_item, arrow_x, arrow_y, metrics["north"], metrics["small"])

    # Barra di scala in basso a sinistra della mappa.
    scalebar = QgsLayoutItemScaleBar(layout)
    _configure_scalebar(scalebar, map_item, metrics["small"], metrics["scale_w"])
    try:
        map_scale_value = map_item.scale()
    except Exception:
        map_scale_value = 0
    scale_label = f"{_text(lang, 'Scala', 'Scale')} {_scale_value_text(map_scale_value)}"
    scale_label_h = max(metrics["small"] * 0.58, 4.0)
    scale_box_w = metrics["scale_w"] + 16.0
    scale_box_h = metrics["scale_h"] + scale_label_h + 8.0
    scale_box_x = map_x + 4.0
    scale_box_y = map_y + map_h - scale_box_h - 4.0
    _add_rectangle(
        layout,
        scale_box_x,
        scale_box_y,
        scale_box_w,
        scale_box_h,
        QColor(255, 255, 255, 240),
        QColor(17, 24, 39),
        0.2,
    )
    _add_fitted_label(
        layout,
        scale_label,
        scale_box_x + 3.0,
        scale_box_y + 1.0,
        scale_box_w - 6.0,
        scale_label_h,
        max(metrics["small"], 7),
        True,
        QColor(17, 24, 39),
        Qt.AlignHCenter,
        Qt.AlignVCenter,
        min_size=6,
    )
    scalebar.attemptMove(
        QgsLayoutPoint(
            scale_box_x + (scale_box_w - metrics["scale_w"]) / 2.0,
            scale_box_y + scale_label_h + 3.0,
            QgsUnitTypes.LayoutMillimeters,
        )
    )
    scalebar.attemptResize(QgsLayoutSize(metrics["scale_w"], metrics["scale_h"], QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(scalebar)


def _combined_layers_extent(map_settings, target_crs):
    if not map_settings:
        return None
    combined = None
    try:
        layers = map_settings.layers()
    except Exception:
        return None
    for layer in layers:
        try:
            extent = layer.extent()
            if not extent or extent.isEmpty() or not extent.isFinite():
                continue
            layer_crs = layer.crs()
            if layer_crs.isValid() and target_crs.isValid() and layer_crs != target_crs:
                transform = QgsCoordinateTransform(layer_crs, target_crs, QgsProject.instance())
                extent = transform.transformBoundingBox(extent)
            if combined is None:
                combined = extent
            else:
                combined.combineExtentWith(extent)
        except Exception:
            continue
    return combined


def _transformed_layer_extent(layer, target_crs):
    try:
        extent = layer.extent()
        if not extent or extent.isEmpty() or not extent.isFinite():
            return None
        layer_crs = layer.crs()
        if layer_crs.isValid() and target_crs.isValid() and layer_crs != target_crs:
            transform = QgsCoordinateTransform(layer_crs, target_crs, QgsProject.instance())
            return transform.transformBoundingBox(extent)
        return extent
    except Exception:
        return None


def _crs_area_extent(crs, target_crs):
    try:
        bounds = crs.bounds()
        if not bounds or bounds.isEmpty() or not bounds.isFinite():
            return None
        source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
            transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
            return transform.transformBoundingBox(bounds)
        return bounds
    except Exception:
        return None


def _extent_ratio(candidate, reference):
    ref_w = max(reference.width(), 1e-9)
    ref_h = max(reference.height(), 1e-9)
    return max(candidate.width() / ref_w, candidate.height() / ref_h)


def _extent_contains(candidate, reference):
    return (
        candidate.xMinimum() <= reference.xMinimum()
        and candidate.xMaximum() >= reference.xMaximum()
        and candidate.yMinimum() <= reference.yMinimum()
        and candidate.yMaximum() >= reference.yMaximum()
    )


def _fit_extent_to_aspect(extent, aspect):
    if not extent or extent.isEmpty() or aspect <= 0:
        return extent
    cx = (extent.xMinimum() + extent.xMaximum()) / 2.0
    cy = (extent.yMinimum() + extent.yMaximum()) / 2.0
    width = max(extent.width(), 1e-9)
    height = max(extent.height(), 1e-9)
    current = width / height
    if current < aspect:
        width = height * aspect
    else:
        height = width / aspect
    return QgsRectangle(cx - (width / 2.0), cy - (height / 2.0), cx + (width / 2.0), cy + (height / 2.0))


def _overview_context_extent(source_extent, layer, map_settings, target_crs, target_aspect=1.0):
    candidates = []

    layer_extent = _transformed_layer_extent(layer, target_crs)
    if layer_extent:
        candidates.append(("layer", layer_extent))

    combined_extent = _combined_layers_extent(map_settings, target_crs)
    if combined_extent:
        candidates.append(("visible", combined_extent))

    crs_extent = _crs_area_extent(target_crs, target_crs)
    if crs_extent:
        candidates.append(("crs", crs_extent))

    valid = []
    for _name, candidate in candidates:
        if not candidate or candidate.isEmpty() or not candidate.isFinite():
            continue
        if not _extent_contains(candidate, source_extent):
            merged = QgsRectangle(candidate)
            merged.combineExtentWith(source_extent)
            candidate = merged
        ratio = _extent_ratio(candidate, source_extent)
        if 4.0 <= ratio <= 30.0:
            valid.append((abs(ratio - 8.0), candidate))

    if valid:
        valid.sort(key=lambda item: item[0])
        return _fit_extent_to_aspect(valid[0][1], target_aspect)

    buffer_distance = max(source_extent.width(), source_extent.height()) * 3.0
    return _fit_extent_to_aspect(_buffered_rect(source_extent, buffer_distance), target_aspect)


def _build_overview_map(layout, map_item, source_extent, x, y, w, h, crs, map_settings, context_extent=None):
    overview_map = QgsLayoutItemMap(layout)
    overview_map.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    overview_map.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    overview_map.setCrs(crs)

    if map_settings:
        try:
            layers = map_settings.layers()
            if layers:
                overview_map.setLayers(layers)
                overview_map.setKeepLayerSet(True)
        except Exception:
            pass

    overview_extent = context_extent
    if not overview_extent or overview_extent.isEmpty() or not overview_extent.isFinite():
        buffer_distance = max(source_extent.width(), source_extent.height()) * 2.5
        overview_extent = _buffered_rect(source_extent, buffer_distance) if buffer_distance > 0 else source_extent
    elif (
        overview_extent.width() < source_extent.width() * 2.0
        and overview_extent.height() < source_extent.height() * 2.0
    ):
        buffer_distance = max(source_extent.width(), source_extent.height()) * 2.5
        overview_extent = _buffered_rect(source_extent, buffer_distance) if buffer_distance > 0 else source_extent
    overview_map.setExtent(overview_extent)
    _set_item_frame(overview_map, 0.3, QColor(17, 24, 39))
    try:
        overview_map.setBackgroundColor(QColor(248, 250, 252))
    except AttributeError:
        pass
    layout.addLayoutItem(overview_map)

    overview = QgsLayoutItemMapOverview("Overview", overview_map)
    overview.setLinkedMap(map_item)
    frame_symbol = QgsFillSymbol.createSimple(
        {"color": "0,0,0,0", "outline_color": "220,38,38,255", "outline_width": "0.45"}
    )
    overview.setFrameSymbol(frame_symbol)
    overview_map.overviews().addOverview(overview)
    return overview_map


def _add_overview_section(layout, map_item, extent, x, y, w, h, crs, map_settings, lang, font_size, context_extent=None):  # noqa: E501
    if w <= 18.0 or h <= 18.0:
        return None
    inner = _add_section_frame(layout, _text(lang, "INQUADRAMENTO", "OVERVIEW"), x, y, w, h, font_size, lang)
    if not inner:
        return None
    inner_x, inner_y, inner_w, inner_h = inner
    return _build_overview_map(
        layout,
        map_item,
        extent,
        inner_x,
        inner_y,
        inner_w,
        inner_h,
        crs,
        map_settings,
        context_extent,
    )


def _style_legend(legend, font_size, lang="it"):
    legend.setTitle(_text(lang, "Legenda", "Legend"))
    try:
        legend.setFontColor(QColor(17, 24, 39))
    except AttributeError:
        pass
    try:
        legend.setStyleFont(legend.Title, QFont("Arial", font_size, QFont.Bold))
        legend.setStyleFont(legend.Group, QFont("Arial", max(font_size, 8)))
        legend.setStyleFont(legend.Subgroup, QFont("Arial", max(font_size, 8)))
        legend.setStyleFont(legend.SymbolLabel, QFont("Arial", max(font_size, 8)))
    except Exception:
        pass
    for setter, value in (
        ("setBoxSpace", 2.6),
        ("setColumnSpace", 4.0),
        ("setSymbolWidth", max(font_size * 0.95, 8.0)),
        ("setSymbolHeight", max(font_size * 0.55, 5.0)),
        ("setWmsLegendWidth", 18.0),
        ("setWmsLegendHeight", 12.0),
    ):
        try:
            getattr(legend, setter)(value)
        except Exception:
            pass
    try:
        legend.setColumnCount(1)
    except Exception:
        pass
    _set_item_frame(legend, 0.2, QColor(17, 24, 39))
    try:
        legend.setBackgroundEnabled(True)
        legend.setBackgroundColor(QColor(255, 255, 255))
    except AttributeError:
        pass


def _add_logo(layout, logo_path, x, y, w, h):
    if not logo_path or not os.path.exists(logo_path):
        return
    logo = QgsLayoutItemPicture(layout)
    logo.setPicturePath(logo_path)
    logo.setResizeMode(QgsLayoutItemPicture.Zoom)
    logo.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    logo.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(logo)


def _add_image_section(layout, title, image_path, x, y, w, h, font_size, lang="it"):
    if not image_path or not os.path.exists(image_path) or w <= 18.0 or h <= 16.0:
        return None
    inner = _add_section_frame(layout, title, x, y, w, h, font_size, lang)
    if not inner:
        return None
    inner_x, inner_y, inner_w, inner_h = inner
    _add_logo(layout, image_path, inner_x, inner_y, inner_w, inner_h)
    return True


def _add_legend_section(layout, map_item, x, y, w, h, font_size, lang="it"):
    if w <= 18.0 or h <= 18.0:
        return None
    inner = _add_section_frame(layout, _text(lang, "LEGENDA", "LEGEND"), x, y, w, h, font_size, lang)
    if not inner:
        return None
    inner_x, inner_y, inner_w, inner_h = inner
    legend = QgsLayoutItemLegend(layout)
    legend.setLinkedMap(map_item)
    legend.setLegendFilterByMapEnabled(True)
    legend.setAutoUpdateModel(True)
    _style_legend(legend, max(font_size, 7), lang)
    legend.attemptMove(QgsLayoutPoint(inner_x, inner_y, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(inner_w, inner_h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend)
    return legend


def _add_attribute_section(layout, layer, features, x, y, w, h, font_size, lang="it"):
    if not features or w <= 18.0 or h <= 18.0:
        return False
    inner = _add_section_frame(
        layout,
        _text(lang, "ATTRIBUTI SELEZIONE", "SELECTION ATTRIBUTES"),
        x,
        y,
        w,
        h,
        font_size,
        lang,
    )
    if not inner:
        return False
    inner_x, inner_y, inner_w, inner_h = inner
    return _add_compact_attribute_block(
        layout,
        layer,
        features,
        inner_x,
        inner_y,
        inner_w,
        inner_h,
        max(font_size, 7),
        lang,
        show_title=False,
    )


def _layout_page_count(layout):
    collection = layout.pageCollection()
    try:
        return collection.pageCount()
    except AttributeError:
        return len(collection.pages())


def _append_layout_page(layout, page_w, page_h):
    page = QgsLayoutItemPage(layout)
    page.setPageSize(QgsLayoutSize(page_w, page_h, QgsUnitTypes.LayoutMillimeters))
    layout.pageCollection().addPage(page)
    return _layout_page_count(layout) - 1


def _page_origin(layout, page_index):
    page = layout.pageCollection().page(page_index)
    if page is None:
        return 0.0, 0.0
    try:
        point = layout.pageCollection().pagePositionToLayoutPosition(
            page_index,
            QgsLayoutPoint(0.0, 0.0, QgsUnitTypes.LayoutMillimeters),
        )
        return float(point.x()), float(point.y())
    except Exception:
        pass
    try:
        pos = page.pos()
        return float(pos.x()), float(pos.y())
    except Exception:
        return 0.0, 0.0


def _page_rect(layout, page_index, x, y, w, h, fill_color, border_color, border_mm=0.2):
    ox, oy = _page_origin(layout, page_index)
    return _add_rectangle(layout, ox + x, oy + y, w, h, fill_color, border_color, border_mm)


def _page_label(
    layout,
    page_index,
    text,
    x,
    y,
    w,
    h,
    font_size,
    bold,
    color,
    halign=Qt.AlignLeft,
    valign=Qt.AlignVCenter,
):
    ox, oy = _page_origin(layout, page_index)
    return _add_label(layout, text, ox + x, oy + y, w, h, font_size, bold, color, halign, valign)


def _page_fitted_label(
    layout,
    page_index,
    text,
    x,
    y,
    w,
    h,
    font_size,
    bold,
    color,
    halign=Qt.AlignLeft,
    valign=Qt.AlignVCenter,
    min_size=7,
):
    ox, oy = _page_origin(layout, page_index)
    return _add_fitted_label(layout, text, ox + x, oy + y, w, h, font_size, bold, color, halign, valign, min_size)


def _page_picture(layout, page_index, image_path, x, y, w, h):
    if not image_path or not os.path.exists(image_path):
        return None
    ox, oy = _page_origin(layout, page_index)
    picture = QgsLayoutItemPicture(layout)
    picture.setPicturePath(image_path)
    picture.setResizeMode(QgsLayoutItemPicture.Zoom)
    picture.attemptMove(QgsLayoutPoint(ox + x, oy + y, QgsUnitTypes.LayoutMillimeters))
    picture.attemptResize(QgsLayoutSize(w, h, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(picture)
    return picture


def _add_report_page_header(layout, page_index, page_w, page_h, title, subtitle=None):
    _page_rect(layout, page_index, 8.0, 8.0, page_w - 16.0, page_h -
               16.0, QColor(255, 255, 255), QColor(17, 24, 39), 0.25)
    large_page = page_w >= 400.0
    title_size = 15 if large_page else 13
    subtitle_size = 10 if large_page else 9
    title_h = 13.8 if large_page else 12.0
    subtitle_y = 24.8 if large_page else 23.5
    subtitle_h = 9.8 if large_page else 9.0
    rule_y = 37.2 if large_page else 35.0
    _page_fitted_label(
        layout,
        page_index,
        title,
        12.0,
        10.5,
        page_w - 24.0,
        title_h,
        title_size,
        True,
        QColor(17, 24, 39),
        Qt.AlignLeft,
        Qt.AlignVCenter,
        min_size=10,
    )
    if subtitle:
        _page_fitted_label(
            layout,
            page_index,
            subtitle,
            12.0,
            subtitle_y,
            page_w - 24.0,
            subtitle_h,
            subtitle_size,
            False,
            QColor(71, 85, 105),
            Qt.AlignLeft,
            Qt.AlignVCenter,
            min_size=8,
        )
    _page_rect(layout, page_index, 12.0, rule_y, page_w - 24.0, 0.25, QColor(17, 24, 39), QColor(17, 24, 39), 0.0)


def _build_horizontal_panel(
    layout,
    layer,
    map_item,
    panel_x,
    panel_y,
    panel_w,
    panel_h,
    metrics,
    settings,
    extent,
    scale_value,
    crs_authid,
    overview_crs=None,
    map_settings=None,
    overview_context=None,
    lang="it",
    chart_image_path=None,
    selection_features=None,
):
    ink = QColor(17, 24, 39)
    _add_rectangle(layout, panel_x, panel_y, panel_w, panel_h, QColor(255, 255, 255), ink, 0.28)

    pad = 2.0
    logo_path = settings.get("logo", "")
    has_logo = bool(logo_path and os.path.exists(logo_path))
    logo_w = panel_w * 0.105 if has_logo else 0.0
    info_w = panel_w * (0.34 if has_logo else 0.38)
    overview_w = panel_w * 0.22
    content_w = panel_w - logo_w - info_w - overview_w
    min_content_w = max(panel_w * 0.22, 54.0)
    if content_w < min_content_w:
        deficit = min_content_w - content_w
        info_w = max(info_w - deficit * 0.65, panel_w * 0.30)
        overview_w = max(overview_w - deficit * 0.35, panel_w * 0.18)
        content_w = panel_w - logo_w - info_w - overview_w

    cursor_x = panel_x
    if has_logo and logo_w > 12.0:
        _add_rectangle(layout, cursor_x, panel_y, logo_w, panel_h, QColor(255, 255, 255), ink, 0.16)
        _add_logo(layout, logo_path, cursor_x + pad, panel_y + pad, logo_w - (2 * pad), panel_h - (2 * pad))
        cursor_x += logo_w

    info_x = cursor_x
    _add_rectangle(layout, info_x, panel_y, info_w, panel_h, QColor(255, 255, 255), ink, 0.16)
    title_text = settings.get("title", "TAVOLA CARTOGRAFICA").upper()
    title_h = min(max(metrics["title"] * 1.18, 14.0), panel_h * 0.28)
    _add_fitted_label(
        layout,
        title_text,
        info_x + pad,
        panel_y + 1.2,
        info_w - (2 * pad),
        title_h,
        metrics["title"],
        True,
        ink,
        Qt.AlignHCenter,
        Qt.AlignVCenter,
        min_size=max(metrics["meta"], 9),
    )
    _add_rectangle(layout, info_x, panel_y + title_h + 2.0, info_w, 0.14, QColor(17, 24, 39), QColor(17, 24, 39), 0.0)

    scale_h = min(max(metrics["meta"] * 1.40, 10.0), panel_h * 0.18)
    scale_y = panel_y + title_h + 3.4
    _add_scale_indicator(layout, info_x + pad, scale_y, info_w - (2 * pad), scale_h, scale_value, metrics["meta"], lang)
    meta_y = scale_y + scale_h + 1.6
    _add_key_value_table(
        layout,
        _metadata_rows(layer.name(), crs_authid, extent, lang),
        info_x + pad,
        meta_y,
        info_w - (2 * pad),
        max(panel_y + panel_h - meta_y - pad, 1.0),
        max(metrics.get("small", 8), 7),
    )

    overview_x = info_x + info_w
    _add_overview_section(
        layout,
        map_item,
        extent,
        overview_x + pad,
        panel_y + pad,
        max(overview_w - (2 * pad), 1.0),
        panel_h - (2 * pad),
        overview_crs or QgsProject.instance().crs(),
        map_settings,
        lang,
        metrics.get("small", 8),
        overview_context,
    )

    content_x = overview_x + overview_w
    content_inner_x = content_x + pad
    content_inner_y = panel_y + pad
    content_inner_w = max(content_w - (2 * pad), 1.0)
    content_inner_h = panel_h - (2 * pad)
    content_gap = 1.4

    attr_complete = False
    if settings.get("export_attributes", False) and selection_features and content_inner_h > 34.0:
        attr_h = min(max(content_inner_h * 0.36, 20.0), content_inner_h * 0.50)
        attr_complete = _add_attribute_section(
            layout,
            layer,
            selection_features,
            content_inner_x,
            content_inner_y,
            content_inner_w,
            attr_h,
            metrics.get("small", 8),
            lang,
        )
        content_inner_y += attr_h + content_gap
        content_inner_h -= attr_h + content_gap

    if attr_complete:
        settings["_attributes_in_titleblock"] = True

    if chart_image_path and os.path.exists(chart_image_path) and content_inner_h > 18.0:
        _add_image_section(
            layout,
            _text(lang, "DASHBOARD", "DASHBOARD"),
            chart_image_path,
            content_inner_x,
            content_inner_y,
            content_inner_w,
            content_inner_h,
            metrics.get("small", 8),
            lang,
        )
    elif content_inner_h > 18.0:
        _add_legend_section(
            layout,
            map_item,
            content_inner_x,
            content_inner_y,
            content_inner_w,
            content_inner_h,
            metrics.get("legend", metrics["meta"]),
            lang,
        )


def _build_vertical_panel(
    layout,
    layer,
    map_item,
    panel_x,
    panel_y,
    panel_w,
    panel_h,
    metrics,
    settings,
    extent,
    scale_value,
    crs_authid,
    overview_crs=None,
    map_settings=None,
    overview_context=None,
    lang="it",
    chart_image_path=None,
    selection_features=None,
):
    ink = QColor(17, 24, 39)
    _add_rectangle(layout, panel_x, panel_y, panel_w, panel_h, QColor(255, 255, 255), ink, 0.28)

    pad = 2.4
    cursor_y = panel_y + pad
    content_w = panel_w - (2 * pad)
    gap = 1.6
    logo_path = settings.get("logo", "")

    if logo_path and os.path.exists(logo_path):
        logo_h = min(max(panel_h * 0.10, 18.0), panel_w * 0.32)
        _add_rectangle(layout, panel_x + pad, cursor_y, content_w, logo_h, QColor(255, 255, 255), ink, 0.14)
        _add_logo(layout, logo_path, panel_x + (2 * pad), cursor_y + pad, content_w - (2 * pad), logo_h - (2 * pad))
        cursor_y += logo_h + gap

    title_text = settings.get("title", "TAVOLA CARTOGRAFICA").upper()
    title_h = min(max(metrics["title"] * 1.24, 15.0), panel_h * 0.12)
    _add_rectangle(layout, panel_x + pad, cursor_y, content_w, title_h, QColor(248, 250, 252), ink, 0.14)
    _add_fitted_label(
        layout,
        title_text,
        panel_x + pad + 1.2,
        cursor_y,
        content_w - 2.4,
        title_h,
        metrics["title"],
        True,
        ink,
        Qt.AlignHCenter,
        Qt.AlignVCenter,
        min_size=max(metrics["meta"], 9),
    )
    cursor_y += title_h + gap

    scale_h = min(max(metrics["meta"] * 1.35, 10.0), panel_h * 0.075)
    _add_scale_indicator(layout, panel_x + pad, cursor_y, content_w, scale_h, scale_value, metrics["meta"], lang)
    cursor_y += scale_h + gap

    meta_h = min(max(metrics["meta"] * 5.4, 38.0), panel_h * 0.23)
    meta_inner = _add_section_frame(
        layout,
        _text(lang, "DATI TAVOLA", "SHEET DATA"),
        panel_x + pad,
        cursor_y,
        content_w,
        meta_h,
        metrics.get("small", 8),
        lang,
    )
    if meta_inner:
        _add_key_value_table(layout, _metadata_rows(layer.name(), crs_authid, extent, lang),
                             *meta_inner, max(metrics.get("small", 8), 7))
    cursor_y += meta_h + gap

    remaining_h = panel_y + panel_h - cursor_y - pad
    if remaining_h > 42.0:
        overview_h = min(max(content_w * 0.50, 28.0), remaining_h * 0.30)
        _add_overview_section(
            layout,
            map_item,
            extent,
            panel_x + pad,
            cursor_y,
            content_w,
            overview_h,
            overview_crs or QgsProject.instance().crs(),
            map_settings,
            lang,
            metrics.get("small", 8),
            overview_context,
        )
        cursor_y += overview_h + gap

    remaining_h = panel_y + panel_h - cursor_y - pad
    attr_complete = False
    if settings.get("export_attributes", False) and selection_features and remaining_h > 42.0:
        attr_h = min(max(remaining_h * 0.34, 24.0), remaining_h * 0.48)
        attr_complete = _add_attribute_section(
            layout,
            layer,
            selection_features,
            panel_x + pad,
            cursor_y,
            content_w,
            attr_h,
            metrics.get("small", 8),
            lang,
        )
        cursor_y += attr_h + gap
        remaining_h = panel_y + panel_h - cursor_y - pad

    if attr_complete:
        settings["_attributes_in_titleblock"] = True

    if chart_image_path and os.path.exists(chart_image_path) and remaining_h > 22.0:
        _add_image_section(
            layout,
            _text(lang, "DASHBOARD", "DASHBOARD"),
            chart_image_path,
            panel_x + pad,
            cursor_y,
            content_w,
            remaining_h,
            metrics.get("small", 8),
            lang,
        )
    elif remaining_h > 22.0:
        _add_legend_section(
            layout,
            map_item,
            panel_x + pad,
            cursor_y,
            content_w,
            remaining_h,
            metrics.get("legend", metrics["meta"]),
            lang,
        )


def _attribute_filter_expression(layer, rect, map_settings, features=None):
    if features is not None:
        feature_ids = [str(int(feature.id())) for feature in features]
        if not feature_ids:
            return "FALSE"
        return f"$id IN ({','.join(feature_ids)})"

    layer_rect = _selection_rect_for_layer(layer, rect, map_settings)
    if layer_rect is None or layer_rect.isEmpty():
        return "FALSE"
    wkt_polygon = layer_rect.asWktPolygon().replace("'", "''")
    return f"intersects($geometry, geom_from_wkt('{wkt_polygon}'))"


def _add_attributes_page_to_layout(layout, layer, rect, map_settings, features, lang="it"):
    if layer.type() != QgsMapLayerType.VectorLayer:
        return

    row_count = len(features)
    field_names = [field.name() for field in layer.fields() if field.name().lower() != "geometry"]
    page_w = 420.0
    page_h = 297.0
    page_index = _append_layout_page(layout, page_w, page_h)

    _add_report_page_header(
        layout,
        page_index,
        page_w,
        page_h,
        f"{_text(lang, 'TABELLA ATTRIBUTI', 'ATTRIBUTE TABLE')} - {layer.name()}",
        _text(
            lang,
            f"Record filtrati sull'area Shift+Draw: {row_count}",
            f"Records filtered on the Shift+Draw area: {row_count}",
        ),
    )

    if row_count <= 0:
        _page_fitted_label(
            layout,
            page_index,
            _text(
                lang,
                "Nessuna geometria del layer attivo interseca l'area selezionata.",
                "No feature from the active layer intersects the selected area.",
            ),
            12.0,
            42.0,
            page_w - 24.0,
            14.0,
            10,
            False,
            QColor(71, 85, 105),
            Qt.AlignLeft,
            Qt.AlignTop,
            min_size=8,
        )
        return

    table = QgsLayoutItemAttributeTable(layout)
    layout.addMultiFrame(table)
    table.setSource(QgsLayoutItemAttributeTable.LayerAttributes)
    table.setVectorLayer(layer)
    table.resetColumns()
    if field_names:
        table.setDisplayedFields(field_names, True)
    table.setFilterFeatures(True)
    table.setFeatureFilter(_attribute_filter_expression(layer, rect, map_settings, features))
    table.setMaximumNumberOfFeatures(max(row_count, 1))
    try:
        table.setResizeMode(QgsLayoutMultiFrame.RepeatUntilFinished)
    except Exception:
        table.setResizeMode(QgsLayoutMultiFrame.UseExistingFrames)
    try:
        table.setHeaderMode(table.AllFrames)
        table.setWrapBehavior(table.WrapText)
        table.setCellMargin(1.2)
        table.setShowGrid(True)
        table.setGridColor(QColor(203, 213, 225))
        table.setGridStrokeWidth(0.12)
        table.setBackgroundColor(QColor(255, 255, 255))
        header_format = QgsTextFormat()
        header_format.setFont(QFont("Arial", 8, QFont.Bold))
        header_format.setColor(QColor(17, 24, 39))
        table.setHeaderTextFormat(header_format)
        content_format = QgsTextFormat()
        content_format.setFont(QFont("Arial", 7))
        content_format.setColor(QColor(31, 41, 55))
        table.setContentTextFormat(content_format)
    except Exception:
        pass

    ox, oy = _page_origin(layout, page_index)
    frame = QgsLayoutFrame(layout, table)
    frame.setFrameEnabled(True)
    frame.attemptMove(QgsLayoutPoint(ox + 12.0, oy + 40.0, QgsUnitTypes.LayoutMillimeters))
    frame.attemptResize(QgsLayoutSize(page_w - 24.0, page_h - 54.0, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(frame)
    table.addFrame(frame)
    table.refresh()


def _add_dashboard_pages_to_layout(layout, dashboard_images, lang="it"):
    page_w = 297.0
    page_h = 210.0
    for index, dashboard in enumerate(dashboard_images, start=1):
        image_path = dashboard.get("path", "")
        if not image_path or not os.path.exists(image_path):
            continue
        raw_type = dashboard.get("type", "chart")
        chart_type = {
            "pie": _text(lang, "TORTA", "PIE"),
            "bar": _text(lang, "BARRE", "BAR"),
            "percent": _text(lang, "PERCENTUALI", "PERCENTAGES"),
        }.get(raw_type, raw_type.upper())
        page_index = _append_layout_page(layout, page_w, page_h)
        _add_report_page_header(
            layout,
            page_index,
            page_w,
            page_h,
            f"{_text(lang, 'DASHBOARD CARTOGRAFICO', 'CARTOGRAPHIC DASHBOARD')} - {chart_type}",
            _text(
                lang,
                f"Grafico {index} generato sui soli elementi dell'area selezionata.",
                f"Chart {index} generated only from selected-area features.",
            ),
        )
        _page_picture(layout, page_index, image_path, 12.0, 38.0, page_w - 24.0, page_h - 50.0)


def _profile_source_summary(profile, lang):
    source_info = profile.get("source_info", {}) or {}
    if source_info.get("type") == "project":
        return _text(
            lang,
            f"Fonte quote: raster progetto '{source_info.get('name', '')}', banda {source_info.get('band', 1)}.",
            f"Elevation source: project raster '{source_info.get('name', '')}', band {source_info.get('band', 1)}.",
        )
    return _text(
        lang,
        f"Fonte quote: OpenTopoData; dataset {DATASET_STACK}; interpolazione {OPEN_TOPO_DATA_METHOD}.",
        f"Elevation source: OpenTopoData; datasets {DATASET_STACK}; {OPEN_TOPO_DATA_METHOD} interpolation.",
    )


def _add_topographic_profile_pages_to_layout(layout, profiles, lang="it"):
    page_w = 420.0
    page_h = 297.0
    for index, profile in enumerate(profiles, start=1):
        image_path = profile.get("path", "")
        title = profile.get("title", f"Profilo topografico {index}")
        error_message = profile.get("error", "")
        if error_message:
            page_index = _append_layout_page(layout, page_w, page_h)
            _add_report_page_header(
                layout,
                page_index,
                page_w,
                page_h,
                _text(lang, "PROFILO TOPOGRAFICO NON GENERATO", "TOPOGRAPHIC PROFILE NOT GENERATED"),
                title,
            )
            _page_fitted_label(
                layout,
                page_index,
                error_message,
                18.0,
                48.0,
                page_w - 36.0,
                34.0,
                11,
                False,
                QColor(31, 41, 55),
                Qt.AlignLeft,
                Qt.AlignTop,
                min_size=8,
            )
            _page_fitted_label(
                layout,
                page_index,
                _text(
                    lang,
                    "Per evitare i limiti del servizio online, usare la sorgente 'Genera Profilo da progetto' con un raster DTM/DEM locale.",  # noqa: E501
                    "To avoid online service limits, use 'Generate Profile from project' with a local DTM/DEM raster.",
                ),
                18.0,
                88.0,
                page_w - 36.0,
                22.0,
                10,
                True,
                QColor(17, 24, 39),
                Qt.AlignLeft,
                Qt.AlignTop,
                min_size=8,
            )
            continue
        if not image_path or not os.path.exists(image_path):
            continue
        page_index = _append_layout_page(layout, page_w, page_h)
        _add_report_page_header(
            layout,
            page_index,
            page_w,
            page_h,
            _text(lang, "PROFILO TOPOGRAFICO", "TOPOGRAPHIC PROFILE"),
            f"{title} - {_profile_source_summary(profile, lang)}",
        )
        _page_picture(layout, page_index, image_path, 12.0, 40.0, page_w - 24.0, page_h - 52.0)


def build_and_export_layout(layer, rect, map_settings, settings):
    asset_dir = None
    try:
        project = QgsProject.instance()
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()

        fmt = settings.get("format", "A4")
        orientation = settings.get("orientation", "Landscape")
        page_w, page_h = get_page_dimensions(fmt, orientation)
        metrics = get_layout_metrics(fmt)

        page = layout.pageCollection().page(0)
        page.setPageSize(QgsLayoutSize(page_w, page_h, QgsUnitTypes.LayoutMillimeters))
        layout.setName(f"Q-Press_{datetime.now().strftime('%Y%m%d%H%M%S')}")

        output_dir = settings.get("output_dir")
        if not output_dir:
            raise Exception(_text(settings.get("language", "it"),
                            "Directory di output non valida.", "Invalid output directory."))
        os.makedirs(output_dir, exist_ok=True)
        asset_dir = tempfile.mkdtemp(prefix="qpress_assets_")
        lang = settings.get("language", "it")
        settings["_attributes_in_titleblock"] = False

        margin = metrics["margin"]
        gap = metrics["gap"]
        cartiglio_pos = settings.get("cartiglio_pos", "Laterale Destro")
        is_horizontal = cartiglio_pos == "bottom" or "Inferiore" in str(cartiglio_pos)

        if is_horizontal:
            panel_h = min(metrics.get("panel_bottom", metrics["panel"]), page_h * 0.32)
            map_x = margin
            map_y = margin
            map_w = page_w - (2 * margin)
            map_h = page_h - (2 * margin) - panel_h - gap
            panel_x = margin
            panel_y = map_y + map_h + gap
            panel_w = map_w
        else:
            panel_w = min(metrics["panel"], page_w * 0.34)
            map_x = margin
            map_y = margin
            map_w = page_w - (2 * margin) - panel_w - gap
            map_h = page_h - (2 * margin)
            panel_x = map_x + map_w + gap
            panel_y = margin
            panel_h = map_h

        if map_w <= 20.0 or map_h <= 20.0 or panel_w <= 20.0 or panel_h <= 20.0:
            raise Exception(
                _text(
                    lang,
                    "Geometria layout non valida: spazio insufficiente per mappa e cartiglio.",
                    "Invalid layout geometry: insufficient space for map and title block.",
                )
            )

        # Sfondo area di composizione.
        _add_rectangle(
            layout,
            margin - 2.0,
            margin - 2.0,
            page_w - (2 * margin) + 4.0,
            page_h - (2 * margin) + 4.0,
            QColor(255, 255, 255),
            QColor(17, 24, 39),
            0.25,
        )

        extent = _resolve_extent(layer, rect)

        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(map_x, map_y, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters))
        _configure_map_item(layout, map_item, extent, map_settings, project.crs())
        _add_rectangle(
            layout,
            map_x - 1.0,
            map_y - 1.0,
            map_w + 2.0,
            map_h + 2.0,
            QColor(0, 0, 0, 0),
            QColor(17, 24, 39),
            0.2,
        )

        interval = calculate_grid_interval(extent)
        _configure_primary_grid(map_item, interval, metrics["small"])
        _configure_secondary_grid(map_item, project.crs().authid(), interval, metrics["small"])
        _add_map_decorations(layout, map_item, metrics, map_x, map_y, map_h, lang)

        selection_features = _features_in_selection(
            layer, rect, map_settings) if layer.type() == QgsMapLayerType.VectorLayer else []
        dashboard_images = []
        chart_thumbnail = None
        dashboard_placement = settings.get("dashboard_placement", "Nel cartiglio (se possibile)")
        dashboard_in_titleblock = dashboard_placement in (
            "titleblock",
            "both",
            "Nel cartiglio (se possibile)",
            "Cartiglio + Stampe successive",
        )
        dashboard_in_pages = dashboard_placement in (
            "pages",
            "both",
            "Stampe successive",
            "Cartiglio + Stampe successive",
        )

        if (
            settings.get("dashboard_enabled", False) and
            layer.type() == QgsMapLayerType.VectorLayer and
            settings.get("dashboard_category_field", "")
        ):
            dashboard_images = build_dashboard_images(
                layer_name=layer.name(),
                features=selection_features,
                category_fields=settings.get(
                    "dashboard_category_fields",
                    [settings.get("dashboard_category_field", "")],
                ),
                value_field=settings.get("dashboard_value_field", ""),
                include_pie=settings.get("dashboard_include_pie", True),
                include_bar=settings.get("dashboard_include_bar", True),
                include_percent=settings.get("dashboard_include_percent", True),
                output_dir=asset_dir,
                chart_title=settings.get("dashboard_title", _text(
                    lang, "Dashboard cartografico", "Cartographic dashboard")),
                chart_subtitle=settings.get("dashboard_subtitle", ""),
                aggregation=settings.get("dashboard_aggregation", "Somma"),
                show_labels=settings.get("dashboard_show_labels", True),
                show_percentages=settings.get("dashboard_show_percentages", True),
                top_n=settings.get("dashboard_top_n", 10),
                sort_order=settings.get("dashboard_sort_order", "Valore decrescente"),
                language=lang,
            )
            if dashboard_images and dashboard_in_titleblock:
                chart_thumbnail = dashboard_images[0]["path"]

        scale_value = map_item.scale() if map_item.scale() > 0 else 0
        overview_context = _overview_context_extent(extent, layer, map_settings, project.crs(), 1.35)
        if is_horizontal:
            _build_horizontal_panel(
                layout,
                layer,
                map_item,
                panel_x,
                panel_y,
                panel_w,
                panel_h,
                metrics,
                settings,
                extent,
                scale_value,
                project.crs().authid(),
                overview_crs=project.crs(),
                map_settings=map_settings,
                overview_context=overview_context,
                lang=lang,
                chart_image_path=chart_thumbnail,
                selection_features=selection_features,
            )
        else:
            _build_vertical_panel(
                layout,
                layer,
                map_item,
                panel_x,
                panel_y,
                panel_w,
                panel_h,
                metrics,
                settings,
                extent,
                scale_value,
                project.crs().authid(),
                overview_crs=project.crs(),
                map_settings=map_settings,
                overview_context=overview_context,
                lang=lang,
                chart_image_path=chart_thumbnail,
                selection_features=selection_features,
            )

        if settings.get("export_attributes", False) and not settings.get("_attributes_in_titleblock", False):
            _add_attributes_page_to_layout(layout, layer, rect, map_settings, selection_features, lang)

        safe_name = safe_filename(layer.name())
        if dashboard_images and dashboard_in_pages:
            _add_dashboard_pages_to_layout(layout, dashboard_images, lang)
        if settings.get("topo_profile", False):
            profiles = settings.get("topo_profile_prebuilt")
            if profiles is None:
                profile_request = prepare_topographic_profile_request(layer, rect, map_settings, settings)
                profiles = build_topographic_profile_images(
                    profile_request["rect"],
                    profile_request["map_settings"],
                    asset_dir,
                    profile_request["titles"],
                    profile_line=profile_request["profile_line"],
                    profile_points=profile_request["profile_points"],
                    language=profile_request["language"],
                    source=profile_request["source"],
                    raster_layer_id=profile_request["raster_layer_id"],
                )
            if profiles:
                _add_topographic_profile_pages_to_layout(layout, profiles, lang)
            else:
                QgsMessageLog.logMessage(
                    _text(
                        lang,
                        "Profilo topografico non generato: dati altimetrici non disponibili.",
                        "Topographic profile not generated: elevation data unavailable.",
                    ),
                    "Q-Press",
                    Qgis.Warning,
                )

        output_filename = f"qpress_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        output_path = os.path.join(output_dir, output_filename)
        export_to_pdf(layout, output_path)
        if asset_dir and os.path.exists(asset_dir):
            shutil.rmtree(asset_dir, ignore_errors=True)
            asset_dir = None

        return output_path

    except Exception as e:
        if asset_dir and os.path.exists(asset_dir):
            shutil.rmtree(asset_dir, ignore_errors=True)
        QgsMessageLog.logMessage(
            f"Eccezione in build_and_export_layout: {str(e)}\n{traceback.format_exc()}",
            "Q-Press",
            Qgis.Critical,
        )
        raise e
