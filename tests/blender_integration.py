"""Run with Blender --background --factory-startup --python this_file.py."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import bpy
from mathutils import Vector

import shapekey_inference
from shapekey_inference.addon import create_shape_key
from shapekey_inference.matching import MatchError
from test_matching import grid, permuted


def object_from_mesh(name, mesh):
    data = bpy.data.meshes.new(name)
    data.from_pydata(mesh.coordinates, mesh.edges, mesh.faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    return obj


class BlenderIntegration(unittest.TestCase):
    def setUp(self):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.mesh = grid(5)
        self.target_mesh, self.expected = permuted(
            self.mesh, deform=lambda p: (p[0], p[1], .1 * p[0] * p[1]))
        self.source = object_from_mesh("Starting", self.mesh)
        self.target = object_from_mesh("Target", self.target_mesh)
        bpy.context.view_layer.objects.active = self.source
        self.source.select_set(True)
        self.target.select_set(True)
        bpy.context.view_layer.update()

    def assert_points(self, key, expected):
        self.assertEqual(len(key.data), len(expected))
        for vertex, point in zip(key.data, expected):
            self.assertLess((vertex.co - Vector(point)).length, 1e-5)

    def test_shuffled_key_and_interpolated_endpoint(self):
        self.target.location = (100, 0, 0)
        key, result = create_shape_key(bpy.context, self.source, self.target, evaluate_target=False)
        self.assertEqual(result.mapping, self.expected)
        expected = [self.target_mesh.coordinates[v] for v in self.expected]
        self.assert_points(key, expected)
        self.assertEqual(key.value, 1)
        self.assert_points(self.source.data.shape_keys.reference_key, self.mesh.coordinates)
        for amount in (0, .5, 1):
            key.value = amount
            bpy.context.view_layer.update()
            evaluated = self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            try:
                for i, vertex in enumerate(mesh.vertices):
                    point = Vector(self.mesh.coordinates[i]).lerp(Vector(expected[i]), amount)
                    self.assertLess((vertex.co - point).length, 1e-5)
            finally:
                evaluated.to_mesh_clear()

    def test_world_transform(self):
        self.source.location = (-2, 3, 1)
        self.source.rotation_euler = (.1, .2, .3)
        self.source.scale = (1.2, .9, 1.5)
        self.target.location = (7, -1, 2)
        self.target.rotation_euler = (.3, -.1, .2)
        self.target.scale = (.9, 1.5, 1.2)
        bpy.context.view_layer.update()
        # Pin the intended correspondence to isolate coordinate conversion.
        anchors = ','.join(f'{u}:{v}' for u, v in enumerate(self.expected))
        key, _ = create_shape_key(bpy.context, self.source, self.target,
                                  coordinate_space='WORLD', anchors_text=anchors)
        for u, v in enumerate(self.expected):
            self.assertLess(((self.source.matrix_world @ key.data[u].co) -
                             (self.target.matrix_world @ self.target.data.vertices[v].co)).length, 1e-5)

    def test_target_evaluated_shape_key_and_modifier(self):
        self.target.shape_key_add(name="Basis")
        target_key = self.target.shape_key_add(name="Raised")
        for point in target_key.data:
            point.co.z += 2
        target_key.value = .5
        modifier = self.target.modifiers.new("Offset", 'DISPLACE')
        modifier.direction = 'Z'
        modifier.strength = .4
        modifier.mid_level = 0
        bpy.context.view_layer.update()
        evaluated = self.target.evaluated_get(bpy.context.evaluated_depsgraph_get())
        expected = [v.co.copy() for v in evaluated.data.vertices]
        key, result = create_shape_key(bpy.context, self.source, self.target)
        self.assert_points(key, [expected[v] for v in result.mapping])
        self.assertEqual(target_key.value, .5)

    def test_raw_target_ignores_shape_key_mix(self):
        self.target.shape_key_add(name="Basis")
        target_key = self.target.shape_key_add(name="Raised")
        for point in target_key.data:
            point.co.z += 2
        target_key.value = 1
        key, result = create_shape_key(bpy.context, self.source, self.target, evaluate_target=False)
        self.assert_points(key, [self.target_mesh.coordinates[v] for v in result.mapping])

    def test_existing_keys_and_preview_disabled(self):
        self.source.shape_key_add(name="Basis")
        existing = self.source.shape_key_add(name="Existing")
        existing.value = .6
        key, _ = create_shape_key(bpy.context, self.source, self.target, preview=False)
        self.assertAlmostEqual(existing.value, .6)
        self.assertEqual(key.value, 0)
        self.assertEqual(len(self.source.data.shape_keys.key_blocks), 3)

    def test_preview_clears_other_mix(self):
        self.source.shape_key_add(name="Basis")
        existing = self.source.shape_key_add(name="Existing")
        existing.value = .6
        key, _ = create_shape_key(bpy.context, self.source, self.target)
        self.assertEqual(existing.value, 0)
        self.assertEqual(key.value, 1)

    def test_shared_mesh_is_copied(self):
        other = bpy.data.objects.new("Shared", self.source.data)
        bpy.context.collection.objects.link(other)
        original = self.source.data
        create_shape_key(bpy.context, self.source, self.target)
        self.assertIsNotNone(self.source.data.shape_keys)
        self.assertNotEqual(self.source.data, original)
        self.assertEqual(other.data, original)
        self.assertIsNone(other.data.shape_keys)

    def test_shared_mesh_with_existing_keys_is_copied(self):
        self.source.shape_key_add(name="Basis")
        existing = self.source.shape_key_add(name="Existing")
        existing.value = .6
        other = bpy.data.objects.new("Shared", self.source.data)
        bpy.context.collection.objects.link(other)
        original = self.source.data
        create_shape_key(bpy.context, self.source, self.target)
        self.assertEqual(other.data, original)
        self.assertAlmostEqual(other.data.shape_keys.key_blocks['Existing'].value, .6)
        self.assertEqual(len(other.data.shape_keys.key_blocks), 2)
        self.assertEqual(len(self.source.data.shape_keys.key_blocks), 3)

    def test_anchor_groups(self):
        for u in (0, 4, 20):
            name = f'SKI_corner_{u}'
            self.source.vertex_groups.new(name=name).add([u], 1, 'REPLACE')
            self.target.vertex_groups.new(name=name).add([self.expected[u]], 1, 'REPLACE')
        _, result = create_shape_key(bpy.context, self.source, self.target)
        self.assertEqual(result.mapping, self.expected)

    def test_bad_anchors_leave_source_unchanged(self):
        original = self.source.data
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target, anchors_text="0:9999")
        self.assertEqual(self.source.data, original)
        self.assertIsNone(self.source.data.shape_keys)
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target, anchors_text="0:1:2")
        self.assertIsNone(self.source.data.shape_keys)

    def test_mismatched_groups_and_topology_leave_source_unchanged(self):
        self.source.vertex_groups.new(name='SKI_test').add([0], 1, 'REPLACE')
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target)
        self.assertIsNone(self.source.data.shape_keys)
        self.source.vertex_groups.clear()
        self.target.modifiers.new("Subdivision", 'SUBSURF')
        bpy.context.view_layer.update()
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target)
        self.assertIsNone(self.source.data.shape_keys)

    def test_absolute_keys_rejected(self):
        self.source.shape_key_add(name="Basis")
        self.source.data.shape_keys.use_relative = False
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target)
        self.assertEqual(len(self.source.data.shape_keys.key_blocks), 1)

    def test_zero_scale_world_rejected(self):
        self.source.scale.x = 0
        bpy.context.view_layer.update()
        with self.assertRaises(MatchError):
            create_shape_key(bpy.context, self.source, self.target, coordinate_space='WORLD')
        self.assertIsNone(self.source.data.shape_keys)

    def test_failed_write_rolls_back_new_basis_and_shared_copy(self):
        class FailingObject:
            def __init__(self, wrapped):
                object.__setattr__(self, '_wrapped', wrapped)
                object.__setattr__(self, '_calls', 0)

            def __getattr__(self, name):
                return getattr(self._wrapped, name)

            def __setattr__(self, name, value):
                setattr(self._wrapped, name, value)

            def shape_key_add(self, **kwargs):
                object.__setattr__(self, '_calls', self._calls + 1)
                if self._calls == 2:
                    raise RuntimeError('Simulated Blender write failure')
                return self._wrapped.shape_key_add(**kwargs)

        for shared in (False, True):
            with self.subTest(shared=shared):
                original = self.source.data
                if shared:
                    other = bpy.data.objects.new('Shared', original)
                    bpy.context.collection.objects.link(other)
                mesh_count = len(bpy.data.meshes)
                with self.assertRaisesRegex(RuntimeError, 'Simulated'):
                    create_shape_key(bpy.context, FailingObject(self.source), self.target)
                self.assertEqual(self.source.data, original)
                self.assertIsNone(self.source.data.shape_keys)
                self.assertEqual(len(bpy.data.meshes), mesh_count)

    def test_operators(self):
        self.assertEqual(bpy.ops.object.tsk_use_selection(), {'FINISHED'})
        settings = bpy.context.scene.topology_shape_keys
        self.assertEqual(settings.source, self.source)
        self.assertEqual(settings.target, self.target)
        self.assertEqual(bpy.ops.object.tsk_create(), {'FINISHED'})
        self.assertIn('Created', settings.status)
        self.assertEqual(self.source.active_shape_key.value, 1)


shapekey_inference.register()
suite = unittest.defaultTestLoader.loadTestsFromTestCase(BlenderIntegration)
result = unittest.TextTestRunner(verbosity=2).run(suite)
shapekey_inference.unregister()
if not result.wasSuccessful():
    sys.exit(1)
print(f"BLENDER_INTEGRATION_OK: {bpy.app.version_string}, {result.testsRun} tests")
