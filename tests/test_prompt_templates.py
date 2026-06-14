from types import MappingProxyType

from diplomacy_llm.config import Settings
from diplomacy_llm.messaging import DiplomacyMessage
from diplomacy_llm.messaging.schemas import MESSAGES_RESPONSE_SCHEMA
from diplomacy_llm.phase_snapshot import PowerPhaseSnapshot
from diplomacy_llm.prompt_templates import (
    get_order_prompt_template,
    get_order_prompt_template_sha256,
)
from diplomacy_llm.prompts import get_user_prompt
from diplomacy_llm.response_schemas import (
    ORDERS_RESPONSE_SCHEMA,
    orders_response_schema_for,
)


def make_settings() -> Settings:
    return Settings(
        default_model="fake/model",
        power_models={
            "FRANCE": "fake/model",
            "GERMANY": "fake/model",
            "ITALY": "fake/model",
            "AUSTRIA": "fake/model",
        },
        map_name="configs/maps/custom.map",
        max_years=3,
        win_score=12,
        total_scs=42,
    )


def make_snapshot() -> PowerPhaseSnapshot:
    return PowerPhaseSnapshot(
        power_name="FRANCE",
        phase="S1901M",
        phase_type="M",
        own_units=("A PAR",),
        own_centers=("PAR",),
        all_units=MappingProxyType(
            {
                "FRANCE": ("A PAR",),
                "GERMANY": ("A MUN",),
            },
        ),
        all_centers=MappingProxyType(
            {
                "FRANCE": ("PAR",),
                "GERMANY": ("MUN",),
            },
        ),
        last_phase=None,
        last_phase_results=MappingProxyType({}),
        orderable_locations=("PAR",),
        possible_orders=MappingProxyType({"PAR": ("A PAR H", "A PAR - BUR")}),
    )


def test_baseline_system_prompt_renders_settings_values() -> None:
    prompt = get_order_prompt_template("baseline_orders").render_system(
        "FRANCE",
        make_settings(),
    )

    assert "There are 4 powers in this game: FRANCE, GERMANY, ITALY, AUSTRIA." in prompt
    assert "To win, you must control 12 supply centers (SCs)." in prompt
    assert "The map has 42 SCs total." in prompt
    assert "To win, you must control 9 supply centers" not in prompt
    assert "The map has 34 SCs total." not in prompt


def test_system_prompt_keeps_adjustment_order_count_phase_specific() -> None:
    prompt = get_order_prompt_template("baseline_orders").render_system(
        "FRANCE",
        make_settings(),
    )

    assert "In Movement and Retreat phases" in prompt
    assert "In Adjustment phases, follow the build/disband/WAIVE requirement" in prompt
    assert "You MUST submit exactly one order per orderable unit." not in prompt


def test_baseline_system_prompt_is_neutral_and_documents_legal_tactics() -> None:
    prompt = get_order_prompt_template("baseline_orders").render_system(
        "FRANCE",
        make_settings(),
    )

    assert "## Legal Diplomacy Tactics" in prompt
    assert "cooperation, negotiation, promises, bluffing, selective truth" in prompt
    assert "deception, and betrayal are legal parts of Diplomacy" in prompt
    assert "These tactics are options, not requirements." in prompt
    assert "## Communication Language" in prompt
    assert "Use English for all natural-language outputs" in prompt
    assert "private message bodies, reasoning" in prompt
    assert "Your objective is to win by reaching 12 supply centers" in prompt
    assert "Do not assume a fixed play style" in prompt
    assert "eliminate rivals" not in prompt
    assert "attack when you can" not in prompt
    assert "Your goal is to expand" not in prompt


def test_baseline_system_prompt_uses_valid_orders_as_location_authority() -> None:
    prompt = get_order_prompt_template("baseline_orders").render_system(
        "FRANCE",
        make_settings(),
    )

    assert "The valid orders list provided each turn is" in prompt
    assert (
        "the authority for which coded locations and order strings may be used"
        in prompt
    )
    assert "The full list of valid locations and their codes" not in prompt
    assert "is provided each turn in your orders list" not in prompt


def test_baseline_user_prompt_matches_deterministic_snapshot() -> None:
    prompt = get_order_prompt_template("baseline_orders").render_user(make_snapshot())

    assert (
        prompt
        == """=== PHASE: S1901M (Movement Phase) ===

YOUR UNITS:           ['A PAR']
YOUR SUPPLY CENTERS:  ['PAR'] (1 SCs)

OTHER POWERS:
  GERMANY: units=['A MUN']  SCs=['MUN'] (1)

VALID ORDERS FOR YOUR UNITS:
  PAR: ['A PAR H', 'A PAR - BUR']

Pick exactly one order per orderable location from the valid orders list above.
Your response must contain 1 order(s).
"""
    )


def test_orders_response_schema_contract_is_orders_and_reasoning_only() -> None:
    json_schema = ORDERS_RESPONSE_SCHEMA["json_schema"]
    schema = json_schema["schema"]
    properties = schema["properties"]

    assert ORDERS_RESPONSE_SCHEMA["type"] == "json_schema"
    assert json_schema["name"] == "diplomacy_orders"
    assert json_schema["strict"] is True
    assert set(properties) == {"orders", "reasoning"}
    assert properties["orders"]["type"] == "array"
    assert properties["orders"]["items"] == {"type": "string"}
    orders_description = properties["orders"]["description"]
    assert "The phase prompt defines how many entries to return." in orders_description
    assert "one per orderable unit" not in orders_description
    assert properties["reasoning"]["type"] == "string"
    assert schema["required"] == ["orders", "reasoning"]
    assert schema["additionalProperties"] is False


def test_orders_response_schema_helper_returns_orders_schema() -> None:
    assert orders_response_schema_for() is ORDERS_RESPONSE_SCHEMA


def test_messages_response_schema_uses_light_structured_envelopes() -> None:
    json_schema = MESSAGES_RESPONSE_SCHEMA["json_schema"]
    schema = json_schema["schema"]
    messages = schema["properties"]["messages"]
    envelope = messages["items"]

    assert MESSAGES_RESPONSE_SCHEMA["type"] == "json_schema"
    assert json_schema["name"] == "diplomacy_messages"
    assert json_schema["strict"] is True
    assert schema["required"] == ["messages"]
    assert schema["additionalProperties"] is False
    assert messages["type"] == "array"
    assert "messaging decision" in messages["description"]
    assert envelope["required"] == [
        "recipient",
        "intent",
        "body",
        "referenced_locations",
        "requested_orders",
        "offered_orders",
    ]
    assert envelope["properties"]["intent"]["enum"] == [
        "propose",
        "request_support",
        "offer_support",
        "coordinate",
        "accept",
        "reject",
        "warn",
        "share_intent",
        "other",
    ]
    assert envelope["properties"]["body"]["type"] == "string"
    assert envelope["additionalProperties"] is False


def test_prompt_template_hash_tracks_template_content() -> None:
    baseline_hash = get_order_prompt_template_sha256("baseline_orders")
    contextual_hash = get_order_prompt_template_sha256("orders_with_context")

    assert baseline_hash == (
        "2953f680211219465a31b6af50f264a54ab55c1548477b3d30f475aa3617ee28"
    )
    assert contextual_hash == (
        "92ad432b19c41e9a8d3afd9a71d3042291b62c59d4c4b1985c61d449f53d6689"
    )
    assert baseline_hash != contextual_hash


def test_orders_with_context_variant_renders_future_placeholders() -> None:
    prompt = get_order_prompt_template("orders_with_context").render_user(
        make_snapshot(),
    )

    assert "CONTEXT SECTIONS:" in prompt
    assert (
        "- Strategy metadata: configured decision guidance, not current messages."
        in prompt
    )
    assert (
        "- Messages: current-phase private press visible to you when enabled." in prompt
    )
    assert "STRATEGY METADATA:\n  (no strategy metadata configured)" in prompt
    assert "MESSAGES:\n  (no messages provided)" in prompt
    assert "VALID ORDERS FOR YOUR UNITS:\n  PAR: ['A PAR H', 'A PAR - BUR']" in prompt
    assert prompt.index("CONTEXT SECTIONS:") < prompt.index("STRATEGY METADATA:")
    assert prompt.index("MESSAGES:") < prompt.index("VALID ORDERS FOR YOUR UNITS:")


def test_orders_with_context_variant_places_strategy_owned_text() -> None:
    prompt = get_order_prompt_template("orders_with_context").render_user(
        make_snapshot(),
        strategy=(
            "Doctrine: Plan before acting.\n"
            "Phase objective: Secure a nearby supply center.\n"
            "Priorities:\n"
            "  - Coordinate support.\n"
            "Constraints:\n"
            "  - Use legal orders only.\n"
            "Risk notes:\n"
            "  - Avoid overextension."
        ),
    )

    assert (
        "STRATEGY METADATA:\n"
        "Doctrine: Plan before acting.\n"
        "Phase objective: Secure a nearby supply center.\n"
        "Priorities:\n"
        "  - Coordinate support.\n"
        "Constraints:\n"
        "  - Use legal orders only.\n"
        "Risk notes:\n"
        "  - Avoid overextension."
    ) in prompt
    assert "VALID ORDERS FOR YOUR UNITS:\n  PAR: ['A PAR H', 'A PAR - BUR']" in prompt


def test_legacy_user_prompt_facade_respects_configured_variant() -> None:
    settings = make_settings().model_copy(
        update={"prompt_variant": "orders_with_context"},
    )

    prompt = get_user_prompt(make_snapshot(), settings)

    assert "STRATEGY METADATA:\n  (no strategy metadata configured)" in prompt


def test_orders_with_context_variant_preserves_valid_orders_block() -> None:
    prompt = get_order_prompt_template("orders_with_context").render_user(
        make_snapshot(),
    )

    assert "VALID ORDERS FOR YOUR UNITS:\n  PAR: ['A PAR H', 'A PAR - BUR']" in prompt


def test_adjustment_build_prompt_renders_build_quota() -> None:
    snapshot = PowerPhaseSnapshot(
        power_name="FRANCE",
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR",),
        own_centers=("BRE", "MAR"),
        all_units=MappingProxyType({"FRANCE": ("A BUR",)}),
        all_centers=MappingProxyType({"FRANCE": ("BRE", "MAR")}),
        last_phase=None,
        last_phase_results=MappingProxyType({}),
        orderable_locations=("BRE", "MAR"),
        possible_orders=MappingProxyType(
            {
                "BRE": ("A BRE B", "F BRE B", "WAIVE"),
                "MAR": ("A MAR B", "F MAR B", "WAIVE"),
            },
        ),
        adjustment_build_count=1,
    )
    prompt = get_order_prompt_template("baseline_orders").render_user(snapshot)

    assert "ADJUSTMENT REQUIREMENT: You may build up to 1 unit(s)." in prompt
    assert "BRE: ['A BRE B', 'F BRE B', 'WAIVE']" in prompt


def test_adjustment_disband_prompt_renders_disband_quota() -> None:
    snapshot = PowerPhaseSnapshot(
        power_name="FRANCE",
        phase="W1901A",
        phase_type="A",
        own_units=("A BUR", "A PIC", "F MAO"),
        own_centers=("BRE", "MAR"),
        all_units=MappingProxyType({"FRANCE": ("A BUR", "A PIC", "F MAO")}),
        all_centers=MappingProxyType({"FRANCE": ("BRE", "MAR")}),
        last_phase=None,
        last_phase_results=MappingProxyType({}),
        orderable_locations=("BUR", "MAO", "PIC"),
        possible_orders=MappingProxyType(
            {
                "BUR": ("A BUR D",),
                "MAO": ("F MAO D",),
                "PIC": ("A PIC D",),
            },
        ),
        adjustment_disband_count=1,
    )
    prompt = get_order_prompt_template("baseline_orders").render_user(snapshot)

    assert "ADJUSTMENT REQUIREMENT: You must disband exactly 1 unit(s)." in prompt
    assert "BUR: ['A BUR D']" in prompt
