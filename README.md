# Topology Shape Keys

A self-contained Blender add-on that creates a shape key between **separate mesh objects**, even when their vertices, edges, and faces have been reordered. Blender 3.6+; no server, compiler, pip packages, or network connection required.

## Install

Build the ZIPs with `python tools/build.py`, or use the ZIPs already in `dist/`:

- **Blender 4.2 and later:** Preferences → Add-ons → menu in the upper right → **Install from Disk…**, choose `dist/topology_shape_keys-1.0.0.zip`, and enable **Topology Shape Keys**.
- **Blender 3.6–4.1:** Preferences → Add-ons → **Install…**, choose `dist/topology_shape_keys-1.0.0-legacy.zip`, and enable **Topology Shape Keys**.

The extension ZIP contains a manifest at its root. The legacy ZIP contains the `shapekey_inference/` package. Install the appropriate ZIP without extracting it.

## Make a transition

1. Switch to **Object Mode**, select both mesh objects, and make the **starting object active** (select it last).
2. Open the 3D View sidebar with **N**, then choose the **Shape Keys** tab.
3. Click **Use Selected Objects**. You can also choose the two objects directly in the panel.
4. Leave **Coordinates → Object Local** enabled when the objects are placed side by side for comparison. Choose **World** to transform target positions into the starting object's coordinate system.
5. Click **Create Shape Key from Target**.
6. Drag **Transition** between **0** (starting Basis) and **1** (target). Hover over the slider and press **I** at different frames to animate it. Blender's Object Data Properties → Shape Keys provides the same slider.

**Preview Result** defaults on: the new key is set to 1, and existing shape key values are set to 0. Turn it off to preserve the existing mix and create the new key at 0. Creation supports Blender Undo. Neither object is deleted, and shared source mesh data is copied before changes.

## Vertex correspondence and symmetry

Matching uses mesh connectivity, rather than assuming matching vertex indices or assigning nearest vertices independently:

1. Validate vertex/edge counts, polygon sizes, and anchor constraints.
2. Jointly refine vertex classes using valence, incident polygon sizes, neighbor classes, and anchors.
3. Search for a bijection while checking edges and polygon cycles, allowing reversed face winding.
4. Rank topology-preserving correspondences by total squared coordinate distance after centering and uniform scale normalization.
5. Validate the entire bijection, every edge, and every polygon before adding any shape keys.

**Topology cannot uniquely identify corresponding features on symmetric meshes.** A cube, a regular grid, repeated disconnected components, or two identical hands can admit several valid mappings. Geometry gives a useful default when the meshes share approximately the same orientation. It does not infer semantic labels or automatically align rotations. Severe deformation or changed orientation may require anchors. A geometrically optimal match can still be the wrong semantic match.

There are two ways to specify **hard anchors**:

- **Anchor Pairs:** enter zero-based `starting_vertex:target_vertex` pairs, such as `0:42, 17:8, 93:12`. To see indices, enable Developer Extras in Preferences → Interface, then enable Indices in Edit Mode's viewport text overlays.
- **SKI_ groups:** on each mesh, create identically named vertex groups such as `SKI_nose`, `SKI_left_eye`, and `SKI_right_eye`. Assign exactly **one vertex with positive weight** to each group. Group names must match between objects. Leave **Use SKI_ Anchor Groups** enabled. These groups are not modified.

A few distinctive anchors usually resolve flips and repeated components. Conflicting anchors fail without adding a key. Symmetric topology produces a geometry-ranking notice; inspect the transition or add anchors.

## Coordinates, modifiers, and existing keys

- The starting shape is its **Basis/reference key**, including when another key is currently active. Existing keys are retained. Absolute shape keys are rejected; the source must use relative shape keys.
- **Evaluate Target** defaults on and captures the target's current shape key mix and modifiers. Disable it to use the target's raw mesh/Basis instead.
- Topology means the same **edge and polygon connectivity**, not just equal vertex counts. A target subdivision, mirror, triangulation, geometry-nodes operation, or other topology change can prevent matching. Apply equivalent topology changes to both objects first, or disable target evaluation as appropriate.
- Source modifiers remain active and operate **after** the new shape key. The key's endpoint matches the captured target coordinates **before source modifiers**. Disable source deformation modifiers when comparing raw endpoints; arbitrary source modifiers cannot be inverted automatically.
- **Object Local** ignores object-level transforms; **World** transforms the captured target positions using `source.matrix_world.inverted() @ target.matrix_world`. Moving objects apart for display therefore usually calls for Object Local.
- Anchors refer to the sampled target mesh. Topology-preserving target modifiers that reorder vertices can change explicit index anchors; named vertex groups are preferable if the modifier preserves group membership.
- Animated or driven values can override the preview on a subsequent scene evaluation. Turn other keys off when you want a pure Basis-to-target transition.
- Linear interpolation is Blender's ordinary shape key interpolation. The endpoint matches; intermediate shapes can intersect or collapse during large deformations. This add-on does not perform collision avoidance or remeshing.

## Search limits

The default limits are **10 seconds** and **128 valid solutions**. The search is synchronous and can occupy Blender until it finishes. Limits bound the correspondence search; mesh extraction and final validation add some overhead.

If a limit is reached **after** a valid match is found, the best verified mapping is used and a warning appears in the panel. This may not be the best geometric match. If no valid mapping has been found, creation fails without changing source mesh data. Add anchors or increase the budget for difficult cases. No partial mapping is ever written.

Python keeps installation simple. Color refinement, frontier-based search, and iterative backtracking handle ordinary meshes without a native service. Highly regular or disconnected meshes can have a combinatorial number of matches; anchors are more useful there than a faster server.

On the development machine, `python tools/benchmark.py` matched randomly reordered quad grids with a mild deformation in **0.055 s / 1,024 vertices**, **0.794 s / 10,000 vertices**, and **3.284 s / 40,000 vertices**, without anchors. All three searches proved the minimum geometric cost. These timings describe that workload; other connectivity and deformation can require substantially more search.

## Scripting

For a legacy install or this checkout:

```python
import bpy
from shapekey_inference.addon import create_shape_key

key, result = create_shape_key(
    bpy.context,
    bpy.data.objects['Starting'],
    bpy.data.objects['Target'],
    name='Morph',
    coordinate_space='LOCAL',
    evaluate_target=True,
    anchors_text='0:42, 17:8',
    preview=False,
)
print(result.mapping)  # source index -> target index
key.value = 0.5
```

Extension installs use Blender's `bl_ext.<repository>.topology_shape_keys` module namespace instead. The independent `matching.py` module exposes `Mesh`, `find_correspondence`, and `validate_mapping` without importing Blender.

## Development and verification

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
& 'C:\Program Files\Blender Foundation\Blender 3.6\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_integration.py
& 'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe' --background --factory-startup --python-exit-code 1 --python tests/blender_integration.py
python tools/build.py
```

The independent tests cover shuffled and deformed meshes, anchors, winding, polygon cycles, disconnected and non-manifold geometry, invalid inputs, search limits, meshes above Python's recursion depth, and exhaustive minimum-cost comparisons on small graphs. Blender tests cover endpoint/intermediate coordinates, transforms, evaluated target keys/modifiers, existing keys, shared mesh data, operators, and failure paths.

API references: [Blender Object API](https://docs.blender.org/api/current/bpy.types.Object.html), [Mesh API](https://docs.blender.org/api/current/bpy.types.Mesh.html), and [ShapeKey API](https://docs.blender.org/api/current/bpy.types.ShapeKey.html).

Licensed under GPL-3.0-or-later; see `LICENSE`.
