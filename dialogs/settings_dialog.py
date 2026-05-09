import math
import os
from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMapLayerType,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsUnitTypes,
)
from ..layout.topographic_profile import (
    API_URL,
    DATASET_STACK,
    OPEN_TOPO_DATA_CHUNK_SIZE,
    OPEN_TOPO_DATA_MAX_PRINT_SAMPLES,
    OPEN_TOPO_DATA_METHOD,
    OPEN_TOPO_DATA_SPACING_M,
    opentopodata_quota_status,
)


class SettingsDialog(QDialog):
    def __init__(self, parent=None, layer=None, selection_extent=None, map_settings=None):
        super().__init__(parent)
        self.layer = layer
        self.selection_extent = selection_extent
        self.map_settings = map_settings
        self._recommended_layout = None
        self._updating_layout = False
        self.language = "it"
        self._translatable_widgets = []
        self._combo_models = {}
        self._tab_specs = []

        self.setWindowTitle("Q-Press - Configurazione Avanzata di Stampa")
        self.resize(820, 560)

        self.setStyleSheet(
            """
            QDialog { background-color: #0B192C; }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #1E3A5F;
                border-radius: 8px;
                margin-top: 15px;
                background-color: #112B4A;
                color: #E2E8F0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px;
                color: #60A5FA;
            }
            QLabel { color: #E2E8F0; font-size: 10pt; }
            QLabel#header { font-size: 18pt; font-weight: bold; color: #60A5FA; }
            QLabel#recommendationLabel {
                color: #BFDBFE;
                font-size: 9pt;
                background-color: #0F2743;
                border: 1px solid #1E3A5F;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QRadioButton { color: #E2E8F0; spacing: 8px; }
            QRadioButton::indicator { width: 18px; height: 18px; }
            QLineEdit, QComboBox {
                border: 1px solid #1E3A5F;
                border-radius: 4px;
                padding: 8px;
                background-color: #0B192C;
                color: #F8FAFC;
            }
            QComboBox::drop-down { border: none; width: 30px; }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #60A5FA;
                margin-right: 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #112B4A;
                color: #F8FAFC;
                selection-background-color: #2563EB;
                outline: none;
            }
            QListWidget {
                border: 1px solid #1E3A5F;
                border-radius: 4px;
                padding: 4px;
                background-color: #0B192C;
                color: #F8FAFC;
                outline: none;
            }
            QListWidget::item {
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            QPushButton {
                background-color: #2563EB;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3B82F6; }
            QPushButton#btnCancel { background-color: #1E3A5F; color: #E2E8F0; }
            QPushButton#btnCancel:hover { background-color: #2D4A77; }
            QPushButton#btnBrowse {
                background-color: #1E3A5F;
                color: #E2E8F0;
                border: 1px solid #3B82F6;
            }
            QPushButton#btnBrowse:hover { background-color: #2D4A77; }
            QLabel#previewLabel {
                background-color: #070F1A;
                border: 1px solid #1E3A5F;
                border-radius: 8px;
            }
            QTabWidget::pane {
                border: 1px solid #1E3A5F;
                background-color: #0B192C;
            }
            QTabBar::tab {
                background-color: #112B4A;
                color: #E2E8F0;
                border: 1px solid #1E3A5F;
                padding: 8px 14px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #2563EB;
                color: #FFFFFF;
            }
            QLabel#infoTitle { color: #60A5FA; font-size: 16pt; font-weight: bold; }
            QLabel#infoSubtle { color: #BFDBFE; font-size: 9pt; }
        """
        )

        outer_layout = QVBoxLayout()
        top_bar = QHBoxLayout()
        header = self._label("Q-PRESS: CONFIGURAZIONE AVANZATA", "Q-PRESS: ADVANCED PRINT SETUP")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignCenter)
        top_bar.addWidget(header, stretch=1)

        self.lbl_language = self._label("Lingua:", "Language:")
        self.combo_language = QComboBox()
        self.combo_language.addItem("Italiano", "it")
        self.combo_language.addItem("English", "en")
        self.combo_language.currentIndexChanged.connect(self._on_language_changed)
        top_bar.addWidget(self.lbl_language)
        top_bar.addWidget(self.combo_language)
        outer_layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        outer_layout.addWidget(self.tabs)

        config_tab = QWidget()
        config_tab_layout = QVBoxLayout()
        config_tab_layout.setContentsMargins(0, 0, 0, 0)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(15, 5, 15, 15)
        main_layout.setSpacing(15)

        left_col = QVBoxLayout()

        grp_aspect = self._group("Dati Cartiglio", "Title Block Data")
        lay_aspect = QVBoxLayout()
        lay_aspect.addWidget(self._label("Titolo:", "Title:"))
        self.title_input = QLineEdit("TAVOLA CARTOGRAFICA")
        lay_aspect.addWidget(self.title_input)

        lay_aspect.addWidget(self._label("Logo (Opzionale):", "Logo (Optional):"))
        h_logo = QHBoxLayout()
        self.logo_input = QLineEdit()
        btn_logo = self._button("Sfoglia", "Browse")
        btn_logo.setObjectName("btnBrowse")
        btn_logo.clicked.connect(self.browse_logo)
        h_logo.addWidget(self.logo_input)
        h_logo.addWidget(btn_logo)
        lay_aspect.addLayout(h_logo)
        grp_aspect.setLayout(lay_aspect)
        left_col.addWidget(grp_aspect)

        grp_format = self._group("Formato e Orientamento", "Format and Orientation")
        lay_format = QVBoxLayout()
        self.combo_format = QComboBox()
        self.combo_format.addItems(["A4", "A3", "A0"])
        self.combo_orientation = QComboBox()
        self._register_combo(
            self.combo_orientation,
            [
                ("Orizzontale (Landscape)", "Landscape", "Landscape"),
                ("Verticale (Portrait)", "Portrait", "Portrait"),
            ],
        )
        self.combo_cartiglio_pos = QComboBox()
        self._register_combo(
            self.combo_cartiglio_pos,
            [
                ("Laterale Destro", "Right Side", "right"),
                ("Orizzontale Inferiore", "Bottom", "bottom"),
            ],
        )

        lay_format.addWidget(self._label("Formato Foglio:", "Paper Size:"))
        lay_format.addWidget(self.combo_format)
        lay_format.addWidget(self._label("Orientamento:", "Orientation:"))
        lay_format.addWidget(self.combo_orientation)
        lay_format.addWidget(self._label("Posizione Cartiglio:", "Title Block Position:"))
        lay_format.addWidget(self.combo_cartiglio_pos)

        self.lbl_recommendation = QLabel("")
        self.lbl_recommendation.setObjectName("recommendationLabel")
        self.lbl_recommendation.setWordWrap(True)
        lay_format.addWidget(self.lbl_recommendation)

        grp_format.setLayout(lay_format)
        left_col.addWidget(grp_format)

        grp_export = self._group("Contenuto", "Content")
        lay_export = QVBoxLayout()
        self.radio_map_only = self._radio("Esporta Solo Mappa", "Export Map Only")
        self.radio_map_attr = self._radio("Esporta Mappa + Tabella Attributi", "Export Map + Attribute Table")
        self.radio_map_only.setChecked(True)
        lay_export.addWidget(self.radio_map_only)
        lay_export.addWidget(self.radio_map_attr)

        grp_export.setLayout(lay_export)
        left_col.addWidget(grp_export)

        grp_dest = self._group("Salvataggio", "Output")
        lay_dest = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setText(os.path.join(os.path.expanduser("~"), "Scrivania"))
        btn_dir = self._button("Cartella", "Folder")
        btn_dir.setObjectName("btnBrowse")
        btn_dir.clicked.connect(self.browse_dir)
        lay_dest.addWidget(self.dir_input)
        lay_dest.addWidget(btn_dir)
        grp_dest.setLayout(lay_dest)
        left_col.addWidget(grp_dest)

        left_col.addStretch()

        lay_btns = QHBoxLayout()
        btn_cancel = self._button("Annulla", "Cancel")
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.clicked.connect(self.reject)
        btn_ok = self._button("Genera PDF", "Generate PDF")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        lay_btns.addWidget(btn_cancel)
        lay_btns.addWidget(btn_ok)
        left_col.addLayout(lay_btns)

        main_layout.addLayout(left_col, stretch=1)

        right_col = QVBoxLayout()
        grp_preview = self._group("Anteprima Layout PDF", "PDF Layout Preview")
        lay_preview = QVBoxLayout()
        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("previewLabel")
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setMinimumSize(320, 420)
        lay_preview.addWidget(self.lbl_preview)
        grp_preview.setLayout(lay_preview)
        right_col.addWidget(grp_preview)
        main_layout.addLayout(right_col, stretch=1)

        config_tab_layout.addLayout(main_layout)
        config_tab.setLayout(config_tab_layout)
        self._add_tab(config_tab, "Configurazione", "Configuration")
        self._add_tab(self._build_profile_tab(), "Profilo", "Profile")
        self._add_tab(self._build_dashboard_tab(), "Dashboard", "Dashboard")
        self._add_tab(self._build_info_tab(), "Info", "Info")

        self.setLayout(outer_layout)

        self.combo_format.currentTextChanged.connect(self._on_layout_changed)
        self.combo_orientation.currentTextChanged.connect(self._on_layout_changed)
        self.combo_cartiglio_pos.currentTextChanged.connect(self._on_layout_changed)
        self.title_input.textChanged.connect(self._on_layout_changed)
        self.chk_dashboard.toggled.connect(self._toggle_chart_controls)
        self.chk_chart_pie.toggled.connect(self._ensure_one_chart_type)
        self.chk_chart_bar.toggled.connect(self._ensure_one_chart_type)
        self.chk_chart_percent.toggled.connect(self._ensure_one_chart_type)
        self.chk_topo_profile.toggled.connect(self._toggle_topo_controls)
        self.combo_topo_source.currentIndexChanged.connect(self._on_topo_source_changed)
        self.combo_topo_title_mode.currentIndexChanged.connect(self._on_topo_mode_changed)
        self.combo_topo_title_field.currentIndexChanged.connect(self._refresh_topo_entity_titles)

        self._populate_topo_fields()
        self._populate_topo_rasters()
        self._populate_topo_entities()
        self._toggle_topo_controls(self.chk_topo_profile.isChecked())
        self._populate_chart_fields()
        self._toggle_chart_controls(self.chk_dashboard.isChecked())
        self._apply_initial_optimal_layout()
        self._apply_language()
        self._on_layout_changed()

    def _resource_path(self, filename):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(base_dir, "resources", filename)

    def _tr(self, italian, english):
        return english if self.language == "en" else italian

    def _register_text(self, widget, italian, english):
        widget._qpress_i18n = (italian, english)
        self._translatable_widgets.append(widget)
        widget.setText(self._tr(italian, english))
        return widget

    def _label(self, italian, english):
        return self._register_text(QLabel(), italian, english)

    def _button(self, italian, english):
        return self._register_text(QPushButton(), italian, english)

    def _checkbox(self, italian, english):
        return self._register_text(QCheckBox(), italian, english)

    def _radio(self, italian, english):
        return self._register_text(QRadioButton(), italian, english)

    def _group(self, italian, english):
        group = QGroupBox()
        group._qpress_i18n = (italian, english)
        self._translatable_widgets.append(group)
        group.setTitle(self._tr(italian, english))
        return group

    def _register_combo(self, combo, rows):
        self._combo_models[combo] = rows
        self._refresh_combo(combo)
        return combo

    def _refresh_combo(self, combo):
        current_data = combo.currentData()
        rows = self._combo_models.get(combo, [])
        combo.blockSignals(True)
        combo.clear()
        for italian, english, data in rows:
            combo.addItem(self._tr(italian, english), data)
        if current_data is not None:
            index = combo.findData(current_data)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _add_tab(self, widget, italian, english):
        self.tabs.addTab(widget, self._tr(italian, english))
        self._tab_specs.append((widget, italian, english))

    def _on_language_changed(self, index):
        self.language = self.combo_language.itemData(index) or "it"
        self._apply_language()

    def _apply_language(self):
        self.setWindowTitle(self._tr("Q-Press - Configurazione Avanzata di Stampa", "Q-Press - Advanced Print Setup"))
        for widget in self._translatable_widgets:
            italian, english = widget._qpress_i18n
            if isinstance(widget, QGroupBox):
                widget.setTitle(self._tr(italian, english))
            else:
                widget.setText(self._tr(italian, english))
        for combo in self._combo_models:
            self._refresh_combo(combo)
        for index, (widget, italian, english) in enumerate(self._tab_specs):
            tab_index = self.tabs.indexOf(widget)
            if tab_index >= 0:
                self.tabs.setTabText(tab_index, self._tr(italian, english))
        if hasattr(self, "lbl_info_details"):
            self.lbl_info_details.setText(
                self._tr(
                    "<b>Autore:</b> Dott. Sarino Alfonso Grande<br/>"
                    "<b>Versione:</b> 1.8.0<br/>"
                    "<b>Email:</b> sino.grande@gmail.com",
                    "<b>Author:</b> Dott. Sarino Alfonso Grande<br/>"
                    "<b>Version:</b> 1.8.0<br/>"
                    "<b>Email:</b> sino.grande@gmail.com",
                )
            )
        if hasattr(self, "plugins_combo"):
            self.plugins_combo.setItemText(0, self._tr("Seleziona un plugin...", "Select a plugin..."))
        if hasattr(self, "combo_chart_value") and self.combo_chart_value.count() > 0:
            self.combo_chart_value.setItemText(0, self._tr("Conteggio geometrie", "Feature count"))
        if hasattr(self, "title_input"):
            current = self.title_input.text().strip()
            if current in ("TAVOLA CARTOGRAFICA", "CARTOGRAPHIC MAP"):
                self.title_input.setText(self._tr("TAVOLA CARTOGRAFICA", "CARTOGRAPHIC MAP"))
        if hasattr(self, "topo_single_title_input"):
            current = self.topo_single_title_input.text().strip()
            if current in ("Profilo topografico", "Topographic profile"):
                self.topo_single_title_input.setText(self._tr("Profilo topografico", "Topographic profile"))
        if hasattr(self, "combo_topo_source"):
            self._on_topo_source_changed()
        if hasattr(self, "chart_title_input"):
            current = self.chart_title_input.text().strip()
            if current in ("Dashboard cartografico", "Cartographic dashboard"):
                self.chart_title_input.setText(self._tr("Dashboard cartografico", "Cartographic dashboard"))
        if hasattr(self, "chart_subtitle_input"):
            current = self.chart_subtitle_input.text().strip()
            if current in ("Dati filtrati sull'area selezionata", "Data filtered on the selected area"):
                self.chart_subtitle_input.setText(
                    self._tr("Dati filtrati sull'area selezionata", "Data filtered on the selected area"))
        self._update_recommendation_label()
        self.update_preview()

    def _build_profile_tab(self):
        profile_tab = QWidget()
        profile_layout = QVBoxLayout()
        profile_layout.setContentsMargins(16, 16, 16, 16)
        profile_layout.setSpacing(12)

        grp_profile = self._group("Profilo topografico", "Topographic Profile")
        lay_profile = QVBoxLayout()

        self.chk_topo_profile = self._checkbox("Genera profilo topografico", "Generate topographic profile")
        self.chk_topo_profile.setChecked(False)
        lay_profile.addWidget(self.chk_topo_profile)

        lay_profile.addWidget(self._label("Sorgente quote:", "Elevation Source:"))
        self.combo_topo_source = QComboBox()
        self._register_combo(
            self.combo_topo_source,
            [
                ("OpenTopoData online", "Online OpenTopoData", "online"),
                ("Genera Profilo da progetto (DTM/DEM)", "Generate Profile from project (DTM/DEM)", "project"),
            ],
        )
        lay_profile.addWidget(self.combo_topo_source)

        lay_profile.addWidget(self._label("Raster DTM/DEM del progetto:", "Project DTM/DEM Raster:"))
        self.combo_topo_raster = QComboBox()
        lay_profile.addWidget(self.combo_topo_raster)

        self.lbl_topo_source_note = QLabel("")
        self.lbl_topo_source_note.setWordWrap(True)
        self.lbl_topo_source_note.setObjectName("infoSubtle")
        lay_profile.addWidget(self.lbl_topo_source_note)

        self.lbl_topo_quota = QLabel("")
        self.lbl_topo_quota.setWordWrap(True)
        self.lbl_topo_quota.setObjectName("infoSubtle")
        lay_profile.addWidget(self.lbl_topo_quota)

        self.lbl_topo_entities = QLabel("")
        self.lbl_topo_entities.setWordWrap(True)
        self.lbl_topo_entities.setObjectName("infoSubtle")
        lay_profile.addWidget(self.lbl_topo_entities)

        lay_profile.addWidget(self._label("Origine titolo profilo:", "Profile Title Source:"))
        self.combo_topo_title_mode = QComboBox()
        self._register_combo(
            self.combo_topo_title_mode,
            [
                ("Campo identita del layer", "Layer Identity Field", "field"),
                ("Titoli manuali per entita", "Manual Titles per Feature", "manual"),
                ("Titolo unico", "Single Title", "single"),
            ],
        )
        lay_profile.addWidget(self.combo_topo_title_mode)

        lay_profile.addWidget(self._label("Campo identita geometria:", "Geometry Identity Field:"))
        self.combo_topo_title_field = QComboBox()
        lay_profile.addWidget(self.combo_topo_title_field)

        lay_profile.addWidget(self._label("Titolo unico:", "Single Title:"))
        self.topo_single_title_input = QLineEdit("Profilo topografico")
        lay_profile.addWidget(self.topo_single_title_input)

        lay_profile.addWidget(self._label("Titoli candidati nel riquadro:", "Candidate Titles in Area:"))
        self.list_topo_titles = QListWidget()
        self.list_topo_titles.setMinimumHeight(150)
        lay_profile.addWidget(self.list_topo_titles)

        grp_profile.setLayout(lay_profile)
        profile_layout.addWidget(grp_profile)
        profile_layout.addStretch()
        profile_tab.setLayout(profile_layout)
        return profile_tab

    def _build_dashboard_tab(self):
        dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout()
        dashboard_layout.setContentsMargins(16, 16, 16, 16)
        dashboard_layout.setSpacing(12)

        grp_dashboard = self._group("Opzioni Dashboard Grafici", "Chart Dashboard Options")
        lay_dashboard = QVBoxLayout()

        self.chk_dashboard = self._checkbox("Genera dashboard (torta, barre, percentuali)",
                                            "Generate dashboard (pie, bar, percentages)")
        self.chk_dashboard.setChecked(False)
        lay_dashboard.addWidget(self.chk_dashboard)

        lay_dashboard.addWidget(self._label("Titolo grafici:", "Chart Title:"))
        self.chart_title_input = QLineEdit("Dashboard cartografico")
        lay_dashboard.addWidget(self.chart_title_input)

        lay_dashboard.addWidget(self._label("Sottotitolo / nota:", "Subtitle / Note:"))
        self.chart_subtitle_input = QLineEdit("Dati filtrati sull'area selezionata")
        lay_dashboard.addWidget(self.chart_subtitle_input)

        lay_dashboard.addWidget(self._label("Campi categoria da usare:", "Category Fields:"))
        self.list_chart_fields = QListWidget()
        self.list_chart_fields.setSelectionMode(QListWidget.MultiSelection)
        self.list_chart_fields.setMinimumHeight(120)
        lay_dashboard.addWidget(self.list_chart_fields)

        lay_dashboard.addWidget(self._label("Campo valore numerico (opzionale):", "Numeric Value Field (optional):"))
        self.combo_chart_value = QComboBox()
        lay_dashboard.addWidget(self.combo_chart_value)

        lay_dashboard.addWidget(self._label("Aggregazione valore:", "Value Aggregation:"))
        self.combo_chart_aggregation = QComboBox()
        self._register_combo(
            self.combo_chart_aggregation,
            [
                ("Somma", "Sum", "sum"),
                ("Media", "Average", "avg"),
                ("Minimo", "Minimum", "min"),
                ("Massimo", "Maximum", "max"),
                ("Conteggio", "Count", "count"),
            ],
        )
        lay_dashboard.addWidget(self.combo_chart_aggregation)

        self.chk_chart_pie = self._checkbox("Grafico a torta", "Pie Chart")
        self.chk_chart_pie.setChecked(True)
        self.chk_chart_bar = self._checkbox("Grafico a barre", "Bar Chart")
        self.chk_chart_bar.setChecked(True)
        self.chk_chart_percent = self._checkbox("Grafico percentuali", "Percentage Chart")
        self.chk_chart_percent.setChecked(True)
        lay_dashboard.addWidget(self.chk_chart_pie)
        lay_dashboard.addWidget(self.chk_chart_bar)
        lay_dashboard.addWidget(self.chk_chart_percent)

        self.chk_chart_labels = self._checkbox("Mostra etichette", "Show Labels")
        self.chk_chart_labels.setChecked(True)
        self.chk_chart_percent_labels = self._checkbox("Mostra percentuali", "Show Percentages")
        self.chk_chart_percent_labels.setChecked(True)
        lay_dashboard.addWidget(self.chk_chart_labels)
        lay_dashboard.addWidget(self.chk_chart_percent_labels)

        lay_dashboard.addWidget(self._label("Numero massimo categorie:", "Maximum Number of Categories:"))
        self.spin_chart_top_n = QSpinBox()
        self.spin_chart_top_n.setRange(3, 25)
        self.spin_chart_top_n.setValue(10)
        lay_dashboard.addWidget(self.spin_chart_top_n)

        lay_dashboard.addWidget(self._label("Ordinamento:", "Sort Order:"))
        self.combo_chart_sort = QComboBox()
        self._register_combo(
            self.combo_chart_sort,
            [
                ("Valore decrescente", "Value Descending", "value_desc"),
                ("Valore crescente", "Value Ascending", "value_asc"),
                ("Nome categoria", "Category Name", "name"),
            ],
        )
        lay_dashboard.addWidget(self.combo_chart_sort)

        lay_dashboard.addWidget(self._label("Destinazione grafici:", "Chart Placement:"))
        self.combo_chart_placement = QComboBox()
        self._register_combo(
            self.combo_chart_placement,
            [
                ("Nel cartiglio (se possibile)", "In Title Block (if possible)", "titleblock"),
                ("Stampe successive", "Following Pages", "pages"),
                ("Cartiglio + Stampe successive", "Title Block + Following Pages", "both"),
            ],
        )
        lay_dashboard.addWidget(self.combo_chart_placement)

        grp_dashboard.setLayout(lay_dashboard)
        dashboard_layout.addWidget(grp_dashboard)
        dashboard_layout.addStretch()
        dashboard_tab.setLayout(dashboard_layout)
        return dashboard_tab

    def _build_info_tab(self):
        info_tab = QWidget()
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(10)

        title = QLabel("Q-Press")
        title.setObjectName("infoTitle")
        title.setAlignment(Qt.AlignCenter)
        info_layout.addWidget(title)

        logo_label = QLabel()
        logo_label.setAlignment(Qt.AlignCenter)
        sarino_logo = self._resource_path("sarino_logo.jpg")
        if os.path.exists(sarino_logo):
            pixmap = QPixmap(sarino_logo)
            logo_label.setPixmap(
                pixmap.scaled(420, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        info_layout.addWidget(logo_label)

        self.lbl_info_details = QLabel()
        self.lbl_info_details.setAlignment(Qt.AlignCenter)
        self.lbl_info_details.setTextFormat(Qt.RichText)
        info_layout.addWidget(self.lbl_info_details)

        info_layout.addWidget(self._label("Plugin collegati:", "Linked Plugins:"))

        self.plugins_combo = QComboBox()
        self.plugins_combo.addItem(self._tr("Seleziona un plugin...", "Select a plugin..."))

        plugin_entries = [
            ("QGIS_ledger", "qgis_ledger_logo.jpg", "https://plugins.qgis.org/plugins/crs/"),
            ("GeoCSV Mapper", "geocsv_logo.svg", "https://plugins.qgis.org/plugins/csv_importer_plugin/"),
            ("Quick CRS Fixer", "quick_crs_fixer_logo.png", "https://plugins.qgis.org/plugins/crs/"),
        ]
        for label, icon_name, url in plugin_entries:
            icon_path = self._resource_path(icon_name)
            if os.path.exists(icon_path):
                self.plugins_combo.addItem(QIcon(icon_path), label, url)
            else:
                self.plugins_combo.addItem(label, url)

        self.plugins_combo.currentIndexChanged.connect(self.open_plugin_link)
        info_layout.addWidget(self.plugins_combo)

        subtle = self._label(
            "La selezione apre la pagina ufficiale del plugin nello store QGIS.",
            "The selection opens the official plugin page in the QGIS plugin store.",
        )
        subtle.setObjectName("infoSubtle")
        subtle.setAlignment(Qt.AlignCenter)
        subtle.setWordWrap(True)
        info_layout.addWidget(subtle)

        info_layout.addStretch()
        info_tab.setLayout(info_layout)
        return info_tab

    def open_plugin_link(self, index):
        url = self.plugins_combo.itemData(index)
        if url:
            QDesktopServices.openUrl(QUrl(url))
            self.plugins_combo.setCurrentIndex(0)

    def _selection_rect_for_layer(self):
        if not self.layer or not self.selection_extent or self.selection_extent.isEmpty():
            return None
        if self.layer.type() != QgsMapLayerType.VectorLayer:
            return None

        source_crs = QgsProject.instance().crs()
        if self.map_settings:
            try:
                source_crs = self.map_settings.destinationCrs()
            except Exception:
                source_crs = QgsProject.instance().crs()

        layer_crs = self.layer.crs()
        if not source_crs.isValid() or not layer_crs.isValid() or source_crs == layer_crs:
            return self.selection_extent

        try:
            transform = QgsCoordinateTransform(source_crs, layer_crs, QgsProject.instance())
            return transform.transformBoundingBox(self.selection_extent)
        except Exception:
            return self.selection_extent

    def _features_in_selection(self):
        layer_rect = self._selection_rect_for_layer()
        if layer_rect is None:
            return []

        request = QgsFeatureRequest().setFilterRect(layer_rect)
        rect_geom = QgsGeometry.fromRect(layer_rect)
        features = []
        for feature in self.layer.getFeatures(request):
            geom = feature.geometry()
            if geom and not geom.isEmpty() and geom.intersects(rect_geom):
                features.append(feature)
        return features

    def _populate_topo_fields(self):
        self.combo_topo_title_field.clear()
        self._topo_has_vector_titles = False
        if not self.layer or self.layer.type() != QgsMapLayerType.VectorLayer:
            self.combo_topo_title_field.addItem(self._tr("Layer non vettoriale", "Non-vector layer"), "")
            self.chk_topo_profile.setEnabled(True)
            return

        self._topo_has_vector_titles = True
        self.chk_topo_profile.setEnabled(True)
        for field in self.layer.fields():
            self.combo_topo_title_field.addItem(field.name(), field.name())
        if self.combo_topo_title_field.count() == 0:
            self.combo_topo_title_field.addItem(self._tr("FID geometria", "Geometry FID"), "")

    def _populate_topo_rasters(self):
        self.combo_topo_raster.clear()
        raster_layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if layer.type() == QgsMapLayerType.RasterLayer
        ]
        if not raster_layers:
            self.combo_topo_raster.addItem(self._tr("Nessun raster nel progetto", "No raster in project"), "")
            return
        for raster in sorted(raster_layers, key=lambda item: item.name().lower()):
            self.combo_topo_raster.addItem(raster.name(), raster.id())
        if self.layer and self.layer.type() == QgsMapLayerType.RasterLayer:
            raster_index = self.combo_topo_raster.findData(self.layer.id())
            if raster_index >= 0:
                self.combo_topo_raster.setCurrentIndex(raster_index)
            source_index = self.combo_topo_source.findData("project")
            if source_index >= 0:
                self.combo_topo_source.setCurrentIndex(source_index)

    def _identity_from_feature(self, feature):
        field_name = self.combo_topo_title_field.currentData()
        if field_name:
            try:
                value = feature[field_name]
                if value not in (None, ""):
                    return str(value)
            except Exception:
                pass

        for field in self.layer.fields():
            try:
                value = feature[field.name()]
                if value not in (None, ""):
                    return str(value)
            except Exception:
                pass
        return f"FID {feature.id()}"

    def _populate_topo_entities(self):
        self._topo_features = self._features_in_selection()
        self._refresh_topo_entity_titles()

    def _refresh_topo_entity_titles(self, *args):
        if not hasattr(self, "list_topo_titles"):
            return

        self.list_topo_titles.clear()
        features = getattr(self, "_topo_features", [])
        if not features:
            self.lbl_topo_entities.setText(
                self._tr(
                    "Nessuna geometria del layer attivo ricade nell'area selezionata.",
                    "No feature from the active layer falls inside the selected area.",
                )
            )
            return

        self.lbl_topo_entities.setText(
            self._tr(
                f"Geometrie candidate nell'area selezionata: {len(features)}. "
                "Il profilo finale usera solo la linea intercettata dal secondo tracciamento.",
                f"Candidate features in the selected area: {len(features)}. "
                "The final profile will use only the line intercepted by the second trace.",
            )
        )
        for feature in features:
            title = self._identity_from_feature(feature)
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, feature.id())
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.list_topo_titles.addItem(item)

    def _toggle_topo_controls(self, enabled):
        controls = [
            self.combo_topo_source,
            self.combo_topo_raster,
            self.lbl_topo_source_note,
            self.lbl_topo_quota,
            self.lbl_topo_entities,
            self.combo_topo_title_mode,
            self.combo_topo_title_field,
            self.topo_single_title_input,
            self.list_topo_titles,
        ]
        for control in controls:
            control.setEnabled(enabled)
        if enabled:
            self._on_topo_source_changed()
            self._on_topo_mode_changed()

    def _on_topo_source_changed(self, *args):
        if not hasattr(self, "combo_topo_source"):
            return
        enabled = self.chk_topo_profile.isChecked() if hasattr(self, "chk_topo_profile") else False
        source = self.combo_topo_source.currentData() or "online"
        has_raster = bool(self.combo_topo_raster.currentData()) if hasattr(self, "combo_topo_raster") else False
        if hasattr(self, "combo_topo_raster"):
            self.combo_topo_raster.setEnabled(enabled and source == "project" and has_raster)
        if not hasattr(self, "lbl_topo_source_note"):
            return
        if source == "project":
            self.lbl_topo_quota.setText(
                self._tr(
                    "Quota online non applicabile: il profilo usera il raster locale selezionato.",
                    "Online quota not applicable: the profile will use the selected local raster.",
                )
            )
            if has_raster:
                self.lbl_topo_source_note.setText(
                    self._tr(
                        "Le quote saranno campionate dal raster selezionato nel progetto QGIS, banda 1.",
                        "Elevations will be sampled from the selected QGIS project raster, band 1.",
                    )
                )
            else:
                self.lbl_topo_source_note.setText(
                    self._tr(
                        "Per usare il profilo da progetto aggiungi un raster DTM/DEM al progetto.",
                        "To use the project profile, add a DTM/DEM raster to the project.",
                    )
                )
            return
        self.lbl_topo_source_note.setText(
            self._tr(
                f"Le quote online vengono richieste a OpenTopoData: {API_URL}. Dataset: {DATASET_STACK}; interpolazione: {OPEN_TOPO_DATA_METHOD}. Il servizio pubblico puo rispondere HTTP 429 se usato troppo spesso.",  # noqa: E501
                f"Online elevations are requested from OpenTopoData: {API_URL}. Datasets: {DATASET_STACK}; interpolation: {OPEN_TOPO_DATA_METHOD}. The public service can return HTTP 429 when used too often.",  # noqa: E501
            )
        )
        self.lbl_topo_quota.setText(self._topo_quota_status_text())

    def _topo_quota_status_text(self):
        estimate = self._topo_online_request_estimate()
        return f"{opentopodata_quota_status(self.language)}\n{estimate}"

    def _topo_online_request_estimate(self):
        width_m = self._extent_width_in_meters()
        if not width_m:
            return self._tr(
                "Stima richieste profilo: non disponibile finche' non e' nota la scala del riquadro.",
                "Profile request estimate: unavailable until the area scale is known.",
            )

        samples = min(max(int(math.ceil(width_m / OPEN_TOPO_DATA_SPACING_M)) + 1, 2), OPEN_TOPO_DATA_MAX_PRINT_SAMPLES)
        requests = max(int(math.ceil(samples / float(OPEN_TOPO_DATA_CHUNK_SIZE))), 1)
        return self._tr(
            f"Stima orientativa sul riquadro: circa {requests} richiesta/e online ({samples} campioni). "
            "Il valore preciso dipende dalla seconda linea tracciata.",
            f"Area-based estimate: about {requests} online request(s) ({samples} samples). "
            "The exact value depends on the second traced line.",
        )

    def _on_topo_mode_changed(self, *args):
        mode = self.combo_topo_title_mode.currentData() or "field"
        use_field = mode in ("field", "manual")
        use_single = mode == "single"
        use_manual = mode == "manual"
        enabled = self.chk_topo_profile.isChecked()
        has_field_source = self.combo_topo_title_field.count() > 0 and getattr(self, "_topo_has_vector_titles", False)

        self.combo_topo_title_field.setEnabled(enabled and use_field and has_field_source)
        self.topo_single_title_input.setEnabled(enabled and use_single)
        self.list_topo_titles.setEnabled(enabled and has_field_source and (use_manual or use_field))
        if use_field and has_field_source:
            self._refresh_topo_entity_titles()

    def _topo_titles(self):
        if not self.chk_topo_profile.isChecked():
            return []

        mode = self.combo_topo_title_mode.currentData() or "field"
        if mode == "single":
            title = self.topo_single_title_input.text().strip()
            return [title or self._tr("Profilo topografico", "Topographic profile")]

        titles = []
        for index in range(self.list_topo_titles.count()):
            title = self.list_topo_titles.item(index).text().strip()
            if title:
                titles.append(title)

        if titles:
            return [titles[0]]

        fallback = self.topo_single_title_input.text().strip() or self._tr("Profilo topografico", "Topographic profile")
        return [fallback]

    def _topo_title_map(self):
        titles = {}
        for index in range(self.list_topo_titles.count()):
            item = self.list_topo_titles.item(index)
            feature_id = item.data(Qt.UserRole)
            title = item.text().strip()
            if feature_id is not None and title:
                titles[str(int(feature_id))] = title
        return titles

    def _is_numeric_field(self, field):
        try:
            if field.isNumeric():
                return True
        except Exception:
            pass
        type_name = field.typeName().lower()
        numeric_tokens = ("int", "real", "double", "float", "decimal", "numeric")
        return any(token in type_name for token in numeric_tokens)

    def _populate_chart_fields(self):
        self.list_chart_fields.clear()
        self.combo_chart_value.clear()
        self.combo_chart_value.addItem(self._tr("Conteggio geometrie", "Feature count"), "")

        if not self.layer or self.layer.type() != QgsMapLayerType.VectorLayer:
            self.list_chart_fields.addItem(QListWidgetItem(self._tr("Layer non vettoriale", "Non-vector layer")))
            self.chk_dashboard.setChecked(False)
            self.chk_dashboard.setEnabled(False)
            return

        self.chk_dashboard.setEnabled(True)
        for index, field in enumerate(self.layer.fields()):
            item = QListWidgetItem(field.name())
            item.setData(Qt.UserRole, field.name())
            self.list_chart_fields.addItem(item)
            if index == 0:
                item.setSelected(True)
            if self._is_numeric_field(field):
                self.combo_chart_value.addItem(field.name(), field.name())

        if self.list_chart_fields.count() == 0:
            self.list_chart_fields.addItem(QListWidgetItem(self._tr("Nessun campo disponibile", "No available field")))
            self.chk_dashboard.setChecked(False)
            self.chk_dashboard.setEnabled(False)

    def _toggle_chart_controls(self, enabled):
        controls = [
            self.chart_title_input,
            self.chart_subtitle_input,
            self.list_chart_fields,
            self.combo_chart_value,
            self.combo_chart_aggregation,
            self.chk_chart_pie,
            self.chk_chart_bar,
            self.chk_chart_percent,
            self.chk_chart_labels,
            self.chk_chart_percent_labels,
            self.spin_chart_top_n,
            self.combo_chart_sort,
            self.combo_chart_placement,
        ]
        for control in controls:
            control.setEnabled(enabled)

    def _ensure_one_chart_type(self):
        if self.chk_chart_pie.isChecked() or self.chk_chart_bar.isChecked() or self.chk_chart_percent.isChecked():
            return
        self.chk_chart_bar.setChecked(True)

    def browse_logo(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._tr("Seleziona Logo", "Select Logo"),
            "",
            self._tr("Immagini (*.png *.jpg *.jpeg *.svg)", "Images (*.png *.jpg *.jpeg *.svg)"),
        )
        if path:
            self.logo_input.setText(path)

    def browse_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            self._tr("Seleziona cartella", "Select folder"),
            self.dir_input.text(),
        )
        if path:
            self.dir_input.setText(path)

    def _get_page_dimensions(self, fmt, orientation_label):
        formats = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A0": (1189.0, 841.0)}
        page_w, page_h = formats.get(fmt, (297.0, 210.0))
        is_landscape = orientation_label == "Landscape" or "Landscape" in str(
            orientation_label) or "Orizzontale" in str(orientation_label)
        return (page_w, page_h) if is_landscape else (page_h, page_w)

    def _get_layout_metrics(self, fmt):
        if fmt == "A0":
            return {"margin": 20.0, "gap": 6.0, "panel": 400.0, "panel_bottom": 230.0}
        if fmt == "A3":
            return {"margin": 12.0, "gap": 4.0, "panel": 152.0, "panel_bottom": 96.0}
        return {"margin": 10.0, "gap": 3.0, "panel": 112.0, "panel_bottom": 82.0}

    def _map_space_for(self, fmt, orientation_label, cartiglio_label):
        geometry = self._layout_geometry(fmt, orientation_label, cartiglio_label)
        return geometry["map_w"], geometry["map_h"]

    def _layout_geometry(self, fmt, orientation_label, cartiglio_label):
        m = self._get_layout_metrics(fmt)
        page_w, page_h = self._get_page_dimensions(fmt, orientation_label)
        is_horizontal_cartiglio = cartiglio_label == "bottom" or "Inferiore" in str(cartiglio_label)
        if is_horizontal_cartiglio:
            panel_h = min(m.get("panel_bottom", m["panel"]), page_h * 0.32)
            panel_w = page_w - (2.0 * m["margin"])
            map_w = page_w - (2.0 * m["margin"])
            map_h = page_h - (2.0 * m["margin"]) - panel_h - m["gap"]
            panel_x = m["margin"]
            panel_y = m["margin"] + map_h + m["gap"]
        else:
            panel_w = min(m["panel"], page_w * 0.34)
            panel_h = page_h - (2.0 * m["margin"])
            map_w = page_w - (2.0 * m["margin"]) - panel_w - m["gap"]
            map_h = page_h - (2.0 * m["margin"])
            panel_x = m["margin"] + map_w + m["gap"]
            panel_y = m["margin"]

        return {
            "page_w": page_w,
            "page_h": page_h,
            "map_x": m["margin"],
            "map_y": m["margin"],
            "map_w": map_w,
            "map_h": map_h,
            "panel_x": panel_x,
            "panel_y": panel_y,
            "panel_w": panel_w,
            "panel_h": panel_h,
            "margin": m["margin"],
            "gap": m["gap"],
            "horizontal": is_horizontal_cartiglio,
        }

    def _extent_aspect(self):
        if not self.selection_extent or self.selection_extent.isEmpty():
            return 1.4
        w = max(self.selection_extent.width(), 0.0001)
        h = max(self.selection_extent.height(), 0.0001)
        return w / h

    def _extent_width_in_meters(self):
        if not self.selection_extent or self.selection_extent.isEmpty():
            return None

        width = self.selection_extent.width()
        if width <= 0:
            return None

        try:
            unit = self.map_settings.mapUnits() if self.map_settings else QgsUnitTypes.DistanceUnknownUnit
        except Exception:
            unit = QgsUnitTypes.DistanceUnknownUnit

        if unit == QgsUnitTypes.DistanceDegrees:
            lat = (self.selection_extent.yMinimum() + self.selection_extent.yMaximum()) / 2.0
            return width * 111320.0 * max(math.cos(math.radians(lat)), 0.01)

        try:
            factor = QgsUnitTypes.fromUnitToUnitFactor(unit, QgsUnitTypes.DistanceMeters)
            if factor > 0:
                return width * factor
        except Exception:
            pass

        return width

    def _estimate_scale(self, map_width_mm):
        width_m = self._extent_width_in_meters()
        if not width_m or map_width_mm <= 0:
            return None
        return width_m / (map_width_mm / 1000.0)

    def _recommend_layout(self):
        extent_aspect = self._extent_aspect()
        best = None

        formats = ["A4", "A3", "A0"]
        orientations = ["Landscape", "Portrait"]
        cartigli = ["right", "bottom"]

        for fmt in formats:
            for orientation in orientations:
                for cartiglio in cartigli:
                    map_w, map_h = self._map_space_for(fmt, orientation, cartiglio)
                    if map_w <= 0 or map_h <= 0:
                        continue

                    area = map_w * map_h
                    map_aspect = map_w / map_h
                    aspect_quality = 1.0 / (1.0 + abs(math.log(max(map_aspect, 0.01) / max(extent_aspect, 0.01))))

                    estimated_scale = self._estimate_scale(map_w)
                    if estimated_scale is None:
                        scale_quality = 1.0
                    elif estimated_scale <= 30000:
                        scale_quality = 0.9
                    elif estimated_scale <= 80000:
                        scale_quality = 0.9
                    elif estimated_scale <= 200000:
                        scale_quality = 0.75
                    else:
                        scale_quality = 0.6

                    text_quality = {"A4": 0.88, "A3": 1.0, "A0": 1.05}.get(fmt, 1.0)
                    if len(self.title_input.text().strip()) > 30 and fmt == "A4":
                        text_quality -= 0.08

                    if estimated_scale is not None and estimated_scale <= 30000:
                        size_quality = {"A4": 1.08, "A3": 1.0, "A0": 0.82}.get(fmt, 1.0)
                    elif estimated_scale is not None and estimated_scale > 150000:
                        size_quality = {"A4": 0.8, "A3": 0.98, "A0": 1.08}.get(fmt, 1.0)
                    else:
                        size_quality = {"A4": 0.95, "A3": 1.0, "A0": 0.92}.get(fmt, 1.0)

                    area_bonus = min(area / 120000.0, 1.35) * 0.03
                    score = (aspect_quality * scale_quality * text_quality * size_quality) + area_bonus
                    candidate = {
                        "format": fmt,
                        "orientation": orientation,
                        "cartiglio_pos": cartiglio,
                        "score": score,
                        "estimated_scale": estimated_scale,
                    }
                    if best is None or candidate["score"] > best["score"]:
                        best = candidate

        return best

    def _apply_initial_optimal_layout(self):
        recommendation = self._recommend_layout()
        if not recommendation:
            return
        self._recommended_layout = recommendation
        self._updating_layout = True
        self.combo_format.setCurrentText(recommendation["format"])
        orientation_index = self.combo_orientation.findData(recommendation["orientation"])
        if orientation_index >= 0:
            self.combo_orientation.setCurrentIndex(orientation_index)
        cartiglio_index = self.combo_cartiglio_pos.findData(recommendation["cartiglio_pos"])
        if cartiglio_index >= 0:
            self.combo_cartiglio_pos.setCurrentIndex(cartiglio_index)
        self._updating_layout = False

    def _format_scale_value(self, scale_value):
        try:
            return f"{int(round(scale_value)):,}".replace(",", ".")
        except Exception:
            return "n/d"

    def _orientation_label(self, value):
        if value == "Portrait":
            return self._tr("Verticale (Portrait)", "Portrait")
        return self._tr("Orizzontale (Landscape)", "Landscape")

    def _cartiglio_label(self, value):
        if value == "bottom":
            return self._tr("Orizzontale Inferiore", "Bottom")
        return self._tr("Laterale Destro", "Right Side")

    def _update_recommendation_label(self):
        recommendation = self._recommend_layout()
        self._recommended_layout = recommendation

        if not recommendation:
            self.lbl_recommendation.setText(
                self._tr("Suggerimento automatico non disponibile.", "Automatic recommendation unavailable.")
            )
            return

        current_fmt = self.combo_format.currentText()
        current_ori = self.combo_orientation.currentData() or "Landscape"
        current_cart = self.combo_cartiglio_pos.currentData() or "right"
        current_scale = self._estimate_scale(self._map_space_for(current_fmt, current_ori, current_cart)[0])
        current_scale_txt = self._format_scale_value(current_scale) if current_scale else "n/d"
        rec_scale_txt = (
            self._format_scale_value(recommendation["estimated_scale"])
            if recommendation["estimated_scale"]
            else "n/d"
        )

        is_current_recommended = (
            current_fmt == recommendation["format"]
            and current_ori == recommendation["orientation"]
            and current_cart == recommendation["cartiglio_pos"]
        )

        if is_current_recommended:
            self.lbl_recommendation.setText(
                self._tr(
                    "Formato ottimizzato applicato automaticamente.\n"
                    f"Scala stimata in stampa: 1:{current_scale_txt}",
                    "Optimized format applied automatically.\n"
                    f"Estimated print scale: 1:{current_scale_txt}",
                )
            )
        else:
            self.lbl_recommendation.setText(
                self._tr(
                    "Suggerimento automatico per migliore leggibilita:\n"
                    f"{recommendation['format']} - {self._orientation_label(recommendation['orientation'])} - "
                    f"{self._cartiglio_label(recommendation['cartiglio_pos'])}\n"
                    f"Scala stimata consigliata: 1:{rec_scale_txt} (attuale: 1:{current_scale_txt})",
                    "Automatic suggestion for better readability:\n"
                    f"{recommendation['format']} - {self._orientation_label(recommendation['orientation'])} - "
                    f"{self._cartiglio_label(recommendation['cartiglio_pos'])}\n"
                    f"Recommended estimated scale: 1:{rec_scale_txt} (current: 1:{current_scale_txt})",
                )
            )

    def _title_font_for_preview(self):
        fmt = self.combo_format.currentText()
        base = {"A4": 8, "A3": 11, "A0": 14}.get(fmt, 8)
        title_len = len(self.title_input.text().strip())
        if title_len > 36:
            base -= 2
        elif title_len > 24:
            base -= 1
        return max(base, 6)

    def _render_map_preview_image(self, width, height):
        if not self.map_settings or not self.selection_extent or self.selection_extent.isEmpty():
            return None

        try:
            settings = QgsMapSettings()
            layers = list(self.map_settings.layers())
            if not layers:
                return None

            settings.setLayers(layers)
            settings.setExtent(self.selection_extent)
            settings.setOutputSize(QSize(max(int(width), 32), max(int(height), 32)))
            settings.setBackgroundColor(QColor("#FBFDF8"))

            try:
                settings.setDestinationCrs(self.map_settings.destinationCrs())
            except Exception:
                pass
            try:
                settings.setTransformContext(QgsProject.instance().transformContext())
            except Exception:
                pass
            try:
                settings.setRotation(self.map_settings.rotation())
            except Exception:
                pass

            job = QgsMapRendererParallelJob(settings)
            job.start()
            job.waitForFinished()
            image = job.renderedImage()
            if image.isNull():
                return None
            return image
        except Exception:
            return None

    def update_preview(self):
        fmt = self.combo_format.currentText()
        geometry = self._layout_geometry(
            fmt,
            self.combo_orientation.currentData() or "Landscape",
            self.combo_cartiglio_pos.currentData() or "right",
        )
        page_w = geometry["page_w"]
        page_h = geometry["page_h"]
        canvas_w = max(self.lbl_preview.width(), 320)
        canvas_h = max(self.lbl_preview.height(), 420)
        scale = min((canvas_w - 24.0) / page_w, (canvas_h - 24.0) / page_h)
        draw_w = page_w * scale
        draw_h = page_h * scale
        ox = (canvas_w - draw_w) / 2.0
        oy = (canvas_h - draw_h) / 2.0

        def rect(x, y, w, h):
            return QRectF(ox + (x * scale), oy + (y * scale), w * scale, h * scale)

        def font(size, bold=False):
            px_size = max(int(size * 0.95), 5)
            return QFont("Arial", px_size, QFont.Bold if bold else QFont.Normal)

        def draw_text(text, area, size, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter, color=None):
            painter.setPen(color or QColor("#111827"))
            painter.setFont(font(size, bold))
            painter.drawText(area, align | Qt.TextWordWrap, text)

        def draw_logo(area):
            logo_path = self.logo_input.text().strip()
            if logo_path and os.path.exists(logo_path):
                logo_pixmap = QPixmap(logo_path)
                if not logo_pixmap.isNull():
                    target_size = area.size().toSize()
                    scaled_logo = logo_pixmap.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    logo_x = area.left() + ((area.width() - scaled_logo.width()) / 2.0)
                    logo_y = area.top() + ((area.height() - scaled_logo.height()) / 2.0)
                    painter.drawPixmap(int(logo_x), int(logo_y), scaled_logo)
                    return
            draw_text("LOGO", area, 6, True, Qt.AlignCenter, QColor("#475569"))

        def draw_scale_badge(area, scale_value):
            if area.width() <= 20 or area.height() <= 8:
                return
            painter.fillRect(area, QColor("#F8FAFC"))
            painter.setPen(QPen(QColor("#111827"), 0.7))
            painter.drawRect(area)
            pad = max(2.0, min(area.height() * 0.18, 5.0))
            icon_w = min(max(area.height() * 1.45, 12.0), area.width() * 0.25)
            body_h = max(area.height() * 0.24, 3.0)
            body_y = area.center().y() - (body_h / 2.0)
            body = QRectF(area.left() + pad, body_y, icon_w, body_h)
            segment_w = body.width() / 4.0
            for idx in range(4):
                segment = QRectF(body.left() + (idx * segment_w), body.top(), segment_w, body.height())
                painter.fillRect(segment, QColor("#111827") if idx % 2 == 0 else QColor("#FFFFFF"))
                painter.drawRect(segment)
            for idx in range(5):
                tick_h = body.height() * (1.6 if idx in (0, 4) else 1.1)
                tick_x = min(max(body.left() + (idx * segment_w), body.left()), body.right())
                painter.drawLine(QPointF(tick_x, body.top() - tick_h * 0.35), QPointF(tick_x, body.bottom()))
            text_left = body.right() + pad
            text_area = QRectF(text_left, area.top() + 1, max(area.right() - text_left - pad, 8), area.height() - 2)
            scale_label = self._tr("SCALA APPLICATA", "APPLIED SCALE")
            scale_number = f"1:{self._format_scale_value(scale_value)}" if scale_value else "1:n/d"
            if text_area.height() >= 16:
                draw_text(scale_label, QRectF(text_area.left(), text_area.top(), text_area.width(),
                          text_area.height() * 0.45), 5, True, Qt.AlignLeft | Qt.AlignVCenter, QColor("#475569"))
                draw_text(scale_number, QRectF(text_area.left(), text_area.top() + text_area.height() * 0.38,
                          text_area.width(), text_area.height() * 0.62), 8, True, Qt.AlignLeft | Qt.AlignVCenter, QColor("#111827"))  # noqa: E501
            else:
                draw_text(scale_number, text_area, 7, True, Qt.AlignLeft | Qt.AlignVCenter, QColor("#111827"))

        def nice_floor(value):
            if value <= 0:
                return 1.0
            exponent = math.floor(math.log10(value))
            for exp in range(exponent + 1, exponent - 4, -1):
                for base in (5.0, 2.0, 1.0):
                    candidate = base * (10 ** exp)
                    if candidate <= value:
                        return max(candidate, 1.0)
            return 1.0

        pixmap = QPixmap(int(canvas_w), int(canvas_h))
        pixmap.fill(QColor("#070F1A"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        page_rect = QRectF(ox, oy, draw_w, draw_h)
        painter.fillRect(page_rect, QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#111827"), 1.2))
        painter.drawRect(page_rect)

        neatline = rect(
            geometry["margin"] - 2.0,
            geometry["margin"] - 2.0,
            page_w - (2.0 * geometry["margin"]) + 4.0,
            page_h - (2.0 * geometry["margin"]) + 4.0,
        )
        painter.setPen(QPen(QColor("#111827"), 0.8))
        painter.drawRect(neatline)

        map_rect = rect(geometry["map_x"], geometry["map_y"], geometry["map_w"], geometry["map_h"])
        painter.fillRect(map_rect, QColor("#FBFDF8"))
        painter.setPen(QPen(QColor("#111827"), 1.0))
        painter.drawRect(map_rect)

        map_image = self._render_map_preview_image(map_rect.width(), map_rect.height())
        if map_image:
            painter.drawImage(map_rect.toRect(), map_image)
            painter.setPen(QPen(QColor("#111827"), 1.0))
            painter.drawRect(map_rect)
        else:
            painter.save()
            painter.setClipRect(map_rect)
            painter.setPen(QPen(QColor(148, 163, 184, 130), 0.6))
            step = max(14.0, min(30.0, map_rect.width() / 8.0))
            x = map_rect.left() + step
            while x < map_rect.right():
                painter.drawLine(QPointF(x, map_rect.top()), QPointF(x, map_rect.bottom()))
                x += step
            y = map_rect.top() + step
            while y < map_rect.bottom():
                painter.drawLine(QPointF(map_rect.left(), y), QPointF(map_rect.right(), y))
                y += step

            painter.setPen(QPen(QColor("#2F6F4E"), 1.0))
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(map_rect.left() + map_rect.width() * 0.10, map_rect.top() + map_rect.height() * 0.72),
                        QPointF(map_rect.left() + map_rect.width() * 0.24, map_rect.top() + map_rect.height() * 0.55),
                        QPointF(map_rect.left() + map_rect.width() * 0.43, map_rect.top() + map_rect.height() * 0.60),
                        QPointF(map_rect.left() + map_rect.width() * 0.61, map_rect.top() + map_rect.height() * 0.42),
                        QPointF(map_rect.left() + map_rect.width() * 0.86, map_rect.top() + map_rect.height() * 0.49),
                    ]
                )
            )
            painter.setPen(QPen(QColor("#8A6F3D"), 0.9))
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(map_rect.left() + map_rect.width() * 0.15, map_rect.top() + map_rect.height() * 0.28),
                        QPointF(map_rect.left() + map_rect.width() * 0.32, map_rect.top() + map_rect.height() * 0.35),
                        QPointF(map_rect.left() + map_rect.width() * 0.50, map_rect.top() + map_rect.height() * 0.27),
                        QPointF(map_rect.left() + map_rect.width() * 0.78, map_rect.top() + map_rect.height() * 0.33),
                    ]
                )
            )
            painter.restore()

        north_size = min(map_rect.width(), map_rect.height()) * 0.10
        north_box = QRectF(map_rect.left() + 7, map_rect.top() + 7, north_size, north_size)
        painter.fillRect(north_box, QColor(255, 255, 255, 242))
        painter.setPen(QPen(QColor("#111827"), 0.9))
        painter.drawRect(north_box)
        cx = north_box.center().x()
        painter.setBrush(QColor("#111827"))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(cx, north_box.top() + 4),
                    QPointF(north_box.left() + 5, north_box.bottom() - 5),
                    QPointF(cx, north_box.bottom() - 8),
                    QPointF(north_box.right() - 5, north_box.bottom() - 5),
                ]
            )
        )
        draw_text("N", QRectF(north_box.left(), north_box.bottom() -
                  13, north_box.width(), 10), 7, True, Qt.AlignCenter)

        scale_w_mm = min(70.0, geometry["map_w"] * 0.30)
        scale_est = self._estimate_scale(geometry["map_w"])
        ground_m = nice_floor((scale_est or 25000.0) * (scale_w_mm / 1000.0))
        unit = "m"
        shown_value = ground_m
        if ground_m >= 1000:
            unit = "km"
            shown_value = ground_m / 1000.0
        scale_box = QRectF(
            map_rect.left() + 7,
            map_rect.bottom() - 32,
            max(scale_w_mm * scale, 76),
            24,
        )
        painter.fillRect(scale_box, QColor(255, 255, 255, 242))
        painter.setPen(QPen(QColor("#111827"), 0.9))
        painter.drawRect(scale_box)
        segment_w = (scale_box.width() - 10.0) / 4.0
        draw_text(f"{self._tr('Scala', 'Scale')} 1:{self._format_scale_value(scale_est or 0)}", QRectF(
            scale_box.left() + 4, scale_box.top() + 2, scale_box.width() - 8, 7), 6, True, Qt.AlignCenter)
        bar_y = scale_box.top() + 11
        for idx in range(4):
            segment = QRectF(scale_box.left() + 5 + (idx * segment_w), bar_y, segment_w, 5)
            painter.fillRect(segment, QColor("#111827") if idx % 2 == 0 else QColor("#FFFFFF"))
            painter.drawRect(segment)
        label = f"0 - {shown_value:g} {unit}"
        draw_text(label, QRectF(scale_box.left() + 4, scale_box.top() +
                  16, scale_box.width() - 8, 7), 6, False, Qt.AlignCenter)

        panel_rect = rect(
            geometry["panel_x"],
            geometry["panel_y"],
            geometry["panel_w"],
            geometry["panel_h"],
        )
        painter.fillRect(panel_rect, QColor("#FFFFFF"))
        painter.setPen(QPen(QColor("#111827"), 1.0))
        painter.drawRect(panel_rect)

        title_preview = self.title_input.text().strip().upper() or "TAVOLA CARTOGRAFICA"
        layer_name = self.layer.name() if self.layer else "Layer attivo"
        meta = self._tr(
            f"{layer_name}\nCRS progetto\nFormato {fmt}",
            f"{layer_name}\nProject CRS\nFormat {fmt}",
        )

        logo_path = self.logo_input.text().strip()
        has_logo = bool(logo_path and os.path.exists(logo_path))
        if geometry["horizontal"]:
            logo_w = panel_rect.width() * 0.12 if has_logo else 0.0
            info_w = panel_rect.width() * (0.36 if has_logo else 0.43)
            logo_rect = QRectF(panel_rect.left(), panel_rect.top(), logo_w, panel_rect.height())
            info_rect = QRectF(
                panel_rect.left() + logo_w,
                panel_rect.top(),
                info_w,
                panel_rect.height(),
            )
            legend_rect = QRectF(info_rect.right(), panel_rect.top(), panel_rect.right() -
                                 info_rect.right(), panel_rect.height())
            if has_logo:
                painter.drawLine(
                    QPointF(logo_rect.right(), panel_rect.top()),
                    QPointF(logo_rect.right(), panel_rect.bottom()),
                )
                draw_logo(logo_rect.adjusted(3, 3, -3, -3))
            painter.drawLine(
                QPointF(info_rect.right(), panel_rect.top()),
                QPointF(info_rect.right(), panel_rect.bottom()),
            )
            title_rect = QRectF(info_rect.left() + 5, info_rect.top() + 4,
                                info_rect.width() - 10, info_rect.height() * 0.25)
            scale_rect = QRectF(info_rect.left() + 5, title_rect.bottom() + 3,
                                info_rect.width() - 10, max(info_rect.height() * 0.18, 15))
            meta_rect = QRectF(info_rect.left() + 6, scale_rect.bottom() + 3, info_rect.width() -
                               12, max(info_rect.bottom() - scale_rect.bottom() - 7, 8))
            draw_text(title_preview, title_rect, self._title_font_for_preview(), True, Qt.AlignCenter)
            draw_scale_badge(scale_rect, scale_est)
            draw_text(meta, meta_rect, 7, False, Qt.AlignLeft | Qt.AlignTop, QColor("#1F2937"))
        else:
            cursor_top = panel_rect.top()
            if has_logo:
                logo_rect = QRectF(panel_rect.left(), panel_rect.top(), panel_rect.width(), panel_rect.height() * 0.13)
                painter.drawLine(
                    QPointF(panel_rect.left(), logo_rect.bottom()),
                    QPointF(panel_rect.right(), logo_rect.bottom()),
                )
                draw_logo(logo_rect.adjusted(4, 3, -4, -3))
                cursor_top = logo_rect.bottom()
            info_h = panel_rect.height() * (0.35 if has_logo else 0.32)
            info_rect = QRectF(panel_rect.left(), cursor_top, panel_rect.width(), info_h)
            legend_rect = QRectF(panel_rect.left(), info_rect.bottom(), panel_rect.width(),
                                 panel_rect.bottom() - info_rect.bottom())
            painter.drawLine(
                QPointF(panel_rect.left(), info_rect.bottom()),
                QPointF(panel_rect.right(), info_rect.bottom()),
            )
            title_rect = QRectF(info_rect.left() + 6, info_rect.top() + 4,
                                info_rect.width() - 12, info_rect.height() * 0.25)
            scale_rect = QRectF(info_rect.left() + 7, title_rect.bottom() + 3,
                                info_rect.width() - 14, max(info_rect.height() * 0.20, 16))
            meta_rect = QRectF(info_rect.left() + 7, scale_rect.bottom() + 3, info_rect.width() -
                               14, max(info_rect.bottom() - scale_rect.bottom() - 7, 8))
            draw_text(title_preview, title_rect, self._title_font_for_preview(), True, Qt.AlignCenter)
            draw_scale_badge(scale_rect, scale_est)
            draw_text(meta, meta_rect, 7, False, Qt.AlignLeft | Qt.AlignTop, QColor("#1F2937"))

        draw_text(self._tr("Legenda", "Legend"), legend_rect.adjusted(6, 5, -6, -legend_rect.height() * 0.72), 8, True)
        legend_rows = [
            ("#2F6F4E", self._tr("Limiti / aree", "Boundaries / areas")),
            ("#8A6F3D", self._tr("Curve / tracciati", "Contours / lines")),
            ("#2563EB", self._tr("Elementi puntuali", "Point features")),
        ]
        row_y = legend_rect.top() + 24
        for color, label_text in legend_rows:
            swatch = QRectF(legend_rect.left() + 7, row_y, 10, 6)
            painter.fillRect(swatch, QColor(color))
            painter.setPen(QPen(QColor("#111827"), 0.4))
            painter.drawRect(swatch)
            draw_text(label_text, QRectF(swatch.right() + 5, row_y - 3, legend_rect.width() - 24, 12), 7, False)
            row_y += 14

        painter.end()
        self.lbl_preview.setPixmap(pixmap)

    def _on_layout_changed(self, *args):
        if self._updating_layout:
            return
        self._update_recommendation_label()
        self.update_preview()

    def get_settings(self):
        chart_fields = []
        for item in self.list_chart_fields.selectedItems():
            field_name = item.data(Qt.UserRole)
            if field_name:
                chart_fields.append(field_name)
        chart_value = self.combo_chart_value.currentData()
        dashboard_enabled = self.chk_dashboard.isChecked() and bool(chart_fields)
        return {
            "title": self.title_input.text().strip(),
            "logo": self.logo_input.text().strip(),
            "format": self.combo_format.currentText(),
            "orientation": self.combo_orientation.currentData() or "Landscape",
            "cartiglio_pos": self.combo_cartiglio_pos.currentData() or "right",
            "export_attributes": self.radio_map_attr.isChecked(),
            "topo_profile": self.chk_topo_profile.isChecked(),
            "topo_profile_title_mode": self.combo_topo_title_mode.currentData() or "field",
            "topo_profile_title_field": self.combo_topo_title_field.currentData() or "",
            "topo_profile_titles": self._topo_titles(),
            "topo_profile_title_map": self._topo_title_map(),
            "topo_profile_single_title": self.topo_single_title_input.text().strip(),
            "topo_profile_source": self.combo_topo_source.currentData() or "online",
            "topo_profile_raster_id": self.combo_topo_raster.currentData() or "",
            "output_dir": self.dir_input.text().strip(),
            "dashboard_enabled": dashboard_enabled,
            "dashboard_category_field": chart_fields[0] if chart_fields else "",
            "dashboard_category_fields": chart_fields,
            "dashboard_value_field": chart_value if chart_value else "",
            "dashboard_title": self.chart_title_input.text().strip(),
            "dashboard_subtitle": self.chart_subtitle_input.text().strip(),
            "dashboard_aggregation": self.combo_chart_aggregation.currentData() or "sum",
            "dashboard_show_labels": self.chk_chart_labels.isChecked(),
            "dashboard_show_percentages": self.chk_chart_percent_labels.isChecked(),
            "dashboard_top_n": self.spin_chart_top_n.value(),
            "dashboard_sort_order": self.combo_chart_sort.currentData() or "value_desc",
            "dashboard_include_pie": self.chk_chart_pie.isChecked(),
            "dashboard_include_bar": self.chk_chart_bar.isChecked(),
            "dashboard_include_percent": self.chk_chart_percent.isChecked(),
            "dashboard_placement": self.combo_chart_placement.currentData() or "titleblock",
            "language": self.language,
        }
