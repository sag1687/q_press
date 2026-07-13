import traceback
from qgis.core import QgsRectangle, QgsGeometry, Qgis
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from ..qt_compat import ensure_qt_compat

ensure_qt_compat(Qt)


class QpressMapTool(QgsMapTool):
    """
    QpressMapTool: Tool di QGIS per selezionare un'area di interesse sul
    canvas.

    Gestisce l'interazione utente tramite Shift + Mouse Drag per disegnare
    una rubber band e, al rilascio, emette l'estensione (QgsRectangle)
    nel CRS corrente del progetto.
    """

    # Segnale storico: emette solo l'estensione del drag.
    rectangleDrawn = pyqtSignal(QgsRectangle)
    # Segnale completo: emette estensione, punto iniziale e punto finale.
    drawCompleted = pyqtSignal(QgsRectangle, object, object)

    def __init__(self, canvas):
        """
        Inizializza il tool mappa.

        Args:
            canvas (QgsMapCanvas): Il canvas della mappa corrente.
        """
        super().__init__(canvas)
        self.canvas = canvas
        self.rubber_band = None
        self.start_point = None
        self.is_dragging = False
        self.draw_mode = "area"

    def set_draw_mode(self, mode):
        self.draw_mode = mode if mode in ("area", "profile") else "area"
        if self.rubber_band:
            self.rubber_band.reset()
            self.rubber_band = None

    def activate(self):
        """Attiva il tool sulla mappa e imposta il cursore personalizzato."""
        super().activate()
        self.canvas.setCursor(Qt.CrossCursor)
        self.is_dragging = False
        self.start_point = None

    def deactivate(self):
        """Disattiva il tool, ripulisce la rubber band e ripristina lo
        stato."""
        if self.rubber_band:
            self.rubber_band.reset()
            self.rubber_band = None
        self.is_dragging = False
        self.start_point = None
        super().deactivate()

    def _create_rubber_band(self):
        """Crea e configura la rubber band (rettangolo visivo) sul canvas."""
        geometry_type = (
            Qgis.GeometryType.Line
            if self.draw_mode == "profile"
            else Qgis.GeometryType.Polygon
        )
        rb = QgsRubberBand(self.canvas, geometry_type)
        if self.draw_mode == "profile":
            rb.setColor(QColor(220, 38, 38, 220))
            rb.setStrokeColor(QColor(220, 38, 38, 255))
            rb.setWidth(3)
        else:
            rb.setColor(QColor(59, 130, 246, 60))
            rb.setStrokeColor(QColor(59, 130, 246, 255))
            rb.setWidth(2)
        return rb

    def canvasPressEvent(self, e):
        """
        Gestisce l'evento di pressione del mouse.
        Avvia la selezione solo se il tasto Shift è premuto insieme al
        click sinistro.
        """
        try:
            if e.button() == Qt.LeftButton and (
                e.modifiers() & Qt.ShiftModifier
            ):
                self.is_dragging = True
                self.start_point = self.toMapCoordinates(e.pos())

                if not self.rubber_band:
                    self.rubber_band = self._create_rubber_band()

                self.rubber_band.reset()
        except Exception as ex:
            print(f"Errore in canvasPressEvent: {ex}")
            traceback.print_exc()

    def canvasMoveEvent(self, e):
        """
        Gestisce il movimento del mouse.
        Aggiorna la geometria della rubber band se l'utente sta trascinando.
        """
        try:
            if self.is_dragging and self.start_point:
                current_point = self.toMapCoordinates(e.pos())

                if self.draw_mode == "profile":
                    line = [self.start_point, current_point]
                    self.rubber_band.setToGeometry(
                        QgsGeometry.fromPolylineXY(line), None
                    )
                else:
                    rect = QgsRectangle(self.start_point, current_point)
                    self.rubber_band.setToGeometry(
                        QgsGeometry.fromRect(rect), None
                    )
        except Exception as ex:
            print(f"Errore in canvasMoveEvent: {ex}")
            traceback.print_exc()

    def canvasReleaseEvent(self, e):
        """
        Gestisce il rilascio del tasto del mouse.
        Finalizza il rettangolo, emette il segnale con il QgsRectangle e
        pulisce.
        """
        try:
            if e.button() == Qt.LeftButton and self.is_dragging:
                self.is_dragging = False
                end_point = self.toMapCoordinates(e.pos())

                # Calcola l'estensione selezionata
                rect = QgsRectangle(self.start_point, end_point)

                dx = abs(self.start_point.x() - end_point.x())
                dy = abs(self.start_point.y() - end_point.y())
                valid_drag = (
                    (dx > 0 or dy > 0)
                    if self.draw_mode == "profile"
                    else not rect.isEmpty()
                )
                if valid_drag:
                    self.rectangleDrawn.emit(rect)
                    self.drawCompleted.emit(rect, self.start_point, end_point)

                # Ripulisce la rubber band per un nuovo utilizzo
                if self.rubber_band:
                    self.rubber_band.reset()
                    self.rubber_band = None
        except Exception as ex:
            print(f"Errore in canvasReleaseEvent: {ex}")
            traceback.print_exc()
