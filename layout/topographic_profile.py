import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapLayerType,
    QgsPointXY,
    QgsProject,
    QgsUnitTypes,
)  # noqa: E501
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen
from ..qt_compat import ensure_qfont_compat, ensure_qimage_compat, ensure_qpainter_compat, ensure_qt_compat

ensure_qt_compat(Qt)
ensure_qfont_compat(QFont)
ensure_qimage_compat(QImage)
ensure_qpainter_compat(QPainter)

DATASET_STACK = "eudem25m,srtm30m,aster30m,mapzen"
API_URL = f"https://api.opentopodata.org/v1/{DATASET_STACK}"
OPEN_TOPO_DATA_NAME = "OpenTopoData"
OPEN_TOPO_DATA_METHOD = "bilinear"
OPEN_TOPO_DATA_SPACING_M = 75.0
OPEN_TOPO_DATA_CHUNK_SIZE = 75
OPEN_TOPO_DATA_REQUEST_INTERVAL_SECONDS = 1.15
OPEN_TOPO_DATA_MAX_RETRIES = 2
OPEN_TOPO_DATA_MAX_RETRY_AFTER_SECONDS = 3.0
OPEN_TOPO_DATA_MAX_PRINT_SAMPLES = 220
_RATE_LIMIT_STATE = {
    "limit": None,
    "remaining": None,
    "reset_at": None,
    "retry_after_seconds": None,
    "last_status": None,
    "last_updated": None,
    "session_requests": 0,
}


class OpenTopoDataRateLimitError(RuntimeError):
    pass


def _text(language, italian, english):
    return english if language == "en" else italian


def _slug(text):
    return "".join(ch if ch.isalnum() else "_" for ch in str(text)).strip("_").lower() or "profile"


def _project_crs(map_settings):
    try:
        if isinstance(map_settings, QgsCoordinateReferenceSystem) and map_settings.isValid():
            return map_settings
    except Exception:
        pass
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


def _fallback_rect_line(rect):
    if rect.width() >= rect.height():
        y = (rect.yMinimum() + rect.yMaximum()) / 2.0
        return [QgsPointXY(rect.xMinimum(), y), QgsPointXY(rect.xMaximum(), y)]
    x = (rect.xMinimum() + rect.xMaximum()) / 2.0
    return [QgsPointXY(x, rect.yMinimum()), QgsPointXY(x, rect.yMaximum())]


def _map_distance(p1, p2):
    return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())


def _polyline_length(points):
    return sum(_map_distance(previous, current) for previous, current in zip(points[:-1], points[1:]))


def _sample_polyline(points, sample_count):
    clean_points = [_as_point_xy(point) for point in points if point is not None]
    if len(clean_points) < 2:
        return clean_points

    total_length = _polyline_length(clean_points)
    if total_length <= 0:
        return [clean_points[0], clean_points[-1]]

    sample_count = max(int(sample_count), 2)
    segment_lengths = [
        _map_distance(previous, current) for previous, current in zip(clean_points[:-1], clean_points[1:])
    ]
    sampled = []
    segment_index = 0
    covered = 0.0

    for index in range(sample_count):
        target = total_length * (index / float(sample_count - 1))
        while segment_index < len(segment_lengths) - 1 and covered + segment_lengths[segment_index] < target:
            covered += segment_lengths[segment_index]
            segment_index += 1

        start = clean_points[segment_index]
        end = clean_points[segment_index + 1]
        segment_length = max(segment_lengths[segment_index], 1e-12)
        ratio = min(max((target - covered) / segment_length, 0.0), 1.0)
        sampled.append(
            QgsPointXY(
                start.x() + ((end.x() - start.x()) * ratio),
                start.y() + ((end.y() - start.y()) * ratio),
            )
        )
    return sampled


def _to_wgs84(points, source_crs):
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if source_crs == wgs84:
        return points

    transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
    return [transform.transform(point) for point in points]


def _to_crs(points, source_crs, target_crs):
    if not target_crs or not target_crs.isValid() or source_crs == target_crs:
        return points
    transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
    return [transform.transform(point) for point in points]


def _haversine_km(p1, p2):
    radius_km = 6371.0088
    lat1 = math.radians(p1.y())
    lat2 = math.radians(p2.y())
    d_lat = lat2 - lat1
    d_lon = math.radians(p2.x() - p1.x())
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def _wgs84_distances(points):
    distances = [0.0]
    for previous, current in zip(points[:-1], points[1:]):
        distances.append(distances[-1] + _haversine_km(previous, current))
    return distances


def _adaptive_sample_count(points, source_crs, preferred_spacing_m):
    if not points or len(points) < 2:
        return 0
    try:
        wgs84_points = _to_wgs84(points, source_crs)
        length_m = _wgs84_distances(wgs84_points)[-1] * 1000.0
    except Exception:
        length_m = _polyline_length(points)
    spacing = max(float(preferred_spacing_m or OPEN_TOPO_DATA_SPACING_M), 1.0)
    return max(int(math.ceil(length_m / spacing)) + 1, 2)


def _profile_points(rect, source_crs, profile_line=None, profile_points=None, preferred_spacing_m=None):
    if profile_points and len(profile_points) >= 2:
        base_points = [_as_point_xy(point) for point in profile_points]
    elif profile_line:
        start, end = profile_line
        base_points = [_as_point_xy(start), _as_point_xy(end)]
    elif rect and not rect.isEmpty() and rect.isFinite():
        base_points = _fallback_rect_line(rect)
    else:
        return []

    sample_count = _adaptive_sample_count(base_points, source_crs, preferred_spacing_m or OPEN_TOPO_DATA_SPACING_M)
    return _sample_polyline(base_points, sample_count)


def _online_profile_points(rect, source_crs, profile_line=None, profile_points=None):
    points = _profile_points(
        rect,
        source_crs,
        profile_line=profile_line,
        profile_points=profile_points,
        preferred_spacing_m=OPEN_TOPO_DATA_SPACING_M,
    )
    if len(points) <= OPEN_TOPO_DATA_MAX_PRINT_SAMPLES:
        return points
    return _sample_polyline(points, OPEN_TOPO_DATA_MAX_PRINT_SAMPLES)


def _header_value(headers, names):
    for name in names:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value not in (None, ""):
            return str(value).strip()
    return None


def _parse_int_header(value):
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _retry_after_seconds(error):
    header = None
    try:
        header = error.headers.get("Retry-After")
    except Exception:
        header = None
    if not header:
        return OPEN_TOPO_DATA_REQUEST_INTERVAL_SECONDS
    try:
        return min(max(float(header), 0.0), OPEN_TOPO_DATA_MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    try:
        retry_time = parsedate_to_datetime(header)
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        wait = (retry_time - datetime.now(timezone.utc)).total_seconds()
        return min(max(wait, 0.0), OPEN_TOPO_DATA_MAX_RETRY_AFTER_SECONDS)
    except Exception:
        return OPEN_TOPO_DATA_REQUEST_INTERVAL_SECONDS


def _parse_retry_after_header(value):
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        retry_time = parsedate_to_datetime(value)
        if retry_time.tzinfo is None:
            retry_time = retry_time.replace(tzinfo=timezone.utc)
        return max((retry_time - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except Exception:
        return None


def _parse_reset_header(value):
    if not value:
        return None
    now = datetime.now(timezone.utc)
    try:
        numeric = float(value)
        if numeric > 1000000000:
            return datetime.fromtimestamp(numeric, timezone.utc)
        return now.timestamp() + max(numeric, 0.0)
    except ValueError:
        pass
    try:
        reset_at = parsedate_to_datetime(value)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        return reset_at
    except Exception:
        return None


def _capture_rate_limit_headers(headers, status_code=None):
    if headers is None:
        return

    limit = _parse_int_header(_header_value(headers, ("X-RateLimit-Limit", "RateLimit-Limit", "X-Rate-Limit-Limit")))
    remaining = _parse_int_header(
        _header_value(headers, ("X-RateLimit-Remaining", "RateLimit-Remaining", "X-Rate-Limit-Remaining"))
    )
    reset_at = _parse_reset_header(
        _header_value(headers, ("X-RateLimit-Reset", "RateLimit-Reset", "X-Rate-Limit-Reset"))
    )
    retry_after = _parse_retry_after_header(_header_value(headers, ("Retry-After", "X-Retry-After")))

    if limit is not None:
        _RATE_LIMIT_STATE["limit"] = limit
    if remaining is not None:
        _RATE_LIMIT_STATE["remaining"] = remaining
    if reset_at is not None:
        _RATE_LIMIT_STATE["reset_at"] = reset_at
    if retry_after is not None:
        _RATE_LIMIT_STATE["retry_after_seconds"] = retry_after
        if reset_at is None:
            _RATE_LIMIT_STATE["reset_at"] = datetime.now(timezone.utc).timestamp() + retry_after
    elif status_code != 429:
        _RATE_LIMIT_STATE["retry_after_seconds"] = None

    _RATE_LIMIT_STATE["last_status"] = status_code
    _RATE_LIMIT_STATE["last_updated"] = datetime.now(timezone.utc)


def _format_time(value, language):
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, timezone.utc)
        else:
            dt = value
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return ""


def opentopodata_quota_status(language="it"):
    state = _RATE_LIMIT_STATE
    session_requests = state.get("session_requests", 0)
    last_updated = state.get("last_updated")
    limit = state.get("limit")
    remaining = state.get("remaining")
    retry_after = state.get("retry_after_seconds")
    reset_at = _format_time(state.get("reset_at"), language)

    if limit is not None and remaining is not None:
        status = _text(
            language,
            f"Quota OpenTopoData nota: {remaining}/{limit} richieste residue.",
            f"Known OpenTopoData quota: {remaining}/{limit} requests remaining.",
        )
    else:
        status = _text(
            language,
            "Quota OpenTopoData non esposta dal servizio: monitoraggio basato su ultima risposta e richieste di sessione.",  # noqa: E501
            "OpenTopoData quota not exposed by the service: monitoring is based on the last response and session requests.",  # noqa: E501
        )

    details = [
        status,
        _text(
            language,
            f"Richieste inviate in questa sessione QGIS: {session_requests}.",
            f"Requests sent in this QGIS session: {session_requests}.",
        ),
    ]
    if retry_after:
        details.append(
            _text(
                language,
                f"Ultimo rate limit: attendere circa {int(math.ceil(retry_after))} s.",
                f"Last rate limit: wait about {int(math.ceil(retry_after))} s.",
            )
        )
    if reset_at:
        details.append(
            _text(language, f"Reset/riapertura stimata: {reset_at}.", f"Estimated reset/reopen: {reset_at}.")
        )
    if last_updated:
        updated = _format_time(last_updated, language)
        details.append(_text(language, f"Aggiornato alle {updated}.", f"Updated at {updated}."))
    return " ".join(details)


def _open_topodata_payload(request):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Forbidden URL scheme: {parsed.scheme}")

    for attempt in range(OPEN_TOPO_DATA_MAX_RETRIES + 1):
        try:
            _RATE_LIMIT_STATE["session_requests"] += 1
            with urllib.request.urlopen(request, timeout=25) as response:  # nosec B310
                _capture_rate_limit_headers(response.headers, getattr(response, "status", None))
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            _capture_rate_limit_headers(error.headers, error.code)
            if error.code != 429:
                raise
            wait_seconds = _retry_after_seconds(error)
            if attempt >= OPEN_TOPO_DATA_MAX_RETRIES:
                raise OpenTopoDataRateLimitError(
                    "OpenTopoData ha risposto con HTTP 429: troppe richieste. "
                    "Attendere qualche minuto oppure usare 'Genera Profilo da progetto' con un raster DTM/DEM locale."
                )
            if wait_seconds > OPEN_TOPO_DATA_MAX_RETRY_AFTER_SECONDS:
                raise OpenTopoDataRateLimitError(
                    "OpenTopoData richiede un'attesa prima di nuove richieste. "
                    "Il profilo online e' stato interrotto per non bloccare QGIS; usare un DTM/DEM locale o riprovare piu' tardi."  # noqa: E501
                )
            _responsive_sleep(wait_seconds)

    raise OpenTopoDataRateLimitError("OpenTopoData ha rifiutato temporaneamente le richieste.")


def _query_elevations(wgs84_points):
    elevations = []
    for start in range(0, len(wgs84_points), OPEN_TOPO_DATA_CHUNK_SIZE):
        if start > 0:
            _responsive_sleep(OPEN_TOPO_DATA_REQUEST_INTERVAL_SECONDS)
        chunk = wgs84_points[start:start + OPEN_TOPO_DATA_CHUNK_SIZE]
        locations = "|".join(f"{point.y():.7f},{point.x():.7f}" for point in chunk)
        query = urllib.parse.urlencode({"locations": locations, "interpolation": OPEN_TOPO_DATA_METHOD})
        request = urllib.request.Request(
            f"{API_URL}?{query}",
            headers={"User-Agent": "Q-Press-QGIS-Plugin/1.9.2"},
        )
        payload = _open_topodata_payload(request)

        if payload.get("status") != "OK":
            raise RuntimeError(payload.get("error", "Invalid OpenTopoData response."))

        for result in payload.get("results", []):
            elevation = result.get("elevation")
            elevations.append(float(elevation) if elevation is not None else None)
    return elevations


def _responsive_sleep(seconds):
    time.sleep(max(float(seconds or 0), 0.0))


def _project_raster_layer(layer_id):
    if not layer_id:
        return None
    layer = QgsProject.instance().mapLayer(layer_id)
    if layer and layer.type() == QgsMapLayerType.RasterLayer:
        return layer
    return None


def _raster_spacing_m(raster_layer):
    if not raster_layer:
        return OPEN_TOPO_DATA_SPACING_M
    try:
        pixel_size = max(
            abs(float(raster_layer.rasterUnitsPerPixelX())), abs(float(raster_layer.rasterUnitsPerPixelY()))
        )
    except Exception:
        return OPEN_TOPO_DATA_SPACING_M

    crs = raster_layer.crs()
    try:
        if crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
            return max(pixel_size * 111320.0, 1.0)
        factor = QgsUnitTypes.fromUnitToUnitFactor(crs.mapUnits(), QgsUnitTypes.DistanceMeters)
        if factor > 0:
            return max(pixel_size * factor, 1.0)
    except Exception:
        pass
    return max(pixel_size, 1.0)


def _sample_raster_elevations(points, source_crs, raster_layer, band=1):
    if not raster_layer:
        return []

    raster_crs = raster_layer.crs()
    raster_points = _to_crs(points, source_crs, raster_crs)
    provider = raster_layer.dataProvider()
    elevations = []

    for point in raster_points:
        try:
            sample_result = provider.sample(point, band)
            if isinstance(sample_result, tuple):
                value = sample_result[0]
                ok = bool(sample_result[1]) if len(sample_result) > 1 else True
            else:
                value = sample_result
                ok = True
            if not ok or value is None:
                elevations.append(None)
                continue
            elevation = float(value)
            elevations.append(elevation if math.isfinite(elevation) else None)
        except Exception:
            elevations.append(None)
    return elevations


def _format_number(value, decimals=1, language="it"):
    text = f"{value:,.{decimals}f}"
    if language == "en":
        return text
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _text_width_pixels(metrics, text):
    try:
        return metrics.horizontalAdvance(text)
    except AttributeError:
        return metrics.width(text)


def _wrap_text_pixels(text, metrics, width):
    lines = []
    for paragraph in str(text or "").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _text_width_pixels(metrics, candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
        if current:
            lines.append(current)
    return lines


def _limit_lines_word_safe(lines, max_lines, metrics, width):
    if len(lines) <= max_lines:
        return lines
    output = lines[:max_lines]
    base = " ".join(output[-1].split())
    while base and _text_width_pixels(metrics, f"{base}...") > width:
        words = base.split()
        if len(words) <= 1:
            base = ""
            break
        base = " ".join(words[:-1])
    output[-1] = f"{base}..." if base else "..."
    return output


def _draw_fitted_text(
    painter,
    rect,
    text,
    base_size,
    bold=False,
    color=None,
    align=Qt.AlignLeft | Qt.AlignVCenter,
    min_size=12,
    word_wrap=True,
):
    weight = QFont.Bold if bold else QFont.Normal
    min_size = max(int(min_size), 6)
    for size in range(int(base_size), min_size - 1, -1):
        font = QFont("Arial", size, weight)
        metrics = QFontMetricsF(font)
        lines = _wrap_text_pixels(text, metrics, rect.width()) if word_wrap else str(text or "").split("\n")
        line_height = metrics.lineSpacing()
        widest = max((_text_width_pixels(metrics, line) for line in lines), default=0.0)
        if len(lines) * line_height <= rect.height() and widest <= rect.width():
            painter.setFont(font)
            if color is not None:
                painter.setPen(QPen(color, 1))
            painter.drawText(rect, align, "\n".join(lines))
            return size

    font = QFont("Arial", min_size, weight)
    metrics = QFontMetricsF(font)
    lines = _wrap_text_pixels(text, metrics, rect.width()) if word_wrap else str(text or "").split("\n")
    max_lines = max(int(rect.height() / max(metrics.lineSpacing(), 1.0)), 1)
    lines = _limit_lines_word_safe(lines, max_lines, metrics, rect.width())
    painter.setFont(font)
    if color is not None:
        painter.setPen(QPen(color, 1))
    painter.drawText(rect, align, "\n".join(lines))
    return min_size


def _station_label(distance_km):
    meters = int(round(distance_km * 1000.0))
    return f"PK {meters // 1000}+{meters % 1000:03d}"


def _interpolate_elevation(valid, target_distance):
    if not valid:
        return None
    if target_distance <= valid[0][0]:
        return valid[0][1]
    if target_distance >= valid[-1][0]:
        return valid[-1][1]
    for (d1, z1), (d2, z2) in zip(valid[:-1], valid[1:]):
        if d1 <= target_distance <= d2:
            span = max(d2 - d1, 1e-12)
            ratio = (target_distance - d1) / span
            return z1 + ((z2 - z1) * ratio)
    return None


def _station_distances(max_dist_km):
    max_dist_m = max(max_dist_km * 1000.0, 1.0)
    step_m = _nice_profile_step(max_dist_m / 8.0)
    stations = []
    current = 0.0
    while current < max_dist_m:
        stations.append(current / 1000.0)
        current += step_m
    if not stations or abs(stations[-1] - max_dist_km) > max_dist_km * 0.02:
        stations.append(max_dist_km)
    return stations


def _limited_station_distances(max_dist_km, max_labels=9):
    stations = _station_distances(max_dist_km)
    if len(stations) <= max_labels:
        return stations
    step = max(int(math.ceil((len(stations) - 1) / float(max_labels - 1))), 1)
    limited = stations[::step]
    if limited[-1] != stations[-1]:
        limited.append(stations[-1])
    return limited[:max_labels]


def _nice_profile_step(raw_value):
    if raw_value <= 0:
        return 1.0
    exponent = math.floor(math.log10(raw_value))
    fraction = raw_value / (10**exponent)
    if fraction <= 1:
        base = 1
    elif fraction <= 2:
        base = 2
    elif fraction <= 5:
        base = 5
    else:
        base = 10
    return base * (10**exponent)


def _source_description(source_info, language):
    if not source_info:
        return ""
    source_type = source_info.get("type", "online")
    if source_type == "project":
        return _text(
            language,
            f"Fonte quote: raster progetto '{
                source_info.get(
                    'name',
                    '')}', banda {
                source_info.get(
                    'band',
                    1)}.",
            f"Elevation source: project raster '{
                source_info.get(
                    'name',
                    '')}', band {
                source_info.get(
                    'band',
                    1)}.",
        )
    dataset_display = DATASET_STACK.replace(",", ", ")
    return _text(
        language,
        f"Fonte quote: {OPEN_TOPO_DATA_NAME} API (api.opentopodata.org/v1); dataset {dataset_display}; interpolazione {OPEN_TOPO_DATA_METHOD}.",  # noqa: E501
        f"Elevation source: {OPEN_TOPO_DATA_NAME} API (api.opentopodata.org/v1); datasets {dataset_display}; {OPEN_TOPO_DATA_METHOD} interpolation.",  # noqa: E501
    )


def _render_profile(distances, elevations, output_dir, title, language="it", source_info=None):
    valid = [(distance, elevation) for distance, elevation in zip(distances, elevations) if elevation is not None]
    if len(valid) < 2:
        return None

    image = QImage(2400, 1400, QImage.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    ink = QColor(17, 24, 39)
    muted = QColor(75, 85, 99)
    axis = QColor(31, 41, 55)
    line = QColor(16, 93, 130)
    station = QColor(120, 113, 108)

    painter.setPen(QPen(ink, 4))
    painter.drawRect(32, 32, 2336, 1336)
    painter.setPen(QPen(ink, 1))
    painter.drawRect(52, 52, 2296, 1296)

    _draw_fitted_text(
        painter,
        QRectF(90, 66, 2220, 72),
        title,
        42,
        True,
        ink,
        Qt.AlignLeft | Qt.AlignVCenter,
        min_size=28,
        word_wrap=True,
    )
    trace_text = _text(
        language,
        "Tracciato profilo: linea indicata dall'utente o geometria lineare intercettata.",
        "Profile trace: user indicated line or intersected line geometry.",
    )
    source_text = f"{trace_text}\n{_source_description(source_info, language)}"
    _draw_fitted_text(
        painter,
        QRectF(90, 146, 2220, 104),
        source_text,
        22,
        False,
        muted,
        Qt.AlignLeft | Qt.AlignTop,
        min_size=16,
        word_wrap=True,
    )
    painter.setPen(QPen(ink, 1))
    painter.drawLine(90, 276, 2310, 276)

    left = 230
    top = 340
    width = 1880
    height = 540
    min_elev = min(elevation for _, elevation in valid)
    max_elev = max(elevation for _, elevation in valid)
    elev_padding = max((max_elev - min_elev) * 0.08, 2.0)
    min_axis = min_elev - elev_padding
    max_axis = max_elev + elev_padding
    elev_span = max(max_axis - min_axis, 1.0)
    max_dist = max(distance for distance, _ in valid)
    max_dist = max(max_dist, 0.001)

    painter.setPen(QPen(QColor(229, 231, 235), 2))
    for index in range(6):
        y = top + (height * index / 5)
        painter.drawLine(left, int(y), left + width, int(y))

    stations = _limited_station_distances(max_dist)
    dash_pen = QPen(QColor(168, 162, 158), 2)
    dash_pen.setStyle(Qt.DashLine)
    painter.setPen(dash_pen)
    for distance in stations:
        x = left + ((distance / max_dist) * width)
        painter.drawLine(int(x), top, int(x), top + height)

    painter.setPen(QPen(axis, 3))
    painter.drawLine(left, top + height, left + width, top + height)
    painter.drawLine(left, top, left, top + height)

    previous = None
    painter.setPen(QPen(line, 6))
    for distance, elevation in valid:
        x = left + ((distance / max_dist) * width)
        y = top + height - (((elevation - min_axis) / elev_span) * height)
        if previous:
            painter.drawLine(int(previous[0]), int(previous[1]), int(x), int(y))
        previous = (x, y)

    for index in range(6):
        distance = max_dist * index / 5
        x = left + (width * index / 5)
        _draw_fitted_text(
            painter,
            QRectF(x - 95, top + height + 20, 190, 34),
            f"{_format_number(distance, language=language)} km",
            19,
            False,
            axis,
            Qt.AlignCenter,
            min_size=13,
            word_wrap=False,
        )
    for index in range(6):
        elevation = min_axis + (elev_span * index / 5)
        y = top + height - (height * index / 5)
        _draw_fitted_text(
            painter,
            QRectF(55, y - 17, 145, 34),
            f"{_format_number(elevation, 0, language)} m",
            19,
            False,
            axis,
            Qt.AlignRight | Qt.AlignVCenter,
            min_size=13,
            word_wrap=False,
        )

    _draw_fitted_text(
        painter,
        QRectF(left, top + height + 58, width, 42),
        _text(language, "Distanza lungo profilo", "Distance Along Profile"),
        22,
        True,
        axis,
        Qt.AlignCenter,
        min_size=16,
        word_wrap=False,
    )
    painter.save()
    painter.translate(56, top + (height / 2))
    painter.rotate(-90)
    _draw_fitted_text(
        painter,
        QRectF(-230, 0, 460, 40),
        _text(language, "Quota sul livello del mare", "Elevation Above Sea Level"),
        22,
        True,
        axis,
        Qt.AlignCenter,
        min_size=15,
        word_wrap=False,
    )
    painter.restore()

    table_x = left
    table_y = 1030
    table_w = width
    table_h = 188
    label_w = 230
    row_h = table_h / 3.0
    painter.setPen(QPen(axis, 2))
    painter.drawRect(int(table_x), int(table_y), int(table_w), int(table_h))
    for row in range(1, 3):
        y = table_y + row_h * row
        painter.drawLine(int(table_x), int(y), int(table_x + table_w), int(y))
    painter.drawLine(int(table_x + label_w), int(table_y), int(table_x + label_w), int(table_y + table_h))

    row_labels = [
        _text(language, "Picchetto", "Stake"),
        _text(language, "Progressiva", "Chainage"),
        _text(language, "Quota", "Elevation"),
    ]
    for row, label in enumerate(row_labels):
        _draw_fitted_text(
            painter,
            QRectF(table_x + 10, table_y + (row * row_h), label_w - 20, row_h),
            label,
            18,
            True,
            axis,
            Qt.AlignLeft | Qt.AlignVCenter,
            min_size=13,
            word_wrap=False,
        )

    station_area_w = table_w - label_w
    column_count = max(len(stations), 1)
    column_w = station_area_w / column_count
    for index, distance in enumerate(stations):
        column_x = table_x + label_w + (index * column_w)
        if index > 0:
            painter.setPen(QPen(station, 1))
            painter.drawLine(int(column_x), int(table_y), int(column_x), int(table_y + table_h))
        station_elevation = _interpolate_elevation(valid, distance)
        cell_rect = QRectF(column_x + 4, table_y, max(column_w - 8, 20), row_h)
        _draw_fitted_text(
            painter,
            cell_rect,
            _station_label(distance),
            17,
            False,
            axis,
            Qt.AlignCenter,
            min_size=11,
            word_wrap=False,
        )
        _draw_fitted_text(
            painter,
            QRectF(column_x + 4, table_y + row_h, max(column_w - 8, 20), row_h),
            f"{_format_number(distance, 3, language)} km",
            17,
            False,
            axis,
            Qt.AlignCenter,
            min_size=11,
            word_wrap=False,
        )
        if station_elevation is not None:
            _draw_fitted_text(
                painter,
                QRectF(column_x + 4, table_y + (2 * row_h), max(column_w - 8, 20), row_h),
                f"{_format_number(station_elevation, 1, language)} m",
                17,
                False,
                axis,
                Qt.AlignCenter,
                min_size=11,
                word_wrap=False,
            )

    summary_parts = [
        f"{_text(language, 'Quota min', 'Min elevation')}: {_format_number(min_elev, 0, language)} m",
        f"{_text(language, 'Quota max', 'Max elevation')}: {_format_number(max_elev, 0, language)} m",
        f"{_text(language, 'Dislivello', 'Elevation gain')}: {_format_number(max_elev - min_elev, 0, language)} m",
        f"{_text(language, 'Lunghezza', 'Length')}: {_format_number(max_dist, language=language)} km",
    ]
    if source_info:
        sample_label = _text(language, "Campioni", "Samples")
        summary_parts.append(f"{sample_label}: {source_info.get('samples', len(valid))}")
    summary = "    ".join(summary_parts)
    _draw_fitted_text(
        painter,
        QRectF(230, 1250, 1880, 70),
        summary,
        20,
        True,
        ink,
        Qt.AlignCenter,
        min_size=14,
        word_wrap=True,
    )

    painter.end()

    filename = f"qpress_topographic_profile_{
        _slug(title)}_{
        datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    path = os.path.join(output_dir, filename)
    image.save(path, "PNG")
    return path


def _profile_data(rect, map_settings, profile_line=None, profile_points=None, source="online", raster_layer_id=None):
    has_trace = bool(profile_line) or bool(profile_points)
    if not has_trace and (not rect or rect.isEmpty() or not rect.isFinite()):
        return None

    source_crs = _project_crs(map_settings)
    raster_layer = _project_raster_layer(raster_layer_id) if source == "project" else None
    if source == "project" and not raster_layer:
        return None
    if raster_layer:
        points = _profile_points(
            rect,
            source_crs,
            profile_line=profile_line,
            profile_points=profile_points,
            preferred_spacing_m=_raster_spacing_m(raster_layer),
        )
    else:
        points = _online_profile_points(
            rect,
            source_crs,
            profile_line=profile_line,
            profile_points=profile_points,
        )
    if len(points) < 2:
        return None

    wgs84_points = _to_wgs84(points, source_crs)
    if raster_layer:
        elevations = _sample_raster_elevations(points, source_crs, raster_layer)
        source_info = {
            "type": "project",
            "name": raster_layer.name(),
            "layer_id": raster_layer.id(),
            "band": 1,
        }
    else:
        elevations = _query_elevations(wgs84_points)
        source_info = {
            "type": "online",
            "name": OPEN_TOPO_DATA_NAME,
            "url": API_URL,
            "datasets": DATASET_STACK,
            "interpolation": OPEN_TOPO_DATA_METHOD,
        }

    distances = _wgs84_distances(wgs84_points)
    source_info["samples"] = len(wgs84_points)

    return distances, elevations, len(wgs84_points), source_info


def build_topographic_profile_image(
    rect,
    map_settings,
    output_dir,
    title="Profilo topografico",
    profile_line=None,
    profile_points=None,
    language="it",
    source="online",
    raster_layer_id=None,
):
    try:
        data = _profile_data(
            rect,
            map_settings,
            profile_line=profile_line,
            profile_points=profile_points,
            source=source,
            raster_layer_id=raster_layer_id,
        )
    except OpenTopoDataRateLimitError as error:
        return {
            "path": "",
            "title": title,
            "source": OPEN_TOPO_DATA_NAME,
            "source_info": {
                "type": "online",
                "name": OPEN_TOPO_DATA_NAME,
                "url": API_URL,
                "datasets": DATASET_STACK,
                "interpolation": OPEN_TOPO_DATA_METHOD,
            },
            "samples": 0,
            "error": str(error),
        }
    if not data:
        return None

    distances, elevations, sample_count, source_info = data
    image_path = _render_profile(distances, elevations, output_dir, title, language, source_info)
    if not image_path:
        return None
    return {
        "path": image_path,
        "source": source_info.get("name", OPEN_TOPO_DATA_NAME),
        "source_info": source_info,
        "samples": sample_count,
    }


def build_topographic_profile_images(
    rect,
    map_settings,
    output_dir,
    titles,
    profile_line=None,
    profile_points=None,
    language="it",
    source="online",
    raster_layer_id=None,
):
    profile_titles = titles or [_text(language, "Profilo topografico", "Topographic profile")]
    try:
        data = _profile_data(
            rect,
            map_settings,
            profile_line=profile_line,
            profile_points=profile_points,
            source=source,
            raster_layer_id=raster_layer_id,
        )
    except OpenTopoDataRateLimitError as error:
        return [
            {
                "path": "",
                "title": profile_titles[0],
                "source": OPEN_TOPO_DATA_NAME,
                "source_info": {
                    "type": "online",
                    "name": OPEN_TOPO_DATA_NAME,
                    "url": API_URL,
                    "datasets": DATASET_STACK,
                    "interpolation": OPEN_TOPO_DATA_METHOD,
                },
                "samples": 0,
                "error": str(error),
            }
        ]
    if not data:
        return []

    distances, elevations, sample_count, source_info = data
    profiles = []
    for title in profile_titles:
        image_path = _render_profile(
            distances,
            elevations,
            output_dir,
            title or _text(language, "Profilo topografico", "Topographic profile"),
            language,
            source_info,
        )
        if not image_path:
            continue
        profiles.append(
            {
                "path": image_path,
                "title": title,
                "source": source_info.get("name", OPEN_TOPO_DATA_NAME),
                "source_info": source_info,
                "samples": sample_count,
            }
        )
    return profiles
