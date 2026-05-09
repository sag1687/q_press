def classFactory(iface):
    """Factory function for QGIS."""
    from .q_press_plugin import QPressPlugin
    return QPressPlugin(iface)
