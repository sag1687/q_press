import os
import shutil
import tempfile
import traceback

from qgis.core import QgsApplication, QgsTask, Qgis
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QDialog, QProgressDialog
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from .qt_compat import ensure_qdialog_compat, ensure_qt_compat
from .tools.map_tool import QpressMapTool
from .layout.layout_builder import build_and_export_layout, prepare_topographic_profile_request
from .layout.topographic_profile import build_topographic_profile_images
from .dialogs.settings_dialog import SettingsDialog

ensure_qt_compat(Qt)
ensure_qdialog_compat(QDialog)


class QPressProfileTask(QgsTask):
    """Background task for elevation sampling and profile image rendering."""

    def __init__(self, request, callback):
        super().__init__("Q-Press - Profilo topografico", QgsTask.CanCancel)
        self.request = request
        self.callback = callback
        self.asset_dir = tempfile.mkdtemp(prefix="qpress_profile_")
        self.profiles = []
        self.error = None

    def run(self):
        try:
            if self.isCanceled():
                return False
            self.setProgress(5)
            self.profiles = build_topographic_profile_images(
                self.request["rect"],
                self.request["map_settings"],
                self.asset_dir,
                self.request["titles"],
                profile_line=self.request["profile_line"],
                profile_points=self.request["profile_points"],
                language=self.request["language"],
                source=self.request["source"],
                raster_layer_id=self.request["raster_layer_id"],
            )
            self.setProgress(100)
            return not self.isCanceled()
        except Exception as error:
            self.error = error
            self.profiles = [
                {
                    "path": "",
                    "title": (self.request.get("titles") or ["Profilo topografico"])[0],
                    "error": str(error),
                }
            ]
            return True

    def finished(self, result):
        self.callback(result, self)


class QPressPlugin:
    """Entry point for the Q-Press plugin."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = __file__
        self.action = None
        self.map_tool = None
        self.capture_mode = "area"
        self.pending_export = None
        self.language = "it"
        self.profile_task = None
        self.profile_progress = None

    def _text(self, italian, english, language=None):
        return english if (language or self.language) == "en" else italian

    def _message_level(self, name):
        message_level = getattr(Qgis, "MessageLevel", None)
        if message_level is not None and hasattr(message_level, name):
            return getattr(message_level, name)
        return getattr(Qgis, name, getattr(Qgis, "Info", 0))

    def _push_message(self, message, level_name="Info", duration=5):
        level = self._message_level(level_name)
        message_bar = self.iface.messageBar()
        try:
            message_bar.pushMessage("Q-Press", message, level, duration)
        except TypeError:
            message_bar.pushMessage("Q-Press", message, level)

    def initGui(self):
        """Create the menu entries and toolbar icons."""
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "qpress_icon.svg")
        self.action = QAction(
            QIcon(icon_path),
            self._text("Q-Press: Area in PDF", "Q-Press: Area to PDF"),
            self.iface.mainWindow(),
        )
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Q-Press", self.action)

        self.map_tool = QpressMapTool(self.iface.mapCanvas())
        self.map_tool.drawCompleted.connect(self.on_draw_completed)

    def unload(self):
        """Remove the plugin menu item and icon."""
        self.iface.removePluginMenu("&Q-Press", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.map_tool:
            self.map_tool.deactivate()
        if self.profile_task:
            self.profile_task.cancel()

    def run(self):
        """Run method that activates the map tool."""
        self.capture_mode = "area"
        self.pending_export = None
        self.map_tool.set_draw_mode("area")
        self.iface.mapCanvas().setMapTool(self.map_tool)
        self._push_message(
            self._text("Shift + Drag per selezionare l'area", "Shift + Drag to select the map area"),
        )

    def on_draw_completed(self, rect, start_point, end_point):
        """Handle the extent drawn by the user. Defers execution to prevent Qt event loop crashes."""
        self.iface.mapCanvas().unsetMapTool(self.map_tool)
        self.iface.mapCanvas().setCursor(Qt.ArrowCursor)

        # FIX: Esecuzione differita per evitare crash quando si apre un dialog
        # modale da un evento del mouse
        if self.capture_mode == "profile":
            QTimer.singleShot(0, lambda: self.process_profile_export(rect, start_point, end_point))
        else:
            QTimer.singleShot(0, lambda: self.process_export(rect))

    def on_rectangle_drawn(self, rect):
        self.on_draw_completed(rect, None, None)

    def process_export(self, rect):
        layer = self.iface.activeLayer()
        if not layer:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Q-Press",
                self._text("Seleziona un layer attivo.", "Select an active layer."),
            )
            return

        dialog = SettingsDialog(
            self.iface.mainWindow(),
            layer=layer,
            selection_extent=rect,
            map_settings=self.iface.mapCanvas().mapSettings(),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        settings = dialog.get_settings()
        self.language = settings.get("language", "it")
        if self.action:
            self.action.setText(self._text("Q-Press: Area in PDF", "Q-Press: Area to PDF"))
        if not settings["output_dir"]:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Q-Press",
                self._text("Devi selezionare una cartella di destinazione.", "Select an output folder."),
            )
            return
        needs_project_raster = all((
            settings.get("topo_profile", False),
            settings.get("topo_profile_source") == "project",
            not settings.get("topo_profile_raster_id"),
        ))
        if needs_project_raster:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Q-Press",
                self._text(
                    "Per generare il profilo da progetto devi selezionare un raster DTM/DEM caricato nel progetto.",
                    "To generate the project profile, select a DTM/DEM raster loaded in the project.",
                ),
            )
            return

        if settings.get("topo_profile", False):
            self.pending_export = {
                "layer": layer,
                "map_rect": rect,
                "map_settings": self.iface.mapCanvas().mapSettings(),
                "settings": settings,
            }
            self.capture_mode = "profile"
            self.map_tool.set_draw_mode("profile")
            self.iface.mapCanvas().setMapTool(self.map_tool)
            self._push_message(
                self._text(
                    "Profilo topografico: esegui un secondo Shift + Drag lungo la direzione del profilo",
                    "Topographic profile: perform a second Shift + Drag along the profile direction",
                ),
            )
            return

        self._run_export(layer, rect, settings)

    def process_profile_export(self, profile_rect, start_point, end_point):
        pending = self.pending_export
        self.pending_export = None
        self.capture_mode = "area"
        self.map_tool.set_draw_mode("area")
        if not pending:
            return

        settings = pending["settings"]
        settings["topo_profile_rect"] = profile_rect
        if start_point is not None and end_point is not None:
            settings["topo_profile_line"] = (start_point, end_point)

        self._run_export(
            pending["layer"],
            pending["map_rect"],
            settings,
            pending.get("map_settings"),
        )

    def _run_export(self, layer, rect, settings, map_settings=None):
        map_settings = map_settings or self.iface.mapCanvas().mapSettings()
        if settings.get("topo_profile", False) and "topo_profile_prebuilt" not in settings:
            self._run_profile_task(layer, rect, settings, map_settings)
            return

        self._run_layout_export(layer, rect, settings, map_settings)

    def _run_profile_task(self, layer, rect, settings, map_settings):
        language = settings.get("language", self.language)
        try:
            request = prepare_topographic_profile_request(layer, rect, map_settings, settings)
        except Exception as error:
            fallback_title = settings.get("topo_profile_single_title")
            if not fallback_title:
                fallback_title = self._text("Profilo topografico", "Topographic profile", language)
            settings["topo_profile_prebuilt"] = [
                {
                    "path": "",
                    "title": fallback_title,
                    "error": str(error),
                }
            ]
            self._run_layout_export(layer, rect, settings, map_settings)
            return

        progress = QProgressDialog(
            self._text("Preparazione profilo topografico...", "Preparing topographic profile...", language),
            self._text("Annulla", "Cancel", language),
            0,
            100,
            self.iface.mainWindow(),
        )
        progress.setWindowTitle("Q-Press")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        def on_finished(result, task):
            self.profile_task = None
            if self.profile_progress:
                self.profile_progress.close()
                self.profile_progress = None

            if not result or task.isCanceled():
                shutil.rmtree(task.asset_dir, ignore_errors=True)
                self._push_message(
                    self._text("Generazione profilo annullata.", "Profile generation canceled.", language),
                    "Warning",
                )
                return

            settings["topo_profile_prebuilt"] = task.profiles
            settings["_topo_profile_asset_dir"] = task.asset_dir
            self._run_layout_export(layer, rect, settings, map_settings)

        task = QPressProfileTask(request, on_finished)
        task.progressChanged.connect(lambda value: progress.setValue(int(value)))
        progress.canceled.connect(task.cancel)
        self.profile_task = task
        self.profile_progress = progress
        QgsApplication.taskManager().addTask(task)

    def _run_layout_export(self, layer, rect, settings, map_settings):
        language = settings.get("language", self.language)
        self._push_message(
            self._text("Generazione layout in corso...", "Generating layout...", language),
        )
        try:
            output_path = build_and_export_layout(
                layer,
                rect,
                map_settings,
                settings,
            )
            self._push_message(
                self._text(
                    f"Esportazione completata: {output_path}",
                    f"Export completed: {output_path}",
                    language,
                ),
                "Success",
            )
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(
                self.iface.mainWindow(),
                self._text("Errore Q-Press", "Q-Press Error", language),
                str(e),
            )
        finally:
            asset_dir = settings.pop("_topo_profile_asset_dir", None)
            if asset_dir:
                shutil.rmtree(asset_dir, ignore_errors=True)
