"""Small local check for the JSON visual stimulus toolchain."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from essential.visual_runtime.grating_compiler import compile_grating
from essential.visual_runtime.grating_specs import load_grating_spec


def main() -> None:
    """Compile one temporary grating spec and print the resulting frame shape."""

    with tempfile.TemporaryDirectory() as tmp_dir:
        spec_path = Path(tmp_dir) / "demo_grating.json"
        spec_path.write_text(
            json.dumps(
                {
                    "name": "demo_grating",
                    "duration_s": 0.1,
                    "angle_deg": 90.0,
                    "spatial_freq_cpd": 0.08,
                    "temporal_freq_hz": 1.0,
                    "contrast": 0.9,
                    "background_gray_u8": 96,
                    "waveform": "sine",
                }
            ),
            encoding="utf-8",
        )
        spec = load_grating_spec(spec_path)
        compiled = compile_grating(
            spec=spec,
            resolution_px=(64, 48),
            refresh_hz=60.0,
            degrees_subtended=80.0,
        )
        print(f"compiled {compiled.spec.name}: {compiled.frames.shape}, dtype={compiled.frames.dtype}")


if __name__ == "__main__":
    main()
