from __future__ import annotations


SEMANTIC_RELATIONS = (
    "none",
    "sequence",
    "parallel",
    "comparison",
    "causality",
    "hierarchy",
    "convergence",
    "cycle",
)
SEMANTIC_RELATION_SET = frozenset(SEMANTIC_RELATIONS)

# Keep aliases intentionally narrow. The planner prompt asks for the canonical
# enum; aliases only cover unambiguous serialization variants, not broad words
# such as "list", "flow", "tree", or "compare" that could hide a bad judgment.
SEMANTIC_RELATION_ALIASES = {
    "cause_effect": "causality",
    "cause_and_effect": "causality",
    "many_to_one": "convergence",
    "inputs_to_output": "convergence",
}


def is_supported_semantic_relation_label(value: object) -> bool:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return bool(key) and (key in SEMANTIC_RELATION_SET or key in SEMANTIC_RELATION_ALIASES)


def normalize_semantic_relation(value: object, *, default: str = "none") -> str:
    """Normalize an explicit relation label without guessing from page content."""
    fallback = default if default in SEMANTIC_RELATION_SET else "none"
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return fallback
    if key in SEMANTIC_RELATION_SET:
        return key
    return SEMANTIC_RELATION_ALIASES.get(key, fallback)


def semantic_relation_prompt_rule(value: object) -> str:
    relation = normalize_semantic_relation(value)
    rules = {
        "none": (
            "No explicit semantic relationship is confirmed. Do not invent a timeline, cycle, hierarchy, "
            "containment, or causal flow merely to decorate the page."
        ),
        "sequence": (
            "The items form an ordered sequence. Direction, numbering, or arrows may show that order, "
            "but do not add steps that are absent from the supplied copy."
        ),
        "parallel": (
            "The items are equal-status parallel components. Give them equal visual weight; do not turn them "
            "into numbered steps, a timeline, a hierarchy, a loop, or a cause-and-effect chain."
        ),
        "comparison": (
            "The page is a comparison. Align the compared sides or dimensions so differences are readable; "
            "do not imply that one side happens before the other."
        ),
        "causality": (
            "The page expresses cause and effect. Use directional structure only for relationships stated in "
            "the copy, and do not invent intermediate causes or outcomes."
        ),
        "hierarchy": (
            "The page expresses parent-child or level relationships. Nesting, tiers, or tree structure are "
            "appropriate; do not render the levels as a chronological process."
        ),
        "convergence": (
            "Several independent inputs combine into one result. Keep the inputs visibly independent and let "
            "them converge on the stated outcome; do not draw a loop or containment relationship."
        ),
        "cycle": (
            "The page describes a genuine repeating cycle. A closed loop is appropriate, but only when every "
            "link and return path is supported by the supplied copy."
        ),
    }
    return f"Semantic relationship ({relation}): {rules[relation]}"
