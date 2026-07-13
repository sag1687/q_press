import os

from qgis.core import QgsLayoutExporter


def export_to_pdf(layout, output_path):
    """
    Esporta il layout fornito in formato PDF.
    """
    exporter = QgsLayoutExporter(layout)
    settings = QgsLayoutExporter.PdfExportSettings()
    settings.appendGeoreference = True
    if hasattr(settings, "dpi"):
        settings.dpi = 300
    if hasattr(settings, "forceVectorOutput"):
        settings.forceVectorOutput = True
    if hasattr(settings, "simplifyGeometries"):
        settings.simplifyGeometries = True
    if hasattr(settings, "exportMetadata"):
        settings.exportMetadata = True

    res = exporter.exportToPdf(output_path, settings)
    if res != QgsLayoutExporter.ExportResult.Success:
        raise Exception(f"Errore durante l'esportazione PDF: {res}")
    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise Exception(
            "Errore durante l'esportazione PDF: file non creato o vuoto."
        )
    return output_path
