"""Loads and validates config/rules.yaml, and maps its field names to the audit schema names."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# rules.yaml names -> internal / DB names (brief's audit schema)
DEFAULT_ALIASES: dict[str, str] = {
    "invoice_no": "invoice_number",
    "qty": "quantity",
    "total": "line_total",
}

# A required header field that may be satisfied by any of these (vendor id is resolved later
# from the vendor name through the ERP, so the name is accepted at data-validation time).
SATISFIED_BY: dict[str, tuple[str, ...]] = {"vendor_id": ("vendor_id", "vendor_name")}

# rules.yaml policy action -> recommendation
ACTION_TO_RECOMMENDATION = {"reject": "reject", "manual_review": "review", "flag": "review", "review": "review"}


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Rules:
    required_header: tuple[str, ...]
    required_line: tuple[str, ...]
    data_types: dict[str, str]
    price_tol_pct: float
    qty_tol_pct: float
    tax_tol_pct: float
    accepted_currencies: tuple[str, ...]
    currency_symbol_map: dict[str, str]
    missing_field_action: str
    total_mismatch_action: str
    invalid_currency_action: str
    auto_approve_confidence: float
    aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))

    def internal(self, name: str) -> str:
        return self.aliases.get(name, name)

    @property
    def header_fields(self) -> tuple[str, ...]:
        return tuple(self.internal(f) for f in self.required_header)

    @property
    def line_fields(self) -> tuple[str, ...]:
        return tuple(self.internal(f) for f in self.required_line)


def parse_rules(raw: dict) -> Rules:
    try:
        req = raw["required_fields"]
        tol = raw["tolerances"]
        pol = raw["validation_policies"]
        rules = Rules(
            required_header=tuple(req["header"]),
            required_line=tuple(req["line_item"]),
            data_types=dict(raw.get("data_types", {})),
            price_tol_pct=float(tol["price_difference_percent"]),
            qty_tol_pct=float(tol["quantity_difference_percent"]),
            tax_tol_pct=float(tol["tax_difference_percent"]),
            accepted_currencies=tuple(str(c).upper() for c in raw["accepted_currencies"]),
            currency_symbol_map={str(k): str(v) for k, v in raw.get("currency_symbol_map", {}).items()},
            missing_field_action=str(pol["missing_field_action"]),
            total_mismatch_action=str(pol["total_mismatch_action"]),
            invalid_currency_action=str(pol["invalid_currency_action"]),
            auto_approve_confidence=float(pol["auto_approve_confidence_threshold"]),
            aliases={**DEFAULT_ALIASES, **{str(k): str(v) for k, v in raw.get("field_aliases", {}).items()}},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RulesError(f"invalid rules.yaml: missing or malformed key {exc}") from exc
    for name, action in (
        ("missing_field_action", rules.missing_field_action),
        ("total_mismatch_action", rules.total_mismatch_action),
        ("invalid_currency_action", rules.invalid_currency_action),
    ):
        if action not in ACTION_TO_RECOMMENDATION:
            raise RulesError(f"invalid rules.yaml: {name}={action!r} (expected one of {sorted(ACTION_TO_RECOMMENDATION)})")
    return rules


def load_rules(path: Path | str) -> Rules:
    p = Path(path)
    if not p.is_file():
        raise RulesError(f"rules file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RulesError("rules.yaml must be a mapping")
    return parse_rules(raw)
