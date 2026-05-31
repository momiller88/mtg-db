import logging
from neo4j import Session

log = logging.getLogger(__name__)

_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Manual overrides
# Cards that pattern-matching can't reliably classify.
# Keys are canonical card names. Values override broad_role and/or sub_roles.
# ---------------------------------------------------------------------------

MANUAL_OVERRIDES: dict[str, dict] = {
    # Combo finishers — win by library depletion
    "Thassa's Oracle":          {"broad_role": "win_condition", "sub_roles": ["combo_finisher", "etb_value"]},
    "Laboratory Maniac":        {"broad_role": "win_condition", "sub_roles": ["combo_finisher"]},
    "Jace, Wielder of Mysteries":{"broad_role": "win_condition", "sub_roles": ["combo_finisher", "card_draw"]},
    "Thassa's Oracle":          {"broad_role": "win_condition", "sub_roles": ["combo_finisher"]},

    # Combo enablers that look like removal/utility
    "Demonic Consultation":     {"broad_role": "combo_piece",   "sub_roles": ["tutor", "self_mill"]},
    "Tainted Pact":             {"broad_role": "combo_piece",   "sub_roles": ["tutor", "self_mill"]},
    "Doomsday":                 {"broad_role": "combo_piece",   "sub_roles": ["tutor"]},
    "Hermit Druid":             {"broad_role": "combo_piece",   "sub_roles": ["self_mill", "mana_dork"]},
    "Underworld Breach":        {"broad_role": "combo_piece",   "sub_roles": ["recursion"]},
    "Ad Nauseam":               {"broad_role": "combo_piece",   "sub_roles": ["card_draw"]},
    "Necropotence":             {"broad_role": "card_draw",     "sub_roles": ["combo_piece", "engine"]},
    "Gaea's Cradle":            {"broad_role": "ramp",          "sub_roles": ["land_ramp", "combo_piece"]},
    "Serra's Sanctum":          {"broad_role": "ramp",          "sub_roles": ["land_ramp", "combo_piece"]},
    "Tolarian Academy":         {"broad_role": "ramp",          "sub_roles": ["land_ramp", "combo_piece"]},

    # Sacrifice outlets that look like generic creatures
    "Ashnod's Altar":           {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet", "ramp"]},
    "Phyrexian Altar":          {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet", "ramp"]},
    "Altar of Dementia":        {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet", "mill"]},
    "Goblin Bombardment":       {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet", "damage"]},
    "Viscera Seer":             {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet", "scry"]},
    "Carrion Feeder":           {"broad_role": "combo_piece",   "sub_roles": ["sacrifice_outlet"]},

    # Classic stax/hate pieces that read like normal cards
    "Null Rod":                 {"broad_role": "stax",          "sub_roles": ["artifact_hate"]},
    "Collector Ouphe":          {"broad_role": "stax",          "sub_roles": ["artifact_hate"]},
    "Drannith Magistrate":      {"broad_role": "stax",          "sub_roles": ["commander_hate"]},
    "Hullbreacher":             {"broad_role": "stax",          "sub_roles": ["draw_hate"]},
    "Notion Thief":             {"broad_role": "stax",          "sub_roles": ["draw_hate"]},

    # Cards with misleading oracle text
    "Smothering Tithe":         {"broad_role": "ramp",          "sub_roles": ["token_gen", "tax"]},
    "Rhystic Study":            {"broad_role": "card_draw",     "sub_roles": ["tax", "engine"]},
    "Mystic Remora":            {"broad_role": "card_draw",     "sub_roles": ["tax", "engine"]},
    "Sylvan Library":           {"broad_role": "card_draw",     "sub_roles": ["engine"]},
    "Sensei's Divining Top":    {"broad_role": "card_draw",     "sub_roles": ["combo_piece", "engine"]},
}


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------

def _has(mechanics: set, *tags: str) -> bool:
    return bool(mechanics.intersection(tags))


def _type(type_line: str, *types: str) -> bool:
    tl = type_line or ""
    return any(t.lower() in tl.lower() for t in types)


def _power_int(power: str | None) -> int:
    try:
        return int(power or 0)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Sub-role assignment
# These are additive — a card can have multiple sub-roles.
# ---------------------------------------------------------------------------

def _sub_roles(mechanics: set, type_line: str, power, combo_count: int) -> list[str]:
    subs: list[str] = []

    # Ramp sub-types
    if _has(mechanics, "mana_rock"):             subs.append("mana_rock")
    if _has(mechanics, "mana_dork"):             subs.append("mana_dork")
    if _has(mechanics, "ramp"):                  subs.append("land_ramp")
    if _has(mechanics, "cost_reduction"):        subs.append("cost_reduction")

    # Draw sub-types
    if _has(mechanics, "loot"):                  subs.append("loot")
    if _has(mechanics, "impulse"):               subs.append("impulse")
    if _has(mechanics, "scry"):                  subs.append("scry")
    if _has(mechanics, "surveil"):               subs.append("surveil")

    # Removal sub-types
    if _has(mechanics, "exile_removal"):         subs.append("exile")
    if _has(mechanics, "destroy"):               subs.append("destroy")
    if _has(mechanics, "bounce"):                subs.append("bounce")
    if _has(mechanics, "damage_spell"):          subs.append("damage")
    if _has(mechanics, "tuck"):                  subs.append("tuck")

    # Evasion
    if _has(mechanics, "flying"):                subs.append("flying")
    if _has(mechanics, "trample"):               subs.append("trample")
    if _has(mechanics, "haste"):                 subs.append("haste")
    if _has(mechanics, "unblockable"):           subs.append("unblockable")
    if _has(mechanics, "deathtouch"):            subs.append("deathtouch")
    if _has(mechanics, "lifelink"):              subs.append("lifelink")
    if _has(mechanics, "first_strike",
             "double_strike"):                   subs.append("first_strike")

    # Value triggers
    if _has(mechanics, "etb_trigger"):           subs.append("etb_value")
    if _has(mechanics, "death_trigger"):         subs.append("death_value")
    if _has(mechanics, "attack_trigger"):        subs.append("attack_value")

    # Combo adjacency by count
    if combo_count >= 5:                         subs.append("combo_synergy")

    # Tribal
    if _has(mechanics, "lord"):                  subs.append("tribal_lord")

    # Protection quality
    if _has(mechanics, "hexproof"):              subs.append("hexproof")
    if _has(mechanics, "indestructible"):        subs.append("indestructible")
    if _has(mechanics, "ward"):                  subs.append("ward")

    # Extra turns
    if _has(mechanics, "extra_turns"):           subs.append("extra_turns")

    # Graveyard
    if _has(mechanics, "flashback", "escape",
             "jump_start"):                      subs.append("graveyard_value")

    return sorted(set(subs))


# ---------------------------------------------------------------------------
# Broad role classification  (priority-ordered rules)
# ---------------------------------------------------------------------------

def classify_card(
    name: str,
    mechanics: set,
    type_line: str,
    power,
    toughness,
    oracle_text: str,
    combo_count: int,
) -> dict:
    """Return {"broad_role": str, "sub_roles": list[str]} for one card.

    Broad roles are assigned in priority order — first match wins.
    Sub-roles are always additive.
    """
    # 0. Manual override
    if name in MANUAL_OVERRIDES:
        override = MANUAL_OVERRIDES[name]
        subs = override.get("sub_roles", [])
        # merge any auto sub-roles not already present
        auto_subs = _sub_roles(mechanics, type_line, power, combo_count)
        merged = sorted(set(subs) | set(auto_subs))
        return {"broad_role": override["broad_role"], "sub_roles": merged}

    # 1. Lands
    if _type(type_line, "Land"):
        subs = []
        if _has(mechanics, "ramp", "mana_rock"):  subs.append("land_ramp")
        if combo_count >= 5:                       subs.append("combo_synergy")
        return {"broad_role": "land", "sub_roles": sorted(subs)}

    # 2. Dedicated combo piece (very high combo presence)
    if combo_count >= 20:
        subs = _sub_roles(mechanics, type_line, power, combo_count)
        if _has(mechanics, "sacrifice_outlet"):    subs = sorted(set(subs) | {"sacrifice_outlet"})
        if _has(mechanics, "untap_effect"):        subs = sorted(set(subs) | {"untap_effect"})
        return {"broad_role": "combo_piece", "sub_roles": subs}

    # 3. Board wipe (before single-target removal)
    if _has(mechanics, "wrath"):
        subs = _sub_roles(mechanics, type_line, power, combo_count)
        if _has(mechanics, "exile_removal"):       subs = sorted(set(subs) | {"exile"})
        return {"broad_role": "board_wipe", "sub_roles": subs}

    # 4. Counterspell
    if _has(mechanics, "counterspell") and _type(type_line, "Instant", "Sorcery"):
        return {"broad_role": "counterspell",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 5. Pure tutor (searches for non-land cards; land searches → ramp)
    if _has(mechanics, "tutor") and not _has(mechanics, "ramp"):
        return {"broad_role": "tutor",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 6. Ramp (mana rocks, dorks, land search)
    if _has(mechanics, "mana_rock", "mana_dork", "ramp", "cost_reduction", "free_cast"):
        return {"broad_role": "ramp",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 7. Card draw / advantage
    if _has(mechanics, "draw", "loot", "impulse") and not _has(mechanics, "wrath"):
        return {"broad_role": "card_draw",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 8. Win condition (extra turns, storm, specific damage)
    if _has(mechanics, "extra_turns", "storm"):
        subs = _sub_roles(mechanics, type_line, power, combo_count)
        if _has(mechanics, "extra_turns"):  subs = sorted(set(subs) | {"extra_turns"})
        return {"broad_role": "win_condition", "sub_roles": subs}

    # 9. Spot removal
    if _has(mechanics, "exile_removal", "destroy", "bounce", "damage_spell", "tuck"):
        if not _has(mechanics, "wrath"):
            return {"broad_role": "removal",
                    "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 10. Recursion
    if _has(mechanics, "recursion"):
        return {"broad_role": "recursion",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 11. Token engine
    if _has(mechanics, "token_gen"):
        return {"broad_role": "token_engine",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 12. Stax / Tax
    if _has(mechanics, "stax", "tax", "discard"):
        return {"broad_role": "stax",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 13. Protection pieces
    if _has(mechanics, "counterspell", "hexproof", "indestructible"):
        return {"broad_role": "protection",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 14. Tribal lord
    if _has(mechanics, "lord"):
        return {"broad_role": "tribal_support",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 15. Combat beater — creature with power ≥ 4 and evasion
    if (_type(type_line, "Creature") and _power_int(power) >= 4
            and _has(mechanics, "flying", "trample", "haste",
                     "unblockable", "double_strike")):
        return {"broad_role": "beater",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 16. Value engine — strong trigger-based card with no clearer role
    if _has(mechanics, "etb_trigger", "death_trigger", "attack_trigger", "cast_trigger"):
        return {"broad_role": "value_engine",
                "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}

    # 17. Utility / catch-all
    return {"broad_role": "utility",
            "sub_roles": _sub_roles(mechanics, type_line, power, combo_count)}


# ---------------------------------------------------------------------------
# Neo4j helpers
# ---------------------------------------------------------------------------

def get_combo_counts(session: Session) -> dict[str, int]:
    """Return {scryfall_id: combo_count} for all cards in at least one combo."""
    result = session.run(
        """
        MATCH (c:Card)-[:PART_OF_COMBO]->(:Combo)
        RETURN c.scryfall_id AS sid, count(*) AS n
        """
    )
    return {row["sid"]: row["n"] for row in result}


# ---------------------------------------------------------------------------
# Bulk classification and persistence
# ---------------------------------------------------------------------------

def classify_all_cards(card_lookup: dict, combo_counts: dict[str, int]) -> dict[str, dict]:
    """Return {card_name: {broad_role, sub_roles}} for every card in the lookup."""
    results: dict[str, dict] = {}
    for name, data in card_lookup.items():
        mechanics = set(data.get("mechanics") or [])
        sid = data.get("scryfall_id", "")
        results[name] = classify_card(
            name=name,
            mechanics=mechanics,
            type_line=data.get("type_line") or "",
            power=data.get("power"),
            toughness=data.get("toughness"),
            oracle_text=data.get("oracle_text") or "",
            combo_count=combo_counts.get(sid, 0),
        )
    log.info("Classified roles for %d cards", len(results))
    return results


def update_card_roles_batch(session: Session, card_roles: dict[str, dict],
                            card_lookup: dict) -> int:
    """Write broad_role and sub_roles to each Card node in AuraDB."""
    rows = []
    for name, role_data in card_roles.items():
        sid = card_lookup.get(name, {}).get("scryfall_id")
        if sid:
            rows.append({
                "scryfall_id": sid,
                "broad_role":  role_data["broad_role"],
                "sub_roles":   role_data["sub_roles"],
            })

    total = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        chunk = rows[i : i + _BATCH_SIZE]
        session.run(
            """
            UNWIND $rows AS row
            MATCH (c:Card {scryfall_id: row.scryfall_id})
            SET c.broad_role = row.broad_role,
                c.sub_roles  = row.sub_roles
            """,
            rows=chunk,
        )
        total += len(chunk)

    log.info("Updated roles on %d Card nodes", total)
    return total


def run_role_classification(session: Session, card_lookup: dict) -> dict:
    """Classify roles for all cards and persist to Neo4j.

    Returns summary dict with counts per broad_role.
    """
    # card_lookup values don't include mechanics from Neo4j — fetch them
    log.info("Fetching mechanics from Neo4j...")
    mech_result = session.run(
        "MATCH (c:Card) WHERE c.mechanics IS NOT NULL "
        "RETURN c.scryfall_id AS sid, c.mechanics AS mechanics"
    )
    neo4j_mechanics: dict[str, list] = {
        row["sid"]: row["mechanics"] for row in mech_result
    }

    # Merge Neo4j mechanics into card_lookup (non-destructive copy)
    enriched = {
        name: {**data, "mechanics": neo4j_mechanics.get(data.get("scryfall_id", ""), [])}
        for name, data in card_lookup.items()
    }

    combo_counts = get_combo_counts(session)
    log.info("Combo counts loaded for %d cards", len(combo_counts))

    card_roles = classify_all_cards(enriched, combo_counts)
    updated = update_card_roles_batch(session, card_roles, enriched)

    # Tally broad_role distribution
    from collections import Counter
    role_counts: Counter = Counter(v["broad_role"] for v in card_roles.values())

    log.info("Role distribution: %s", dict(role_counts.most_common()))
    return {
        "cards_classified": len(card_roles),
        "nodes_updated": updated,
        "role_distribution": dict(role_counts.most_common()),
    }
