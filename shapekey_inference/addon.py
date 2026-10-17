"""Blender UI and transactional shape-key creation."""

import re

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

from .matching import MatchError, Mesh, find_correspondence


ANCHOR_PREFIX = "SKI_"


def _mesh_poll(self, obj):
    return obj.type == 'MESH'


def _is_editable(block):
    # ID.is_editable was introduced after Blender 3.6.
    return getattr(block, "is_editable", block.library is None or block.override_library is not None)


class TSK_Settings(bpy.types.PropertyGroup):
    source: PointerProperty(name="Starting Object", type=bpy.types.Object, poll=_mesh_poll)
    target: PointerProperty(name="Target Object", type=bpy.types.Object, poll=_mesh_poll)
    key_name: StringProperty(name="Key Name", default="Target")
    coordinate_space: EnumProperty(
        name="Coordinates",
        items=[('LOCAL', "Object Local", "Use target local coordinates; ignore object placement"),
               ('WORLD', "World", "Match target world positions before source modifiers")],
        default='LOCAL')
    evaluate_target: BoolProperty(
        name="Evaluate Target", default=True,
        description="Include the target's current shape-key mix and modifiers; topology must still match")
    preview: BoolProperty(name="Preview Result", default=True,
                         description="Set the new key to 1; turn other keys off so the endpoint is visible")
    anchors: StringProperty(
        name="Anchor Pairs", default="",
        description="Zero-based source:target vertex indices, for example 0:42, 17:8; hard constraints")
    use_groups: BoolProperty(
        name="Use SKI_ Anchor Groups", default=True,
        description="Match identically named SKI_ vertex groups; each must contain exactly one vertex")
    timeout: FloatProperty(name="Time Limit (seconds)", default=10, min=0.1, max=600)
    max_solutions: IntProperty(name="Solution Limit", default=128, min=1, max=100000)
    status: StringProperty(default="")


def _snapshot(mesh, coordinates=None):
    return Mesh.from_data(
        coordinates if coordinates is not None else (v.co[:] for v in mesh.vertices),
        (tuple(e.vertices) for e in mesh.edges),
        (tuple(f.vertices) for f in mesh.polygons))


def _parse_anchors(text):
    anchors = {}
    if not text.strip():
        return anchors
    for pair in re.split(r'[,;\n]+', text):
        match = re.fullmatch(r'\s*(\d+)\s*:\s*(\d+)\s*', pair)
        if not match:
            raise MatchError("Anchor format: source:target, source:target (zero-based indices)")
        u, v = map(int, match.groups())
        if u in anchors and anchors[u] != v:
            raise MatchError("A source vertex has conflicting anchors")
        anchors[u] = v
    return anchors


def _group_anchors(obj, mesh):
    groups = {g.index: g.name for g in obj.vertex_groups if g.name.startswith(ANCHOR_PREFIX)}
    members = {name: [] for name in groups.values()}
    for v in mesh.vertices:
        for membership in v.groups:
            if membership.group in groups and membership.weight > 0:
                members[groups[membership.group]].append(v.index)
    for name, vertices in members.items():
        if len(vertices) != 1:
            raise MatchError(f"{obj.name}: anchor group {name!r} must contain exactly one vertex")
    return {name: vertices[0] for name, vertices in members.items()}


def _merge_group_anchors(anchors, source, target):
    if source.keys() != target.keys():
        raise MatchError("Both objects must have the same SKI_ anchor group names")
    for name, u in source.items():
        v = target[name]
        if u in anchors and anchors[u] != v:
            raise MatchError(f"Anchor group {name!r} conflicts with an index pair")
        anchors[u] = v


def create_shape_key(context, source, target, *, name="Target", coordinate_space='LOCAL',
                     evaluate_target=True, anchors_text="", use_groups=True,
                     timeout=10.0, max_solutions=128, preview=True):
    """Create a key only after a complete, validated correspondence is found.

    Source uses its reference/Basis shape. Target optionally uses its evaluated
    state. Shared source data is copied on success; failures roll back changes.
    """
    if source is None or target is None or source.type != 'MESH' or target.type != 'MESH':
        raise MatchError("Choose two mesh objects")
    if source == target:
        raise MatchError("Starting and target objects must be different")
    if source.mode != 'OBJECT' or target.mode != 'OBJECT':
        raise MatchError("Switch both objects to Object Mode before creating the key")
    if not _is_editable(source):
        raise MatchError("The starting object must be editable; make it local first")
    if coordinate_space not in {'LOCAL', 'WORLD'}:
        raise MatchError("Coordinate space must be LOCAL or WORLD")
    keys = source.data.shape_keys
    if keys and not keys.use_relative:
        raise MatchError("The starting object uses absolute shape keys; relative shape keys are required")

    basis = keys.reference_key.data if keys else source.data.vertices
    source_mesh = _snapshot(source.data, (p.co[:] for p in basis))
    anchors = _parse_anchors(anchors_text)
    source_groups = _group_anchors(source, source.data) if use_groups else {}

    evaluated = None
    try:
        if evaluate_target:
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = target.evaluated_get(depsgraph)
            target_data = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            target_matrix = evaluated.matrix_world.copy()
        else:
            target_data = target.data
            target_matrix = target.matrix_world.copy()
        if use_groups:
            _merge_group_anchors(anchors, source_groups, _group_anchors(target, target_data))
        if coordinate_space == 'WORLD':
            try:
                transform = source.matrix_world.inverted() @ target_matrix
            except ValueError as exc:
                raise MatchError("Starting object has zero scale; world conversion is undefined") from exc
            coordinates = [(transform @ v.co)[:] for v in target_data.vertices]
        else:
            coordinates = [v.co[:] for v in target_data.vertices]
        target_mesh = _snapshot(target_data, coordinates)
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()

    result = find_correspondence(source_mesh, target_mesh, anchors,
                                 timeout=timeout, max_solutions=max_solutions)
    positions = [axis for v in result.mapping for axis in target_mesh.coordinates[v]]

    # All matching and validation precedes the first Blender data mutation.
    original_data = source.data
    original_index = source.active_shape_key_index
    original_values = [(key, key.value, key.mute) for key in keys.key_blocks] if keys else []
    copied_data = None
    created = []
    try:
        if original_data.users > 1 or not _is_editable(original_data):
            copied_data = original_data.copy()
            source.data = copied_data
        if source.data.shape_keys is None:
            created.append(source.shape_key_add(name="Basis", from_mix=False))
        key = source.shape_key_add(name=name.strip() or target.name, from_mix=False)
        created.append(key)
        key.data.foreach_set("co", positions)
        key.relative_key = source.data.shape_keys.reference_key
        key.value = 0.0
        if preview:
            for other in source.data.shape_keys.key_blocks:
                if other != key:
                    other.value = 0.0
            key.value = 1.0
        source.active_shape_key_index = len(source.data.shape_keys.key_blocks) - 1
        source.data.update()
    except Exception:
        if copied_data is not None:
            source.data = original_data
            bpy.data.meshes.remove(copied_data)
        else:
            for created_key in reversed(created):
                source.shape_key_remove(created_key)
            for old_key, value, muted in original_values:
                old_key.value, old_key.mute = value, muted
        source.active_shape_key_index = original_index
        raise
    return key, result


def _selected_pair(context):
    source = context.active_object
    others = [obj for obj in context.selected_objects if obj.type == 'MESH' and obj != source]
    if source is None or source.type != 'MESH' or len(others) != 1:
        raise MatchError("Select exactly two meshes; make the starting object active")
    if len(context.selected_objects) != 2:
        raise MatchError("Select exactly two mesh objects")
    return source, others[0]


class OBJECT_OT_tsk_use_selection(bpy.types.Operator):
    bl_idname = "object.tsk_use_selection"
    bl_label = "Use Selected Objects"
    bl_description = "Active mesh is the starting object; the other selected mesh is the target"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        try:
            source, target = _selected_pair(context)
        except MatchError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings = context.scene.topology_shape_keys
        settings.source, settings.target = source, target
        settings.key_name = target.name
        settings.status = ""
        return {'FINISHED'}


class OBJECT_OT_tsk_create(bpy.types.Operator):
    bl_idname = "object.tsk_create"
    bl_label = "Create Shape Key from Target"
    bl_description = "Infer vertex correspondence from topology and create the target shape key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        settings = context.scene.topology_shape_keys
        source, target = settings.source, settings.target
        context.window_manager.progress_begin(0, 100)
        try:
            if source is None or target is None:
                source, target = _selected_pair(context)
            key, result = create_shape_key(
                context, source, target, name=settings.key_name,
                coordinate_space=settings.coordinate_space,
                evaluate_target=settings.evaluate_target,
                anchors_text=settings.anchors, use_groups=settings.use_groups,
                timeout=settings.timeout, max_solutions=settings.max_solutions,
                preview=settings.preview)
        except MatchError as exc:
            settings.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        except Exception as exc:
            settings.status = f"Creation failed: {exc}"
            self.report({'ERROR'}, settings.status)
            return {'CANCELLED'}
        finally:
            context.window_manager.progress_end()
        settings.status = f"Created {key.name}: {len(result.mapping):,} vertices in {result.elapsed:.2f}s"
        self.report({'INFO'}, settings.status)
        if not result.complete:
            settings.status += ". Search limited; add anchors to refine."
            self.report({'WARNING'}, "Search limit reached. Best verified match used; add anchors to refine it")
        elif result.used_geometry:
            settings.status += ". Geometry ranked matches; inspect symmetry."
            self.report({'WARNING'}, "Geometry ranked possible matches. Check symmetry; add anchors if needed")
        return {'FINISHED'}


class VIEW3D_PT_topology_shape_keys(bpy.types.Panel):
    bl_label = "Topology Shape Keys"
    bl_idname = "VIEW3D_PT_topology_shape_keys"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Shape Keys"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.topology_shape_keys
        layout.operator("object.tsk_use_selection", icon='RESTRICT_SELECT_OFF')
        layout.prop(settings, "source")
        layout.prop(settings, "target")
        layout.prop(settings, "key_name")
        layout.prop(settings, "coordinate_space")
        layout.prop(settings, "evaluate_target")
        layout.prop(settings, "preview")
        box = layout.box()
        box.label(text="Correspondence Anchors", icon='PINNED')
        box.prop(settings, "anchors")
        box.prop(settings, "use_groups")
        box.label(text="Same SKI_ name, one vertex per group")
        row = layout.row(align=True)
        row.prop(settings, "timeout")
        row.prop(settings, "max_solutions")
        layout.operator("object.tsk_create", icon='SHAPEKEY_DATA')
        if settings.source and settings.source.data.shape_keys:
            key = settings.source.active_shape_key
            if key and key != settings.source.data.shape_keys.reference_key:
                layout.prop(key, "value", text="Transition", slider=True)
        if settings.status:
            # Keep long messages readable in the narrow sidebar.
            import textwrap
            for line in textwrap.wrap(settings.status, width=36):
                layout.label(text=line)


CLASSES = (TSK_Settings, OBJECT_OT_tsk_use_selection, OBJECT_OT_tsk_create,
           VIEW3D_PT_topology_shape_keys)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.topology_shape_keys = PointerProperty(type=TSK_Settings)


def unregister():
    del bpy.types.Scene.topology_shape_keys
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
