import os
from collections import defaultdict
from datetime import datetime

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPen,
    QBrush,
)
from ..qt_compat import (
    ensure_qfont_compat,
    ensure_qimage_compat,
    ensure_qpainter_compat,
    ensure_qt_compat,
)

ensure_qt_compat(Qt)
ensure_qfont_compat(QFont)
ensure_qimage_compat(QImage)
ensure_qpainter_compat(QPainter)

PALETTE = [
    QColor(30, 64, 175),
    QColor(5, 120, 87),
    QColor(146, 64, 14),
    QColor(185, 28, 28),
    QColor(91, 33, 182),
    QColor(14, 116, 144),
    QColor(63, 98, 18),
    QColor(67, 56, 202),
    QColor(15, 118, 110),
    QColor(161, 98, 7),
]


def _text(language, italian, english):
    return english if language == "en" else italian


def _slug(text):
    return (
        "".join(ch if ch.isalnum() else "_" for ch in str(text))
        .strip("_")
        .lower()
        or "chart"
    )


def _safe_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(text, max_chars=28):
    text = " ".join(str(text).split())
    if len(text) <= max_chars:
        return text
    output = ""
    for word in text.split():
        candidate = word if not output else f"{output} {word}"
        if len(candidate) > max(max_chars - 3, 1):
            break
        output = candidate
    return f"{output}..." if output else "..."


def _format_number(value, decimals=2):
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _text_width(metrics, text):
    try:
        return metrics.horizontalAdvance(text)
    except AttributeError:
        return metrics.width(text)


def _draw_fitted_text(
    painter,
    rect,
    text,
    size,
    bold=False,
    color=None,
    align=Qt.AlignLeft | Qt.AlignVCenter,
    min_size=8,
):  # noqa: E501
    weight = QFont.Bold if bold else QFont.Normal
    raw = str(text or "")
    for font_size in range(int(size), int(min_size) - 1, -1):
        font = QFont("Arial", font_size, weight)
        metrics = QFontMetricsF(font)
        lines = []
        for paragraph in raw.split("\n"):
            words = paragraph.split()
            if not words:
                lines.append("")
                continue
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if _text_width(metrics, candidate) <= rect.width():
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
        if not lines:
            lines = [""]
        widest = max(
            (_text_width(metrics, line) for line in lines), default=0.0
        )
        if (
            widest <= rect.width()
            and (len(lines) * metrics.lineSpacing()) <= rect.height()
        ):
            painter.setFont(font)
            painter.setPen(QPen(color or QColor(17, 24, 39), 1))
            painter.drawText(rect, align, "\n".join(lines))
            return

    font = QFont("Arial", int(min_size), weight)
    metrics = QFontMetricsF(font)
    capacity = max(int(rect.width() / max(metrics.averageCharWidth(), 1.0)), 4)
    painter.setFont(font)
    painter.setPen(QPen(color or QColor(17, 24, 39), 1))
    painter.drawText(rect, align, _truncate(raw, capacity))


def _aggregate(
    features,
    category_field,
    value_field,
    aggregation,
    top_n,
    sort_order,
    language="it",
):
    stats = defaultdict(
        lambda: {"sum": 0.0, "count": 0, "min": None, "max": None}
    )

    for feature in features:
        try:
            category_value = feature[category_field]
        except Exception:  # nosec B112
            continue

        category_label = (
            str(category_value).strip() if category_value is not None else ""
        )
        category_label = category_label or _text(language, "N/D", "N/A")

        if value_field:
            try:
                numeric = _safe_float(feature[value_field])
            except Exception:
                numeric = None
            if numeric is None:
                continue
        else:
            numeric = 1.0

        bucket = stats[category_label]
        bucket["sum"] += numeric
        bucket["count"] += 1
        bucket["min"] = (
            numeric if bucket["min"] is None else min(bucket["min"], numeric)
        )
        bucket["max"] = (
            numeric if bucket["max"] is None else max(bucket["max"], numeric)
        )

    rows = []
    for label, bucket in stats.items():
        if aggregation in ("Media", "Average", "avg") and bucket["count"]:
            value = bucket["sum"] / bucket["count"]
        elif aggregation in ("Minimo", "Minimum", "min"):
            value = bucket["min"] if bucket["min"] is not None else 0.0
        elif aggregation in ("Massimo", "Maximum", "max"):
            value = bucket["max"] if bucket["max"] is not None else 0.0
        elif aggregation in ("Conteggio", "Count", "count"):
            value = float(bucket["count"])
        else:
            value = bucket["sum"]
        rows.append((label, value))

    if sort_order in ("Nome categoria", "Category Name", "name"):
        rows.sort(key=lambda item: item[0].lower())
    elif sort_order in ("Valore crescente", "Value Ascending", "value_asc"):
        rows.sort(key=lambda item: item[1])
    else:
        rows.sort(key=lambda item: item[1], reverse=True)

    if top_n and len(rows) > top_n:
        head = rows[: max(top_n - 1, 1)]
        tail_sum = sum(value for _, value in rows[max(top_n - 1, 1):])
        rows = head + [(_text(language, "Altri", "Other"), tail_sum)]

    total = sum(value for _, value in rows)
    return rows, total


def _base_canvas(
    title, subtitle, field_label, width=1800, height=1200, language="it"
):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    ink = QColor(17, 24, 39)
    muted = QColor(75, 85, 99)
    painter.setPen(QPen(ink, 3))
    painter.drawRect(24, 24, width - 48, height - 48)
    painter.setPen(QPen(ink, 1))
    painter.drawRect(38, 38, width - 76, height - 76)

    _draw_fitted_text(
        painter,
        QRectF(70, 58, width - 140, 52),
        title,
        28,
        True,
        ink,
        Qt.AlignLeft | Qt.AlignVCenter,
        18,
    )
    subtitle_text = subtitle or _text(
        language,
        "Distribuzione statistica sulle sole geometrie presenti nell'area "
        "selezionata",
        "Statistical distribution using only features inside the "
        "selected area",
    )
    _draw_fitted_text(
        painter,
        QRectF(70, 110, width - 140, 34),
        subtitle_text,
        14,
        False,
        muted,
        Qt.AlignLeft | Qt.AlignVCenter,
        10,
    )
    painter.setPen(QPen(ink, 1))
    painter.drawLine(70, 158, width - 70, 158)

    _draw_fitted_text(
        painter,
        QRectF(70, 166, width - 140, 30),
        f"{_text(language, 'Campo', 'Field')}: {field_label}",
        12,
        True,
        ink,
        Qt.AlignLeft | Qt.AlignVCenter,
        9,
    )
    return image, painter


def _save(image, output_dir, chart_type, layer_name, field_name):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"qpress_dashboard_{
        _slug(layer_name)}_{
        _slug(field_name)}_{chart_type}_{timestamp}.png"
    path = os.path.join(output_dir, filename)
    image.save(path, "PNG")
    return path


def _legend_text(label, value, total, show_percentages):
    if show_percentages and total > 0:
        percent = (value / total) * 100
        label_text = _truncate(label, 32)
        value_text = _format_number(value)
        return f"{label_text}  {value_text}  ({percent:.1f}%)"
    return f"{_truncate(label, 32)}  {_format_number(value)}"


def _render_pie(
    rows,
    total,
    layer_name,
    output_dir,
    field_name,
    title,
    subtitle,
    show_labels,
    show_percentages,
    language="it",
):  # noqa: E501
    image, painter = _base_canvas(
        title, subtitle, field_name, language=language
    )

    pie_rect = QRectF(120, 245, 640, 640)
    start_angle = 90 * 16
    for idx, (label, value) in enumerate(rows):
        ratio = 0 if total <= 0 else value / total
        span_angle = -int(round(360 * ratio * 16))
        painter.setBrush(QBrush(PALETTE[idx % len(PALETTE)]))
        painter.setPen(QPen(QColor(255, 255, 255), 3))
        painter.drawPie(pie_rect, start_angle, span_angle)
        start_angle += span_angle

    painter.setBrush(QBrush(QColor(255, 255, 255)))
    painter.setPen(QPen(QColor(255, 255, 255), 1))
    painter.drawEllipse(QRectF(315, 440, 250, 250))
    _draw_fitted_text(
        painter,
        QRectF(315, 500, 250, 44),
        _text(language, "Totale", "Total"),
        16,
        True,
        QColor(17, 24, 39),
        Qt.AlignCenter,
        11,
    )
    _draw_fitted_text(
        painter,
        QRectF(315, 546, 250, 50),
        _format_number(total),
        20,
        True,
        QColor(17, 24, 39),
        Qt.AlignCenter,
        12,
    )

    legend_x = 850
    for idx, (label, value) in enumerate(rows):
        y = 245 + (idx * 64)
        color = PALETTE[idx % len(PALETTE)]
        painter.fillRect(legend_x, y, 28, 28, color)
        painter.setPen(QPen(QColor(17, 24, 39), 1))
        painter.drawRect(legend_x, y, 28, 28)
        text = _legend_text(label, value, total, show_percentages)
        _draw_fitted_text(
            painter,
            QRectF(legend_x + 44, y - 2, 820, 34),
            text,
            15,
            False,
            QColor(17, 24, 39),
            Qt.AlignLeft | Qt.AlignVCenter,
            10,
        )

    if show_labels:
        _draw_fitted_text(
            painter,
            QRectF(120, 900, 1500, 36),
            _text(
                language,
                "Etichette e percentuali calcolate sui dati filtrati "
                "dall'area selezionata.",
                "Labels and percentages calculated on data filtered by the "
                "selected area.",
            ),
            11,
            False,
            QColor(75, 85, 99),
            Qt.AlignLeft | Qt.AlignVCenter,
            9,
        )

    painter.end()
    return _save(image, output_dir, "pie", layer_name, field_name)


def _render_bar(
    rows,
    total,
    layer_name,
    output_dir,
    field_name,
    title,
    subtitle,
    show_labels,
    show_percentages,
    language="it",
):  # noqa: E501
    del show_labels
    image, painter = _base_canvas(
        title, subtitle, field_name, language=language
    )

    chart_x = 420
    chart_y = 240
    chart_w = 1180
    row_h = 62
    max_value = max((value for _, value in rows), default=1.0)
    max_value = max(max_value, 1.0)

    for idx, (label, value) in enumerate(rows):
        y = chart_y + idx * row_h
        if y > 1030:
            break
        painter.setPen(QPen(QColor(17, 24, 39), 1))
        _draw_fitted_text(
            painter,
            QRectF(90, y, 300, 34),
            label,
            13,
            False,
            QColor(17, 24, 39),
            Qt.AlignRight | Qt.AlignVCenter,
            9,
        )
        painter.drawRect(chart_x, y + 5, chart_w, 28)
        fill_w = (value / max_value) * chart_w
        painter.fillRect(
            QRectF(chart_x, y + 5, fill_w, 28), PALETTE[idx % len(PALETTE)]
        )
        value_text = _format_number(value)
        if show_percentages and total > 0:
            value_text = f"{value_text} ({(value / total) * 100:.1f}%)"
        _draw_fitted_text(
            painter,
            QRectF(chart_x + chart_w + 18, y, 170, 34),
            value_text,
            13,
            False,
            QColor(17, 24, 39),
            Qt.AlignLeft | Qt.AlignVCenter,
            9,
        )

    painter.end()
    return _save(image, output_dir, "bar", layer_name, field_name)


def _render_percent(
    rows,
    total,
    layer_name,
    output_dir,
    field_name,
    title,
    subtitle,
    language="it",
):
    image, painter = _base_canvas(
        title, subtitle, field_name, language=language
    )

    left = 120
    top = 240
    row_h = 62
    bar_w = 1050

    for idx, (label, value) in enumerate(rows):
        y = top + idx * row_h
        if y > 1030:
            break
        pct = 0 if total <= 0 else (value / total) * 100
        painter.setPen(QPen(QColor(17, 24, 39), 1))
        _draw_fitted_text(
            painter,
            QRectF(left, y, 340, 34),
            label,
            14,
            False,
            QColor(17, 24, 39),
            Qt.AlignLeft | Qt.AlignVCenter,
            9,
        )
        painter.drawRect(int(left + 380), int(y + 5), int(bar_w), 28)
        fill_w = max(0, min(bar_w, (pct / 100.0) * bar_w))
        painter.fillRect(
            QRectF(left + 380, y + 5, fill_w, 28), PALETTE[idx % len(PALETTE)]
        )
        _draw_fitted_text(
            painter,
            QRectF(left + 380 + bar_w + 18, y, 190, 34),
            f"{pct:.2f}%".replace(".", ","),
            14,
            False,
            QColor(17, 24, 39),
            Qt.AlignLeft | Qt.AlignVCenter,
            9,
        )

    painter.end()
    return _save(image, output_dir, "percent", layer_name, field_name)


def build_dashboard_images(
    layer_name,
    features,
    category_fields,
    value_field,
    include_pie,
    include_bar,
    include_percent,
    output_dir,
    chart_title="Dashboard cartografico",
    chart_subtitle="",
    aggregation="Somma",
    show_labels=True,
    show_percentages=True,
    top_n=10,
    sort_order="Valore decrescente",
    language="it",
):
    if isinstance(category_fields, str):
        fields = [category_fields] if category_fields else []
    else:
        fields = [field for field in category_fields if field]

    images = []
    for field_name in fields:
        effective_aggregation = "count" if not value_field else aggregation
        rows, total = _aggregate(
            features,
            field_name,
            value_field,
            effective_aggregation,
            top_n,
            sort_order,
            language,
        )
        if not rows:
            continue

        title = chart_title or _text(
            language, "Dashboard cartografico", "Cartographic dashboard"
        )
        if len(fields) > 1:
            title = f"{title} - {field_name}"

        if include_pie:
            images.append(
                {
                    "type": "pie",
                    "field": field_name,
                    "path": _render_pie(
                        rows,
                        total,
                        layer_name,
                        output_dir,
                        field_name,
                        title,
                        chart_subtitle,
                        show_labels,
                        show_percentages,
                        language,
                    ),
                }
            )
        if include_bar:
            images.append(
                {
                    "type": "bar",
                    "field": field_name,
                    "path": _render_bar(
                        rows,
                        total,
                        layer_name,
                        output_dir,
                        field_name,
                        title,
                        chart_subtitle,
                        show_labels,
                        show_percentages,
                        language,
                    ),
                }
            )
        if include_percent:
            images.append(
                {
                    "type": "percent",
                    "field": field_name,
                    "path": _render_percent(
                        rows,
                        total,
                        layer_name,
                        output_dir,
                        field_name,
                        title,
                        chart_subtitle,
                        language,
                    ),
                }
            )
    return images
