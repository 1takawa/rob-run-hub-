"""Game-independent calculations for the ROB RUN Info helper tab."""

from __future__ import annotations

from math import ceil


STRUCTURES: dict[str, dict[str, int | str]] = {
    "trailer": {"name": "Прицеп", "base": 5, "slots": 9},
    "small_house": {"name": "Маленький дом", "base": 15, "slots": 27},
    "car_garage": {"name": "Гараж для авто", "base": 4, "slots": 6},
    "large_car_garage": {"name": "Большой гараж", "base": 6, "slots": 12},
    "warehouse": {"name": "Склад", "base": 10, "slots": 15},
    "workshop": {"name": "Мастерская", "base": 5, "slots": 9},
    "grow_room": {"name": "Комната выращивания", "base": 5, "slots": 9},
    "chemical_lab": {"name": "Химическая лаборатория", "base": 3, "slots": 6},
    "mesh_fence": {"name": "Сетчатый забор", "base": 2, "slots": 3},
    "metal_fence": {"name": "Металлический забор", "base": 5, "slots": 6},
    "concrete_fence": {"name": "Бетонный забор", "base": 10, "slots": 9},
}

FUSE_POINTS = {"uncommon": 1, "epic": 3, "legendary": 5}
DECODER_POINTS = {"uncommon": 1, "epic": 3, "legendary": 5}
DECODER_LABELS = {"uncommon": "Обычный", "epic": "Эпический", "legendary": "Легендарный"}
INSTALL_SECONDS_PER_DECODER = 12


def raid_defense(structure_key: str, fuses: dict[str, int]) -> dict:
    """Calculate defense and decoder counts; reject impossible fuse layouts."""
    if structure_key not in STRUCTURES:
        raise ValueError("Выбери строение из списка.")
    structure = STRUCTURES[structure_key]
    counts: dict[str, int] = {}
    for rarity in FUSE_POINTS:
        try:
            amount = int(fuses.get(rarity, 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("Количество предохранителей должно быть целым числом.") from exc
        if amount < 0:
            raise ValueError("Количество предохранителей не может быть отрицательным.")
        counts[rarity] = amount

    fuse_count = sum(counts.values())
    slots = int(structure["slots"])
    if fuse_count > slots:
        raise ValueError(f"У этого строения {slots} ячеек. Сейчас указано: {fuse_count}.")

    defense = int(structure["base"]) + sum(counts[key] * FUSE_POINTS[key] for key in FUSE_POINTS)
    decoders = {rarity: ceil(defense / strength) for rarity, strength in DECODER_POINTS.items()}
    seconds = {rarity: quantity * INSTALL_SECONDS_PER_DECODER for rarity, quantity in decoders.items()}
    return {
        "structure": structure_key,
        "base": int(structure["base"]),
        "slots": slots,
        "fuse_count": fuse_count,
        "defense": defense,
        "decoders": decoders,
        "install_seconds": seconds,
    }
