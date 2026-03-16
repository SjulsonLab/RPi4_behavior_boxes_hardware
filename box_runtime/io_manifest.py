"""Fixed GPIO mappings for the supported BehavBox profiles.

Data contracts:
- ``profile_name``: profile selector string, one of ``"head_fixed"`` or
  ``"freely_moving"``
- GPIO definitions: fixed Python literals in this file, not external data
  files
- return value: ``BoxProfileManifest`` containing dictionaries keyed by
  canonical semantic name, with each pin spec storing GPIO number, semantic
  name, board alias, and alternate aliases
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class GpioPinSpec:
    """One resolved GPIO role from the fixed profile mapping.

    Data contracts:
    - ``pin``: Broadcom serial controller (BCM) GPIO number as ``int``
    - ``canonical_name``: canonical semantic identifier as snake_case ``str``
    - ``board_alias``: board or silkscreen connector name as ``str`` or ``None``
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
    """Resolved GPIO mapping for one named box profile.

    Data contracts:
    - ``inputs``/``outputs``/``user_configurable``: dictionaries keyed by
      canonical semantic name with ``GpioPinSpec`` values
    - ``reserved``: dictionary keyed by BCM GPIO number with ``GpioPinSpec``
      values
    """

    profile_name: str
    inputs: dict[str, GpioPinSpec]
    outputs: dict[str, GpioPinSpec]
    user_configurable: dict[str, GpioPinSpec]
    reserved: dict[int, GpioPinSpec]


def _dedupe_aliases(*values: str | None) -> tuple[str, ...]:
    aliases: list[str] = []
    for value in values:
        if value is None:
            continue
        alias = str(value).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _spec(
    *,
    pin: int,
    canonical_name: str,
    board_alias: str | None,
    direction: str,
    device_type: str,
    notes: str = "",
    aliases: tuple[str, ...] = (),
) -> GpioPinSpec:
    """Build one fixed GPIO pin specification.

    Data contracts:
    - ``pin``: BCM GPIO number as ``int``
    - ``canonical_name``: semantic pin name as snake_case ``str``
    - ``board_alias``: silkscreen or board label as ``str`` or ``None``
    - ``direction``: mapping role string
    - ``device_type``: coarse hardware category string
    - ``notes``: human-readable free text
    - ``aliases``: alternate labels as ``tuple[str, ...]``
    - returns: immutable ``GpioPinSpec``
    """

    return GpioPinSpec(
        pin=int(pin),
        canonical_name=str(canonical_name),
        board_alias=board_alias,
        direction=str(direction),
        device_type=str(device_type),
        notes=str(notes),
        aliases=_dedupe_aliases(board_alias, *aliases),
    )


def _shared_outputs() -> dict[str, GpioPinSpec]:
    return {
        "reward_left": _spec(
            pin=19,
            canonical_name="reward_left",
            board_alias="pump1",
            direction="output",
            device_type="pump",
        ),
        "reward_right": _spec(
            pin=20,
            canonical_name="reward_right",
            board_alias="pump2",
            direction="output",
            device_type="pump",
        ),
        "reward_center": _spec(
            pin=21,
            canonical_name="reward_center",
            board_alias="pump3",
            direction="output",
            device_type="pump",
        ),
        "reward_4": _spec(
            pin=7,
            canonical_name="reward_4",
            board_alias="pump4",
            direction="output",
            device_type="pump",
        ),
        "vacuum": _spec(
            pin=25,
            canonical_name="vacuum",
            board_alias="pump_en",
            direction="output",
            device_type="pump",
        ),
        "cue_led_1": _spec(
            pin=22,
            canonical_name="cue_led_1",
            board_alias="Cue1",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED1",),
        ),
        "cue_led_2": _spec(
            pin=18,
            canonical_name="cue_led_2",
            board_alias="Cue2",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED2",),
        ),
        "cue_led_3": _spec(
            pin=17,
            canonical_name="cue_led_3",
            board_alias="Cue3",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED3",),
        ),
        "cue_led_4": _spec(
            pin=14,
            canonical_name="cue_led_4",
            board_alias="Cue4",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED4",),
        ),
        "cue_led_5": _spec(
            pin=10,
            canonical_name="cue_led_5",
            board_alias="DIO4",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED5",),
        ),
        "cue_led_6": _spec(
            pin=11,
            canonical_name="cue_led_6",
            board_alias="DIO5",
            direction="output",
            device_type="cue_led",
            aliases=("cueLED6",),
        ),
        "trigger_out": _spec(
            pin=24,
            canonical_name="trigger_out",
            board_alias="DIO2",
            direction="output",
            device_type="trigger",
        ),
    }


def _shared_reserved() -> dict[int, GpioPinSpec]:
    return {
        9: _spec(
            pin=9,
            canonical_name="reserved_gpio_9",
            board_alias="DIO3",
            direction="reserved",
            device_type="irig_output",
            notes="not used by behavbox (reserved for IRIG output)",
        ),
    }


def _fixed_head_fixed_manifest() -> BoxProfileManifest:
    inputs = {
        "trigger_in": _spec(
            pin=23,
            canonical_name="trigger_in",
            board_alias="DIO1",
            direction="input",
            device_type="trigger",
        ),
        "ir_lick_left": _spec(
            pin=5,
            canonical_name="ir_lick_left",
            board_alias="IR_rx1",
            direction="input",
            device_type="ir_sensor",
        ),
        "ir_lick_right": _spec(
            pin=6,
            canonical_name="ir_lick_right",
            board_alias="IR_rx2",
            direction="input",
            device_type="ir_sensor",
        ),
        "ir_lick_center": _spec(
            pin=12,
            canonical_name="ir_lick_center",
            board_alias="IR_rx3",
            direction="input",
            device_type="ir_sensor",
        ),
        "treadmill_1": _spec(
            pin=13,
            canonical_name="treadmill_1",
            board_alias="IR_rx4",
            direction="input",
            device_type="ir_sensor",
            notes="connect treadmill to the negative pin only",
            aliases=("Treadmill 1", "treadmill_encoder_a"),
        ),
        "treadmill_2": _spec(
            pin=16,
            canonical_name="treadmill_2",
            board_alias="IR_rx5",
            direction="input",
            device_type="ir_sensor",
            notes="connect treadmill to the negative pin only",
            aliases=("Treadmill 2", "treadmill_encoder_b"),
        ),
        "lick_left": _spec(
            pin=26,
            canonical_name="lick_left",
            board_alias="Lick1",
            direction="input",
            device_type="lick",
            aliases=("lick_1",),
        ),
        "lick_right": _spec(
            pin=27,
            canonical_name="lick_right",
            board_alias="Lick2",
            direction="input",
            device_type="lick",
            aliases=("lick_2",),
        ),
        "lick_center": _spec(
            pin=15,
            canonical_name="lick_center",
            board_alias="Lick3",
            direction="input",
            device_type="lick",
            aliases=("lick_3",),
        ),
    }
    outputs = _shared_outputs()
    outputs["airpuff"] = _spec(
        pin=8,
        canonical_name="airpuff",
        board_alias="pump5",
        direction="output",
        device_type="pump",
    )
    return BoxProfileManifest(
        profile_name="head_fixed",
        inputs=inputs,
        outputs=outputs,
        user_configurable={
            "user_configurable": _spec(
                pin=4,
                canonical_name="user_configurable",
                board_alias="Camera",
                direction="user_configurable",
                device_type="user_configurable",
                aliases=("user",),
            )
        },
        reserved=_shared_reserved(),
    )


def _fixed_freely_moving_manifest() -> BoxProfileManifest:
    inputs = {
        "trigger_in": _spec(
            pin=23,
            canonical_name="trigger_in",
            board_alias="DIO1",
            direction="input",
            device_type="trigger",
        ),
        "poke_left": _spec(
            pin=5,
            canonical_name="poke_left",
            board_alias="IR_rx1",
            direction="input",
            device_type="ir_sensor",
        ),
        "poke_right": _spec(
            pin=6,
            canonical_name="poke_right",
            board_alias="IR_rx2",
            direction="input",
            device_type="ir_sensor",
        ),
        "poke_center": _spec(
            pin=12,
            canonical_name="poke_center",
            board_alias="IR_rx3",
            direction="input",
            device_type="ir_sensor",
        ),
        "poke_extra1": _spec(
            pin=13,
            canonical_name="poke_extra1",
            board_alias="IR_rx4",
            direction="input",
            device_type="ir_sensor",
            notes="connect treadmill to the negative pin only",
        ),
        "poke_extra2": _spec(
            pin=16,
            canonical_name="poke_extra2",
            board_alias="IR_rx5",
            direction="input",
            device_type="ir_sensor",
            notes="connect treadmill to the negative pin only",
        ),
    }
    outputs = _shared_outputs()
    outputs["reward_5"] = _spec(
        pin=8,
        canonical_name="reward_5",
        board_alias="pump5",
        direction="output",
        device_type="pump",
    )
    return BoxProfileManifest(
        profile_name="freely_moving",
        inputs=inputs,
        outputs=outputs,
        user_configurable={
            "user_configurable": _spec(
                pin=4,
                canonical_name="user_configurable",
                board_alias="Camera",
                direction="user_configurable",
                device_type="user_configurable",
                aliases=("user",),
            )
        },
        reserved=_shared_reserved(),
    )


FIXED_GPIO_PROFILES = {
    "head_fixed": _fixed_head_fixed_manifest(),
    "freely_moving": _fixed_freely_moving_manifest(),
}


@lru_cache(maxsize=8)
def load_box_profile(profile_name: str) -> BoxProfileManifest:
    """Load one fixed profile mapping from this module.

    Args:
    - ``profile_name``: profile selector string.

    Returns:
    - ``manifest``: resolved ``BoxProfileManifest`` for the requested profile.
    """

    normalized_profile = str(profile_name).strip().lower()
    if normalized_profile not in FIXED_GPIO_PROFILES:
        raise KeyError(f"Unknown box profile {profile_name!r}")
    return FIXED_GPIO_PROFILES[normalized_profile]
