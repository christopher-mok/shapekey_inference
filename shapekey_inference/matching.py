"""Bounded mesh isomorphism search, independent of Blender and third-party modules.

Topology is a hard constraint; normalized coordinate distance only ranks valid
correspondences. Face cycles are checked, including on non-manifold meshes.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
import math
import time


class MatchError(ValueError):
    """No valid correspondence could be established."""


class MatchTimeout(MatchError):
    """The search budget expired before a valid mapping was found."""


@dataclass(frozen=True)
class Mesh:
    coordinates: tuple
    edges: tuple
    faces: tuple

    @classmethod
    def from_data(cls, coordinates, edges, faces):
        return cls(tuple(tuple(float(x) for x in p) for p in coordinates),
                   tuple(tuple(e) for e in edges), tuple(tuple(f) for f in faces))


@dataclass(frozen=True)
class MatchResult:
    mapping: tuple  # source index -> target index
    geometry_error: float  # RMS distance in centered, unit-RMS coordinates
    solutions: int
    complete: bool  # minimum geometric cost proved, not semantic certainty
    used_geometry: bool
    stop_reason: str
    elapsed: float


def _cycle(face):
    """Canonical cycle, independent of winding and starting corner."""
    # Mesh faces cannot repeat vertices, so the minimum has one position.
    i = face.index(min(face))
    forward = face[i:] + face[:i]
    backward = (forward[0],) + tuple(reversed(forward[1:]))
    return min(forward, backward)


class _Topology:
    def __init__(self, mesh):
        self.mesh = mesh
        n = len(mesh.coordinates)
        if not n:
            raise MatchError("Meshes must contain at least one vertex")
        if any(len(p) != 3 or not all(math.isfinite(x) for x in p)
               for p in mesh.coordinates):
            raise MatchError("Vertex coordinates must be finite 3D points")
        self.neighbors = [set() for _ in range(n)]
        self.incident = [[] for _ in range(n)]
        self.edges = set()
        for edge in mesh.edges:
            if (len(edge) != 2 or any(not isinstance(v, int) or not 0 <= v < n for v in edge)
                    or edge[0] == edge[1]):
                raise MatchError("Invalid mesh edge")
            a, b = edge
            key = tuple(sorted(edge))
            if key in self.edges:
                raise MatchError("Duplicate mesh edges are not supported")
            self.edges.add(key)
            self.neighbors[a].add(b)
            self.neighbors[b].add(a)
        self.face_counts = Counter()
        self.face_positions = []
        for fi, face in enumerate(mesh.faces):
            if (len(face) < 3 or any(not isinstance(v, int) or not 0 <= v < n for v in face)
                    or len(set(face)) != len(face)):
                raise MatchError("Invalid mesh face")
            for a, b in zip(face, face[1:] + face[:1]):
                if tuple(sorted((a, b))) not in self.edges:
                    raise MatchError("A face boundary is missing its mesh edge")
            self.face_counts[_cycle(face)] += 1
            self.face_positions.append({v: i for i, v in enumerate(face)})
            for v in face:
                self.incident[v].append(fi)


def _valid_mapping(a, b, mapping):
    n = len(a.mesh.coordinates)
    if len(mapping) != n or set(mapping) != set(range(n)):
        return False
    if {tuple(sorted((mapping[u], mapping[v]))) for u, v in a.edges} != b.edges:
        return False
    return Counter(_cycle(tuple(mapping[v] for v in f)) for f in a.mesh.faces) == b.face_counts


def validate_mapping(source, target, mapping):
    """Check bijection, every edge, and every polygon cycle (either winding)."""
    return _valid_mapping(_Topology(source), _Topology(target), mapping)


def _normalized(points):
    n = len(points)
    center = tuple(math.fsum(p[d] for p in points) / n for d in range(3))
    centered = [tuple(p[d] - center[d] for d in range(3)) for p in points]
    scale = math.sqrt(math.fsum(x * x for p in centered for x in p) / n)
    if scale == 0:
        scale = 1.0
    return [tuple(x / scale for x in p) for p in centered]


def _colors(a, b, anchors, check_time):
    """Joint Weisfeiler-Lehman refinement with matching anchor labels."""
    labels = [{}, {}]
    for marker, (u, v) in enumerate(sorted(anchors.items()), 1):
        labels[0][u] = marker
        labels[1][v] = marker
    signatures = []
    for side, topo in enumerate((a, b)):
        signatures.append([
            (labels[side].get(v, 0), len(topo.neighbors[v]),
             tuple(sorted(len(topo.mesh.faces[f]) for f in topo.incident[v])))
            for v in range(len(topo.neighbors))])

    def intern(rows):
        table = {}
        result = []
        for row in rows:
            result.append([table.setdefault(sig, len(table)) for sig in row])
        if Counter(result[0]) != Counter(result[1]):
            raise MatchError("Meshes have different topology, or anchors are inconsistent")
        return result, len(table)

    colors, count = intern(signatures)
    # Bounded refinement; stopping early changes speed, never correctness.
    for _ in range(32):
        check_time()
        signatures = [
            [(colors[side][v], tuple(sorted(colors[side][w] for w in neighbors)))
             for v, neighbors in enumerate(topo.neighbors)]
            for side, topo in enumerate((a, b))]
        colors, new_count = intern(signatures)
        if new_count == count:
            break
        count = new_count
    return colors


def find_correspondence(source, target, anchors=None, *, timeout=10.0,
                        max_solutions=128):
    """Find an exact topology match, then minimize normalized geometric error.

    A bounded search returns its best *validated* mapping on timeout/solution
    limit. If it has found none, it raises MatchTimeout. Anchors are hard
    source-index -> target-index constraints. No vertex order is assumed.
    """
    started = time.perf_counter()
    if not math.isfinite(timeout) or timeout <= 0 or max_solutions < 1:
        raise ValueError("Use a positive timeout and solution limit")
    deadline = started + timeout

    def check_time():
        if time.perf_counter() >= deadline:
            raise MatchTimeout("Search time limit reached; add anchor pairs or increase the limit")

    a, b = _Topology(source), _Topology(target)
    n = len(source.coordinates)
    if (n != len(target.coordinates) or len(a.edges) != len(b.edges)
            or Counter(map(len, source.faces)) != Counter(map(len, target.faces))):
        raise MatchError("Meshes must have the same vertex, edge, and polygon topology")
    anchors = dict(anchors or {})
    if (any(not isinstance(v, int) or not 0 <= v < n
            for pair in anchors.items() for v in pair)
            or len(set(anchors.values())) != len(anchors)):
        raise MatchError("Anchor indices must be in range and one-to-one")
    ca, cb = _normalized(source.coordinates), _normalized(target.coordinates)

    def cost(u, v):
        return sum((ca[u][d] - cb[v][d]) ** 2 for d in range(3))

    best, best_cost, solutions = None, math.inf, 0
    # Cheap, verified incumbent for meshes whose original indices survived.
    identity = tuple(range(n))
    if all(u == v for u, v in anchors.items()) and _valid_mapping(a, b, identity):
        best = identity
        best_cost = math.fsum(cost(u, u) for u in range(n))
        solutions = 1

    used_geometry = False
    complete, reason = False, "timeout"
    try:
        colors_a, colors_b = _colors(a, b, anchors, check_time)
        groups_a, groups_b = defaultdict(set), defaultdict(set)
        for v in range(n):
            groups_a[colors_a[v]].add(v)
            groups_b[colors_b[v]].add(v)
        used_geometry = any(len(group) > 1 for group in groups_a.values())
        mapping, inverse = [-1] * n, [-1] * n
        counts = [0] * n
        versions = [0] * n
        frontier = []
        seed_order = sorted(range(n), key=lambda u: (
            len(groups_a[colors_a[u]]), -len(a.neighbors[u]), u))

        def refresh(u):
            versions[u] += 1
            if mapping[u] == -1 and counts[u]:
                heapq.heappush(frontier, (-counts[u], len(groups_a[colors_a[u]]),
                                          -len(a.neighbors[u]), u, versions[u]))

        def assign(u, v):
            mapping[u], inverse[v] = v, u
            for w in a.neighbors[u]:
                counts[w] += 1
                refresh(w)

        def unassign(u, v):
            mapping[u], inverse[v] = -1, -1
            refresh(u)
            for w in a.neighbors[u]:
                counts[w] -= 1
                refresh(w)

        def compatible(u, v):
            if inverse[v] != -1 or colors_a[u] != colors_b[v]:
                return False
            if any(mapping[w] != -1 and mapping[w] not in b.neighbors[v]
                   for w in a.neighbors[u]):
                return False
            if any(inverse[w] != -1 and inverse[w] not in a.neighbors[u]
                   for w in b.neighbors[v]):
                return False
            # Every partially mapped source polygon must fit a target cycle.
            for fi in a.incident[u]:
                face = source.faces[fi]
                known = [(i, v if w == u else mapping[w])
                         for i, w in enumerate(face) if w == u or mapping[w] != -1]
                if len(known) == 1:
                    continue
                k = len(face)
                start = a.face_positions[fi][u]
                found = False
                for fj in b.incident[v]:
                    if len(target.faces[fj]) != k:
                        continue
                    pos = b.face_positions[fj]
                    origin = pos[v]
                    if any(all(pos.get(w, -1) == (origin + sign * (i - start)) % k
                               for i, w in known) for sign in (1, -1)):
                        found = True
                        break
                if not found:
                    return False
            return True

        # Unique refined colors (including anchors) need no search.
        fixed_count, partial_cost = 0, 0.0
        for color, group in groups_a.items():
            if len(group) == 1:
                u, v = next(iter(group)), next(iter(groups_b[color]))
                if not compatible(u, v):
                    raise MatchError("Meshes have different face connectivity or incompatible anchors")
                assign(u, v)
                fixed_count += 1
                partial_cost += cost(u, v)
            if fixed_count % 256 == 0:
                check_time()

        def choose():
            # Lazy heap updates avoid scanning the entire mesh at each vertex.
            if len(frontier) > max(1024, 8 * n):
                frontier.clear()
                for u in range(n):
                    refresh(u)
            while frontier:
                _, _, _, u, version = frontier[0]
                if mapping[u] == -1 and counts[u] and version == versions[u]:
                    return u
                heapq.heappop(frontier)
            return next((u for u in seed_order if mapping[u] == -1), None)

        def candidates(u):
            mapped_neighbors = [mapping[w] for w in a.neighbors[u] if mapping[w] != -1]
            if mapped_neighbors:
                smallest = min(mapped_neighbors, key=lambda v: len(b.neighbors[v]))
                pool = b.neighbors[smallest]
            else:
                pool = groups_b[colors_a[u]]
            values = [v for v in pool if compatible(u, v)]
            return iter(sorted(((cost(u, v), v) for v in values)))

        # Each frame owns a choice; iterative DFS supports >1000-vertex meshes.
        stack = []
        assigned = fixed_count
        while True:
            check_time()
            if assigned == n:
                if _valid_mapping(a, b, mapping):
                    candidate = tuple(mapping)
                    if candidate != best:
                        solutions += 1
                    if best is None or partial_cost < best_cost:
                        best, best_cost = candidate, partial_cost
                    if best_cost <= 1e-24:
                        complete, reason = True, "optimal"
                        break
                    if solutions >= max_solutions:
                        reason = "solution_limit"
                        break
            elif best is not None and partial_cost > best_cost + 1e-12:
                pass
            else:
                u = choose()
                stack.append([u, candidates(u), None])

            # Move to the next alternative, undoing only the owning assignment.
            while stack:
                check_time()
                frame = stack[-1]
                u, options, chosen = frame
                if chosen is not None:
                    v, delta = chosen
                    unassign(u, v)
                    partial_cost -= delta
                    assigned -= 1
                    frame[2] = None
                try:
                    delta, v = next(options)
                except StopIteration:
                    stack.pop()
                    continue
                if best is not None and partial_cost + delta > best_cost + 1e-12:
                    continue
                assign(u, v)
                partial_cost += delta
                assigned += 1
                frame[2] = (v, delta)
                break
            else:
                complete, reason = True, "exhausted"
                break
    except MatchTimeout:
        if best is None:
            raise

    if best is None:
        raise MatchError("No topology-preserving correspondence exists for these meshes and anchors")
    # Never expose a partial or unchecked mapping, including on budget expiry.
    if not _valid_mapping(a, b, best):
        raise MatchError("Internal correspondence validation failed")
    return MatchResult(best, math.sqrt(max(0.0, best_cost) / n), solutions,
                       complete, used_geometry, reason, time.perf_counter() - started)
