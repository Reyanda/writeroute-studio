"""Minimal text-model layer shared with the private Writing-Master engine.

Only `textmodel` and `statistics` are carried here, because those are the only
modules `writeroute` imports. The detection engine — `scoring`, `skillengine`, the
feature packs, `allowlist` and `reported` — deliberately does **not** live in this
repository. An earlier release bundled a stale copy of it, and installing that copy
silently reverted false-positive work done in the private tree. Keeping the surface
this small makes that regression impossible rather than merely unlikely.
"""
