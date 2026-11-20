"""German invoice IVN_DE_004: ligature repair and pipe-table line items when the LLM gives no usable JSON."""
from decimal import Decimal

from app.agents.parser import apply_fallbacks, parse_invoice
from pathlib import Path

import pytest

from app.core.llm import LLMError
from app.core.rules import load_rules
from app.extraction.extract import fix_ligatures
from app.schemas.models import ExtractedInvoice

@pytest.fixture
def rules():
    return load_rules(Path(__file__).resolve().parents[3] / "config" / "rules.yaml")


TEXT = """HafenLogis+k GmbH
Rechnungsnummer: RE-2025-004
Bestellnummer: PO-1004
Ar+kelcode | Beschreibung | Menge | Preis | Gesamt
SKU-301 | Transportkisten | 40 | 25.00€| 1000.00€
SKU-302 | Sicherheitswesten | 60 | 10.00€| 600.00€
Gesamtbetrag: 1760.00€
"""


def test_ti_ligature_is_restored_but_real_plus_signs_are_kept():
    assert fix_ligatures("HafenLogis+k Ar+kelcode") == "HafenLogistik Artikelcode"
    assert fix_ligatures("A+B 3+4 C++") == "A+B 3+4 C++"


def test_pipe_table_line_items_found_by_regex(rules):
    inv = apply_fallbacks(ExtractedInvoice(), TEXT, rules)
    assert [(li.item_code, li.quantity, li.unit_price, li.line_total) for li in inv.line_items] == [
        ("SKU-301", Decimal("40"), Decimal("25.00"), Decimal("1000.00")),
        ("SKU-302", Decimal("60"), Decimal("10.00"), Decimal("600.00"))]


class FlakyLLM:
    """Bad (non-JSON) first reply, good second reply to a *different* prompt (a cached replay would stay bad)."""
    def __init__(self):
        self.prompts = []

    def complete(self, role, system, user, **kw):
        self.prompts.append(user)
        if len(self.prompts) == 1:
            return "Sure! Here is the invoice summary."
        if user == self.prompts[0]:
            raise LLMError("replayed the cached bad reply")
        return '{"invoice_number": "RE-2025-004", "line_items": [{"item_code": "SKU-301", "quantity": 40, "unit_price": 25, "line_total": 1000}]}'


def test_parser_retry_uses_a_new_prompt_and_recovers(rules):
    llm = FlakyLLM()
    inv = parse_invoice(TEXT, llm, rules)
    assert inv.invoice_number == "RE-2025-004" and len(inv.line_items) == 1
    assert len(set(llm.prompts)) == 2
