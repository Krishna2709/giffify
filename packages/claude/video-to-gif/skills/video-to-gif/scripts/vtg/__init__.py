"""video-to-gif deterministic conversion engine (VTG-TS-001).

Standard-library-only Python engine that parses timestamps/manifests, validates
input, plans outputs, and drives FFmpeg's two-pass palette pipeline to produce
optimized GIFs. See ``versioned_technical_spec.md`` for the normative contract.
"""

from __future__ import annotations

__version__ = "0.2.0"
__spec_id__ = "VTG-TS-001"

__all__ = ["__spec_id__", "__version__"]
