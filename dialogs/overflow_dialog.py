from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .. import plugin_hub
from ..qt_compat import ensure_qdialog_compat

ensure_qdialog_compat(QDialog)


def _text(language, italian, english):
    return english if language == "en" else italian


class OverflowDialog(QDialog):
    def __init__(self, row_count, available_mm, estimated_mm, template_name, language="it"):
        super().__init__()
        self.setWindowTitle(_text(language, "Q-Press - Avviso Tabella Attributi", "Q-Press - Attribute Table Warning"))

        self.setStyleSheet(plugin_hub.FAMILY_STYLE + """
            QPushButton#btnCancel {
                background: #1b2430; color: #c3ccd6; border-color: #2c3a48;
            }
            QPushButton#btnCancel:hover { background: #22303e; }
        """)

        layout = QVBoxLayout()

        lbl = QLabel(
            _text(
                language,
                f"La tabella attributi contiene {row_count} righe e non puo\n"
                f"essere contenuta nel cartiglio selezionato ({template_name}).\n\n"
                f"Spazio disponibile: {available_mm:.1f} mm\n"
                f"Spazio necessario: {estimated_mm:.1f} mm\n",
                f"The attribute table contains {row_count} rows and cannot\n"
                f"fit inside the selected title block ({template_name}).\n\n"
                f"Available space: {available_mm:.1f} mm\n"
                f"Required space: {estimated_mm:.1f} mm\n",
            )
        )
        layout.addWidget(lbl)

        self.chk_map = QCheckBox(_text(language, "Mantieni la tavola cartografica", "Keep the map sheet"))
        self.chk_map.setChecked(True)
        layout.addWidget(self.chk_map)

        self.chk_attr = QCheckBox(
            _text(
                language,
                "Aggiungi la tabella attributi come pagina successiva",
                "Add the attribute table as a following page",
            )
        )  # noqa: E501
        self.chk_attr.setChecked(True)
        layout.addWidget(self.chk_attr)

        btn_layout = QHBoxLayout()
        btn_ok = QPushButton(_text(language, "Conferma", "Confirm"))
        btn_cancel = QPushButton(_text(language, "Annulla", "Cancel"))
        btn_cancel.setObjectName("btnCancel")
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)


def show_attribute_overflow_dialog(row_count, available_mm, estimated_mm, template_name, language="it"):
    """
    Mostra il dialog per l'overflow e restituisce le opzioni scelte.
    """
    dialog = OverflowDialog(row_count, available_mm, estimated_mm, template_name, language)
    result = dialog.exec()

    if result == QDialog.Accepted:
        return {
            "generate_map": dialog.chk_map.isChecked(),
            "generate_attr_pdf": dialog.chk_attr.isChecked(),
        }

    return {
        "generate_map": False,
        "generate_attr_pdf": False,
    }
