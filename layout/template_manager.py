import os


def get_template_path(aspect_ratio):
    """
    Seleziona il template ottimale in base all'aspect ratio.
    """
    plugin_dir = os.path.dirname(os.path.dirname(__file__))
    templates_dir = os.path.join(plugin_dir, "templates")

    if aspect_ratio > 1.0:
        return os.path.join(templates_dir, "A4_landscape.qpt")
    else:
        return os.path.join(templates_dir, "A4_portrait.qpt")


def get_attributes_template_path():
    plugin_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(plugin_dir, "templates", "attributes_only.qpt")
