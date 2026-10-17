import math
import random
import unittest
from itertools import permutations

from shapekey_inference.matching import (MatchError, MatchTimeout, Mesh,
                                         find_correspondence, validate_mapping)


def mesh_from_faces(points, faces, loose_edges=()):
    edges = {tuple(sorted(edge)) for edge in loose_edges}
    for face in faces:
        edges.update(tuple(sorted((a, b))) for a, b in zip(face, face[1:] + face[:1]))
    return Mesh.from_data(points, sorted(edges), faces)


def grid(size):
    points = [(x, y, 0) for y in range(size) for x in range(size)]
    faces = [(y * size + x, y * size + x + 1,
              (y + 1) * size + x + 1, (y + 1) * size + x)
             for y in range(size - 1) for x in range(size - 1)]
    return mesh_from_faces(points, faces)


def permuted(mesh, seed=19, deform=lambda p: p):
    rng = random.Random(seed)
    mapping = list(range(len(mesh.coordinates)))
    rng.shuffle(mapping)
    points = [None] * len(mapping)
    for u, v in enumerate(mapping):
        points[v] = deform(mesh.coordinates[u])
    faces = [tuple(mapping[v] for v in reversed(f)) for f in mesh.faces]
    rng.shuffle(faces)
    edges = [tuple(mapping[v] for v in reversed(e)) for e in mesh.edges]
    rng.shuffle(edges)
    return Mesh.from_data(points, edges, faces), tuple(mapping)


class MatchingTests(unittest.TestCase):
    def test_shuffled_deformed_grid(self):
        source = grid(7)
        target, expected = permuted(source, deform=lambda p: (p[0], p[1], .2 * math.sin(p[0])))
        result = find_correspondence(source, target)
        self.assertEqual(result.mapping, expected)
        self.assertTrue(validate_mapping(source, target, result.mapping))
        self.assertTrue(result.complete)

    def test_geometry_overrides_valid_but_wrong_identity(self):
        source = grid(3)
        # A horizontal reflection is a topology automorphism, but wrong geometry.
        mapping = tuple(y * 3 + 2 - x for y in range(3) for x in range(3))
        points = [None] * 9
        for u, v in enumerate(mapping):
            points[v] = source.coordinates[u]
        target = Mesh.from_data(points, source.edges, source.faces)
        self.assertTrue(validate_mapping(source, target, range(9)))
        self.assertEqual(find_correspondence(source, target).mapping, mapping)

    def test_anchors_override_geometry_on_symmetric_mesh(self):
        source = grid(3)
        mapping = tuple(y * 3 + 2 - x for y in range(3) for x in range(3))
        result = find_correspondence(source, source, {0: 2, 2: 0, 6: 8})
        self.assertEqual(result.mapping, mapping)

    def test_translation_and_scale_independent_ranking(self):
        source = grid(5)
        target, mapping = permuted(source, deform=lambda p: tuple(4 * v + 15 for v in p))
        result = find_correspondence(source, target)
        self.assertEqual(result.mapping, mapping)
        self.assertLess(result.geometry_error, 1e-12)

    def test_disconnected_components_and_loose_vertices(self):
        source = mesh_from_faces(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (4, 0, 0), (4, 1, 0),
             (5, 0, 0), (9, 2, 0), (9, 3, 0), (15, 4, 0)],
            [(0, 1, 2), (3, 4, 5)], [(6, 7)])
        target, mapping = permuted(source)
        self.assertEqual(find_correspondence(source, target).mapping, mapping)

    def test_non_manifold_edge(self):
        source = mesh_from_faces([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)],
                                 [(0, 1, 2), (1, 0, 3), (0, 1, 4)])
        target, mapping = permuted(source)
        self.assertEqual(find_correspondence(source, target).mapping, mapping)

    def test_polygon_cycle_not_just_edges_or_vertex_set(self):
        points = [(i, i % 2, 0) for i in range(5)]
        complete_edges = [(a, b) for a in range(5) for b in range(a + 1, 5)]
        source = mesh_from_faces(points, [(0, 1, 2, 3), (0, 3, 4)], complete_edges)
        target = mesh_from_faces(points, [(0, 2, 1, 3), (0, 3, 4)], complete_edges)
        self.assertFalse(validate_mapping(source, target, range(5)))
        with self.assertRaises(MatchError):
            find_correspondence(source, target, dict(enumerate(range(5))))

    def test_regular_graphs_with_different_connectivity(self):
        points = [(math.cos(i), math.sin(i), 0) for i in range(6)]
        source = mesh_from_faces(points, [], [(i, (i + 1) % 6) for i in range(6)])
        target = mesh_from_faces(points, [], [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)])
        with self.assertRaises(MatchError):
            find_correspondence(source, target)

    def test_counts_and_bad_anchors(self):
        source = grid(3)
        for anchors in ({0: 999}, {0: 1, 1: 1}, {0: -1}, {0: 4}):
            with self.subTest(anchors=anchors), self.assertRaises(MatchError):
                find_correspondence(source, source, anchors)
        with self.assertRaises(MatchError):
            find_correspondence(source, grid(4))

    def test_timeout_never_returns_partial_mapping(self):
        source = grid(10)
        target, _ = permuted(source)
        with self.assertRaises(MatchTimeout):
            find_correspondence(source, target, timeout=1e-12)
        # Already verified identity can be returned even on a tiny budget.
        result = find_correspondence(source, source, timeout=1e-12)
        self.assertFalse(result.complete)
        self.assertTrue(validate_mapping(source, source, result.mapping))

    def test_solution_limit_returns_valid_incumbent(self):
        source = grid(3)
        target, _ = permuted(source, deform=lambda p: (p[0], p[1], .3 * p[0] ** 2))
        result = find_correspondence(source, target, max_solutions=1)
        self.assertTrue(validate_mapping(source, target, result.mapping))
        self.assertEqual(result.stop_reason, 'solution_limit')

    def test_no_recursive_limit_on_large_mesh(self):
        source = grid(40)
        target, expected = permuted(source)
        result = find_correspondence(source, target, timeout=15)
        self.assertEqual(result.mapping, expected)

    def test_single_vertex_and_coincident_vertices(self):
        for points in ([(0, 0, 0)], [(0, 0, 0)] * 5):
            source = mesh_from_faces(points, [])
            self.assertTrue(validate_mapping(source, source,
                                            find_correspondence(source, source).mapping))

    def test_invalid_inputs(self):
        bad = [Mesh.from_data([], [], []),
               Mesh.from_data([(math.nan, 0, 0)], [], []),
               Mesh.from_data([(0, 0, 0)], [(0, 0)], []),
               Mesh.from_data([(0, 0, 0)] * 3, [], [(0, 1, 2)])]
        for source in bad:
            with self.subTest(source=source), self.assertRaises(MatchError):
                find_correspondence(source, source)

    def test_minimum_cost_matches_exhaustive_small_graph_search(self):
        # Independent exhaustive oracle exercises branching and backtracking.
        rng = random.Random(101)
        for case in range(8):
            points = [tuple(rng.uniform(-1, 1) for _ in range(3)) for _ in range(6)]
            edges = [(u, v) for u in range(6) for v in range(u + 1, 6) if rng.random() < .35]
            source = mesh_from_faces(points, [], edges)
            target, expected = permuted(source, seed=case,
                                        deform=lambda p: tuple(rng.uniform(-1, 1) for _ in p))

            def normalize(points):
                mean = [sum(p[d] for p in points) / len(points) for d in range(3)]
                centered = [[p[d] - mean[d] for d in range(3)] for p in points]
                scale = math.sqrt(sum(x*x for p in centered for x in p) / len(points))
                return [[x / scale for x in p] for p in centered]

            a, b = normalize(source.coordinates), normalize(target.coordinates)

            def error(mapping):
                return sum((a[u][d] - b[v][d]) ** 2
                           for u, v in enumerate(mapping) for d in range(3))

            valid = [m for m in permutations(range(6)) if validate_mapping(source, target, m)]
            best = min(map(error, valid))
            result = find_correspondence(source, target, max_solutions=1000)
            with self.subTest(case=case):
                self.assertTrue(result.complete)
                self.assertAlmostEqual(error(result.mapping), best, places=10)


if __name__ == '__main__':
    unittest.main()
