"""Topology Shape Keys: a dependency-free Blender add-on."""

bl_info = {
    "name": "Topology Shape Keys",
    "author": "Topology Shape Keys contributors",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Shape Keys",
    "description": "Infer vertex correspondence and morph between separate meshes",
    "category": "Object",
}


def register():
    from . import addon
    addon.register()


def unregister():
    from . import addon
    addon.unregister()
