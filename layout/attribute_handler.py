from ..dialogs.overflow_dialog import show_attribute_overflow_dialog


def check_table_fits(row_count, layout_item_height_mm=50.0):
    """
    Stima se la tabella attributi può entrare in uno spazio prefissato
    del layout.
    """
    cell_height_mm = 5.0
    header_height_mm = 7.0
    estimated_height_mm = header_height_mm + (row_count * cell_height_mm)
    available_height_mm = max(layout_item_height_mm, 1.0)
    fits = estimated_height_mm <= available_height_mm
    return fits, available_height_mm, estimated_height_mm


def handle_attribute_table(row_count, language="it"):
    """
    Gestisce la scelta di aggiungere la tabella attributi come pagina
    del PDF unico.
    Returns:
        (fits: bool, generate_attr_page: bool)
    """
    fits, available, estimated = check_table_fits(
        row_count=row_count, layout_item_height_mm=50.0
    )

    if row_count <= 0:
        return True, True

    if fits:
        return True, True

    template_name = "Layout Corrente" if language != "en" else "Current Layout"
    result = show_attribute_overflow_dialog(
        row_count, available, estimated, template_name, language
    )
    return fits, result.get("generate_attr_pdf", False)
