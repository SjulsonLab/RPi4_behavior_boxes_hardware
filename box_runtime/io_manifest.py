"""Profile-aware GPIO manifest loading from the tracked v4 CSV.

Data contracts:
- ``profile_name``: profile selector string, one of ``"head_fixed"`` or
  ``"freely_moving"``
- CSV rows: repository-tracked ``unified_GPIO_pin_arrangement_v4.csv`` records
  with GPIO numbers as scalar integers
- return value: ``BoxProfileManifest`` containing dictionaries keyed by
  canonical semantic name, with each pin spec storing GPIO number, semantic
  name, board alias, and alternate aliases
"""

from __future__ import annotations

from csv import reader
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re


PROFILE_COLUMN_NAMES = {
    "head_fixed": "Head-fixed",
    "freely_moving": "Freely-moving",
}


@dataclass(frozen=True)
class GpioPinSpec:
    """One resolved GPIO role from the profile manifest.

    Data contracts:
    - ``pin``: BCM GPIO number as ``int``
    - ``canonical_name``: canonical semantic identifier as snake_case ``str``
    - ``board_alias``: board/silkscreen connector name as ``str`` or ``None``
    - ``direction``: one of ``"input"``, ``"output"``, ``"user_configurable"``,
      or ``"reserved"``
    - ``aliases``: alternate label tuple used by manual-control surfaces
    """

    pin: int
    canonical_name: str
    board_alias: str | None
    direction: str
    device_type: str
    notes: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class BoxProfileManifest:
    """Resolved GPIO manifest for one named box profile.

    Data contracts:
    - ``inputs``/``outputs``/``user_configurable``: dictionaries keyed by
      canonical semantic name with ``GpioPinSpec`` values
    - ``reserved``: dictionary keyed by BCM GPIO number with ``GpioPinSpec``
      values
    """

    profile_name: str
    source_csv: Path
    inputs: dict[str, GpioPinSpec]
    outputs: dict[str, GpioPinSpec]
    user_configurable: dict[str, GpioPinSpec]
    reserved: dict[int, GpioPinSpec]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize_name(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = text.replace("-", "_").replace("/", "_")
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    normalized = text.lower()
    normalized = re.sub(r"^cue_led([0-9]+)$", r"cue_led_\1", normalized)
    return normalized


def _build_aliases(canonical_name: str, board_alias: str | None, csv_value: str) -> tuple[str, ...]:
    aliases: list[str] = []

    def _append(alias_value: str | None) -> None:
        if not alias_value:
            return
        alias_text = str(alias_value).strip()
        if not alias_text:
            return
        if alias_text not in aliases:
            aliases.append(alias_text)

    _append(board_alias)
    normalized_csv_value = _normalize_name(csv_value)
    if normalized_csv_value and normalized_csv_value != canonical_name:
        _append(normalized_csv_value)

    lick_aliases = {
        "lick_left": "lick_1",
        "lick_right": "lick_2",
        "lick_center": "lick_3",
    }
    treadmill_aliases = {
        "treadmill_1": "treadmill_encoder_a",
        "treadmill_2": "treadmill_encoder_b",
    }
    _append(lick_aliases.get(canonical_name))
    _append(treadmill_aliases.get(canonical_name))
    return tuple(aliases)


def _canonical_user_name() -> str:
    return "user_configurable"


def _classify_direction(csv_type: str, canonical_name: str) -> str:
    type_name = _normalize_name(csv_type)
    if type_name == "user_configurable":
        return "user_configurable"
    if canonical_name.endswith("_out") or type_name in {"pump", "cue_led"}:
        return "output"
    if canonical_name.endswith("_in") or type_name in {"ir_sensor", "lick", "trigger"}:
        return "input"
    raise ValueError(f"Cannot classify GPIO direction for type={csv_type!r}, name={canonical_name!r}")


def _build_spec(
    gpio_pin: int,
    csv_type: str,
    board_alias: str,
    profile_value: str,
    notes: str,
) -> GpioPinSpec | None:
    raw_value = str(profile_value).strip()
    if not raw_value or raw_value.lower() in {"(unused)", "unused"}:
        return None

    if _normalize_name(csv_type) == "user_configurable":
        canonical_name = _canonical_user_name()
    else:
        canonical_name = _normalize_name(raw_value)
    direction = _classify_direction(csv_type, canonical_name)
    aliases = _build_aliases(canonical_name, board_alias or None, raw_value)
    return GpioPinSpec(
        pin=int(gpio_pin),
        canonical_name=canonical_name,
        board_alias=board_alias or None,
        direction=direction,
        device_type=_normalize_name(csv_type),
        notes=str(notes).strip(),
        aliases=aliases,
    )


@lru_cache(maxsize=8)
def load_box_profile(profile_name: str) -> BoxProfileManifest:
    """Load one profile manifest from the tracked v4 CSV.

    Args:
    - ``profile_name``: profile selector string.

    Returns:
    - ``manifest``: resolved ``BoxProfileManifest`` for the requested profile.
    """

    normalized_profile = str(profile_name).strip().lower()
    if normalized_profile not in PROFILE_COLUMN_NAMES:
        raise KeyError(f"Unknown box profile {profile_name!r}")

    csv_path = _repo_root() / "unified_GPIO_pin_arrangement_v4.csv"
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(reader(handle))
    if not rows:
        raise RuntimeError(f"GPIO manifest CSV is empty: {csv_path}")

    header = [str(value).strip() for value in rows[0]]
    index_lookup = {name: header.index(name) for name in header if name}
    required_columns = ["GPIO", "Type", "PCB Name", PROFILE_COLUMN_NAMES[normalized_profile], "notes"]
    missing = [name for name in required_columns if name not in index_lookup]
    if missing:
        raise RuntimeError(f"GPIO manifest is missing required columns: {missing}")

    inputs: dict[str, GpioPinSpec] = {}
    outputs: dict[str, GpioPinSpec] = {}
    user_configurable: dict[str, GpioPinSpec] = {}
    reserved: dict[int, GpioPinSpec] = {}

    for row in rows[1:]:
        if len(row) <= index_lookup["GPIO"]:
            continue
        gpio_text = str(row[index_lookup["GPIO"]]).strip()
        if not gpio_text:
            continue
        gpio_pin = int(gpio_text)
        csv_type = str(row[index_lookup["Type"]]).strip()
        board_alias = str(row[index_lookup["PCB Name"]]).strip()
        profile_value = str(row[index_lookup[PROFILE_COLUMN_NAMES[normalized_profile]]]).strip()
        notes = str(row[index_lookup["notes"]]).strip()

        spec = _build_spec(gpio_pin, csv_type, board_alias, profile_value, notes)
        if spec is None:
            reserved[gpio_pin] = GpioPinSpec(
                pin=gpio_pin,
                canonical_name=f"reserved_gpio_{gpio_pin}",
                board_alias=board_alias or None,
                direction="reserved",
                device_type=_normalize_name(csv_type),
                notes=notes,
                aliases=tuple(alias for alias in [board_alias] if alias),
            )
            continue

        if spec.direction == "input":
            inputs[spec.canonical_name] = spec
        elif spec.direction == "output":
            outputs[spec.canonical_name] = spec
        elif spec.direction == "user_configurable":
            user_configurable[spec.canonical_name] = spec
        else:
            reserved[gpio_pin] = spec

    return BoxProfileManifest(
        profile_name=normalized_profile,
        source_csv=csv_path,
        inputs=inputs,
        outputs=outputs,
        user_configurable=user_configurable,
        reserved=reserved,
    )
