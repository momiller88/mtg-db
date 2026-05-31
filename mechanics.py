import re
import logging
from neo4j import Session

log = logging.getLogger(__name__)

_BATCH_SIZE = 500

# ---------------------------------------------------------------------------
# Pattern definitions
# Each mechanic maps to a list of case-insensitive regex patterns.
# A card receives the tag if ANY pattern matches its oracle text.
# Patterns are compiled once at import time.
# ---------------------------------------------------------------------------

_RAW_PATTERNS: dict[str, list[str]] = {

    # --- Card advantage ---
    "draw": [
        r"draw (?:a|an|\d+|x|that many|cards equal) card",
        r"draws? (?:a|an|\d+|x) card",
        r"draw cards equal",
    ],
    "loot": [
        r"draw(?:s)? (?:a|an|\d+) card[^.]*discard",
        r"discard[^.]*draw(?:s)? (?:a|an|\d+) card",
    ],
    "scry": [r"\bscry\b"],
    "surveil": [r"\bsurveil\b"],
    "impulse": [
        r"look at the top (?:\d+|x) card",
        r"exile the top (?:\d+|x) card",
    ],

    # --- Mana / Ramp ---
    "ramp": [
        r"search your library for .{0,20}(?:basic )?land card",
        r"put .{0,20}(?:basic )?land card.{0,30}onto the battlefield",
        r"search your library for (?:a|an) (?:forest|plains|island|swamp|mountain)",
    ],
    "mana_rock": [
        r"\{t\}: add \{",
    ],
    "mana_dork": [
        # creature that taps to add mana — type check applied in detect_mechanics
        r"\{t\}: add \{",
    ],
    "cost_reduction": [
        r"cost(?:s)? \{?\d*[wubrgc]?\}? less to cast",
        r"you may cast .{0,40} without paying",
        r"reduce the cost",
        r"spells? you cast cost \{",
    ],
    "free_cast": [
        r"you may cast .{0,40} without paying its mana cost",
        r"cast .{0,40} without paying its mana cost",
    ],

    # --- Hard removal ---
    "destroy": [
        r"destroy target",
        r"destroy all",
        r"destroy each",
    ],
    "exile_removal": [
        r"exile target (?:creature|permanent|artifact|enchantment|planeswalker)",
        r"exile all (?:creature|permanent)",
    ],
    "bounce": [
        r"return target .{0,50} to (?:its|their) owner",
        r"return target .{0,50} to your hand",
    ],
    "tuck": [
        r"put .{0,40} into (?:its|their) owner.s library",
        r"shuffle .{0,30} into (?:its|their|your) (?:owner.s )?library",
    ],
    "wrath": [
        r"destroy all creatures",
        r"exile all creatures",
        r"all creatures get -",
        r"deals \d+ damage to each creature",
        r"each creature (?:gets|deals) .{0,30} and dies",
    ],
    "damage_spell": [
        r"deals? (?:\{x\}|\d+) damage to (?:any target|target creature|each creature|each opponent|target player)",
    ],

    # --- Counterspells ---
    "counterspell": [
        r"counter target (?:spell|ability|activated|triggered)",
        r"counter that spell",
        r"counter it",
    ],

    # --- Tutors ---
    "tutor": [
        r"search your library for (?:a card|an? \w+ card|up to)",
    ],

    # --- Recursion ---
    "recursion": [
        r"return .{0,60} from (?:your |a )?graveyard (?:to|onto) (?:the battlefield|your hand|your library)",
        r"from (?:your |a )?graveyard .{0,30} to (?:the battlefield|your hand)",
    ],

    # --- Token generation ---
    "token_gen": [
        r"create(?:s)? (?:a|an|\d+|x|that many) .{0,40}token",
        r"put(?:s)? (?:a|an|\d+|x) .{0,40}token .{0,20}onto the battlefield",
    ],

    # --- Combo enablers ---
    "untap_effect": [
        r"untap (?:target|all|each|up to \d+) .{0,40}(?:creature|land|permanent|artifact)",
        r"untap (?:it|them|that creature)",
    ],
    "copy": [
        r"creat(?:e|es) a copy of",
        r"copy(?:ies)? target",
        r"cop(?:y|ies) (?:it|that|each)",
    ],
    "flicker": [
        r"exile .{0,50}then return (?:it|them) .{0,30}to the battlefield",
        r"exile target .{0,40}\. return it to the battlefield",
    ],
    "extra_turns": [
        r"take an extra turn",
        r"takes? an? extra turn",
    ],
    "storm": [r"\bstorm\b"],
    "proliferate": [r"\bproliferate\b"],

    # --- Stax / Control ---
    "tax": [
        r"cost(?:s)? \{?\d*[wubrgc]?\}? more to cast",
        r"spells? .{0,20} cost .{0,10} more",
        r"pay .{0,20} more to cast",
    ],
    "stax": [
        r"don.t untap",
        r"skip .{0,20}untap",
        r"creature.* can.t attack",
        r"player.* can.t cast",
        r"opponent.* can.t",
        r"tap .{0,30}don.t untap",
    ],
    "discard": [
        r"(?:target player|each opponent|each player|opponent) (?:discards?|puts? .{0,20} into (?:their|a) graveyard)",
        r"discard (?:their hand|up to \d+ card)",
    ],
    "mill": [
        r"\bmill\b",
        r"put(?:s)? the top (?:\d+|x) card.* (?:of (?:their|your|each) library )?into (?:their|a|the|your) graveyard",
    ],

    # --- Life ---
    "life_gain": [
        r"you gain \d+ life",
        r"gain life equal",
        r"you gain x life",
        r"gains? \{?\d+\}? life",
    ],
    "life_drain": [
        r"each opponent loses \d+ life",
        r"loses? \d+ life .{0,20} you gain",
        r"opponent.* loses? .{0,10} life and you gain",
    ],

    # --- Graveyard interaction ---
    "graveyard_hate": [
        r"exile .{0,30}graveyard",
        r"remove .{0,20} from .{0,10}graveyard",
    ],

    # --- Triggered value engines ---
    "etb_trigger": [
        r"when(?:ever)? .{0,60} enters(?: the battlefield)?",
        r"when .{0,30} enters,",
    ],
    "death_trigger": [
        r"when(?:ever)? .{0,60} dies",
        r"when(?:ever)? .{0,60} is put into a graveyard from",
    ],
    "attack_trigger": [
        r"when(?:ever)? .{0,60} attacks?",
    ],
    "cast_trigger": [
        r"when(?:ever)? you cast",
        r"whenever a player casts",
    ],

    # --- Evasion ---
    "unblockable": [
        r"can.t be blocked\b",
    ],

    # --- Land interaction ---
    "land_denial": [
        r"destroy target land",
        r"destroy all lands",
        r"land.{0,20}doesn.t untap",
    ],
    "landfall": [
        r"whenever a land enters(?: the battlefield)? under your control",
        r"whenever you play a land",
    ],

    # --- Counters ---
    "counter_synergy": [
        r"(?:place|put) .{0,10}\+1/\+1 counter",
        r"(?:add|remove) .{0,10}counter",
        r"for each .{0,20}counter",
        r"whenever .{0,30}counter is placed",
    ],

    # --- Sacrifice ---
    "sacrifice_outlet": [
        r"sacrifice (?:a|another|target) .{0,30}:",
        r"sacrifice (?:a|another|target) creature:",
        r"sacrifice (?:a|another|target) permanent:",
    ],
    "sacrifice_synergy": [
        r"whenever .{0,40} (?:is sacrificed|dies|is put into a graveyard from the battlefield)",
        r"when you sacrifice",
    ],

    # --- Tribal ---
    "lord": [
        r"other .{0,30} you control get \+",
        r"each .{0,20} you control (?:gets?|has) \+",
    ],
}

# Compile all patterns once
_COMPILED: dict[str, list[re.Pattern]] = {
    tag: [re.compile(p, re.IGNORECASE) for p in patterns]
    for tag, patterns in _RAW_PATTERNS.items()
}

# Scryfall keywords that map directly to mechanic tags
_KEYWORD_MAP: dict[str, str] = {
    "flying":        "flying",
    "trample":       "trample",
    "haste":         "haste",
    "vigilance":     "vigilance",
    "lifelink":      "lifelink",
    "deathtouch":    "deathtouch",
    "first strike":  "first_strike",
    "double strike": "double_strike",
    "reach":         "reach",
    "menace":        "menace",
    "hexproof":      "hexproof",
    "indestructible":"indestructible",
    "flash":         "flash",
    "ward":          "ward",
    "shroud":        "shroud",
    "persist":       "persist",
    "undying":       "undying",
    "annihilator":   "annihilator",
    "infect":        "infect",
    "wither":        "wither",
    "storm":         "storm",
    "cascade":       "cascade",
    "convoke":       "convoke",
    "delve":         "delve",
    "emerge":        "emerge",
    "affinity":      "affinity",
    "equip":         "equip",
    "reconfigure":   "reconfigure",
    "prototype":     "prototype",
    "partner":       "partner",
    "morph":         "morph",
    "mutate":        "mutate",
    "suspend":       "suspend",
    "cycling":       "cycling",
    "kicker":        "kicker",
    "overload":      "overload",
    "flashback":     "flashback",
    "jump-start":    "jump_start",
    "escape":        "escape",
}


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def detect_mechanics(oracle_text: str, keywords: list[str],
                     type_line: str = "") -> set[str]:
    """Return the set of mechanic tags for a single card.

    Args:
        oracle_text: Card's full oracle text (may be empty).
        keywords:    Scryfall keyword list for the card.
        type_line:   Card type line, used to disambiguate mana_rock vs mana_dork.
    """
    mechanics: set[str] = set()
    text = oracle_text or ""

    # Pattern matching against oracle text
    for tag, compiled in _COMPILED.items():
        for pattern in compiled:
            if pattern.search(text):
                mechanics.add(tag)
                break

    # Disambiguate mana_rock vs mana_dork
    if "mana_rock" in mechanics or "mana_dork" in mechanics:
        mechanics.discard("mana_rock")
        mechanics.discard("mana_dork")
        if "Creature" in type_line:
            mechanics.add("mana_dork")
        else:
            mechanics.add("mana_rock")

    # Keywords from Scryfall
    for kw in keywords:
        tag = _KEYWORD_MAP.get(kw.lower())
        if tag:
            mechanics.add(tag)

    # Landfall already covered by oracle pattern, but Storm/Cascade also in keywords
    return mechanics


# ---------------------------------------------------------------------------
# Bulk tagging
# ---------------------------------------------------------------------------

def tag_all_cards(card_lookup: dict) -> dict[str, set[str]]:
    """Return {card_name: set[mechanic_tag]} for every card in the lookup."""
    results: dict[str, set[str]] = {}
    for name, data in card_lookup.items():
        results[name] = detect_mechanics(
            data.get("oracle_text") or "",
            data.get("keywords") or [],
            data.get("type_line") or "",
        )
    log.info("Tagged mechanics for %d cards", len(results))
    return results


# ---------------------------------------------------------------------------
# Neo4j persistence
# ---------------------------------------------------------------------------

def update_card_mechanics_batch(session: Session, card_mechanics: dict[str, set[str]],
                                card_lookup: dict) -> int:
    """Write the mechanics list to each Card node in AuraDB.

    Args:
        session:       Active Neo4j session.
        card_mechanics: Output of tag_all_cards().
        card_lookup:    Used to resolve scryfall_id by name.

    Returns:
        Number of card nodes updated.
    """
    rows = []
    for name, mechanics in card_mechanics.items():
        sid = card_lookup.get(name, {}).get("scryfall_id")
        if sid:
            rows.append({"scryfall_id": sid, "mechanics": sorted(mechanics)})

    total = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        chunk = rows[i : i + _BATCH_SIZE]
        session.run(
            """
            UNWIND $rows AS row
            MATCH (c:Card {scryfall_id: row.scryfall_id})
            SET c.mechanics = row.mechanics
            """,
            rows=chunk,
        )
        total += len(chunk)

    log.info("Updated mechanics on %d Card nodes", total)
    return total


def run_mechanic_tagging(session: Session, card_lookup: dict) -> dict:
    """Tag all cards with mechanics and persist to Neo4j.

    Returns summary dict.
    """
    card_mechanics = tag_all_cards(card_lookup)

    # Tally tag distribution for the summary
    from collections import Counter
    tag_counts: Counter = Counter()
    for mechanics in card_mechanics.values():
        tag_counts.update(mechanics)

    updated = update_card_mechanics_batch(session, card_mechanics, card_lookup)

    top_tags = tag_counts.most_common(10)
    log.info("Top mechanics: %s", top_tags)

    return {
        "cards_tagged": len(card_mechanics),
        "nodes_updated": updated,
        "unique_tags": len(tag_counts),
        "top_10": dict(top_tags),
    }
