"""Optional repeatable benchmark; not part of the installed add-on."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_matching import grid, permuted
from shapekey_inference.matching import find_correspondence


for size in (32, 100, 200):
    source = grid(size)
    target, expected = permuted(source, deform=lambda p: (p[0], p[1], .01 * p[0]))
    result = find_correspondence(source, target, timeout=20)
    assert result.mapping == expected
    print(f"{size * size:>6,} vertices: {result.elapsed:.3f}s, "
          f"complete={result.complete}, reason={result.stop_reason}", flush=True)
