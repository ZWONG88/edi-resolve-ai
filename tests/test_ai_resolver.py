import os
import time

import pytest

from backend import ai_resolver
from backend.comparator import compare
from backend.parser import parse_invoice, parse_po
from backend.reference import load_price_contracts, load_uom_conversions
from tests.conftest import REFERENCE, fx

UOM = load_uom_conversions(REFERENCE / "uom_conversions.json")
PRICES = load_price_contracts(REFERENCE / "price_contracts.json")


def bundle(po: str, inv: str) -> dict:
    result = compare(parse_po(fx(po)), parse_invoice(fx(inv)), UOM, PRICES)
    return ai_resolver.build_evidence_bundle(result)


def good_response(b: dict) -> dict:
    fid = next(f["finding_id"] for f in b["findings"] if f["check"] == "unit_price")
    return {"status": "OK", "finding_ids": [fid], "possible_cause": "Invoice price differs "
            "by +0.10 per EA.", "cited_evidence_ids": ["po.1.unit_price", "inv.1.unit_price"],
            "uncertainty_note": "Hypothesis only.", "missing_evidence": [],
            "suggested_next_action": "VERIFY_CONTRACT_PRICE"}


def test_stub_matched() -> None:
    e = ai_resolver.explain(bundle("po_AT01.json", "inv_AT01.json"), stub_mode=True)
    assert e.status == "OK" and e.suggested_next_action == "NO_ACTION_NEEDED"


def test_stub_price_with_contract_cites_contract() -> None:
    e = ai_resolver.explain(bundle("po_AT05.json", "inv_AT05.json"), stub_mode=True)
    assert e.status == "OK"
    assert "contract.CTR-005" in e.cited_evidence_ids
    assert "hypothesis" in e.possible_cause.lower()


def test_stub_price_without_contract_abstains() -> None:
    e = ai_resolver.explain(bundle("po_AT04.json", "inv_AT04.json"), stub_mode=True)
    assert e.status == "AI_ABSTAIN"
    assert "contract" not in e.possible_cause.lower()
    assert e.missing_evidence


def test_stub_insufficient_evidence_abstains() -> None:
    e = ai_resolver.explain(bundle("po_AT03.json", "inv_AT03.json"), stub_mode=True)
    assert e.status == "AI_ABSTAIN"
    assert e.suggested_next_action == "REQUEST_UOM_MAPPING"


def test_valid_provider_response_accepted() -> None:
    b = bundle("po_AT04.json", "inv_AT04.json")
    e = ai_resolver.explain(b, stub_mode=False, generate=lambda s, u: good_response(b))
    assert e.status == "OK"


def test_invented_contract_id_rejected() -> None:
    b = bundle("po_AT04.json", "inv_AT04.json")
    resp = good_response(b) | {"possible_cause": "Contract CTR-FAKE-999 raised the price.",
                               "cited_evidence_ids": ["contract.CTR-FAKE-999"]}
    e = ai_resolver.explain(b, stub_mode=False, generate=lambda s, u: resp)
    assert e.status == "AI_ABSTAIN"


def test_wrong_delta_rejected() -> None:
    b = bundle("po_AT04.json", "inv_AT04.json")
    resp = good_response(b) | {"possible_cause": "Invoice price is 0.25 higher per EA."}
    e = ai_resolver.explain(b, stub_mode=False, generate=lambda s, u: resp)
    assert e.status == "AI_ABSTAIN"
    assert "0.25" in e.uncertainty_note


def test_unsupported_contract_diagnosis_rejected() -> None:
    b = bundle("po_AT04.json", "inv_AT04.json")
    resp = good_response(b) | {"possible_cause": "The supplier contract changed."}
    e = ai_resolver.explain(b, stub_mode=False, generate=lambda s, u: resp)
    assert e.status == "AI_ABSTAIN"


def test_disallowed_action_rejected() -> None:
    b = bundle("po_AT04.json", "inv_AT04.json")
    resp = good_response(b) | {"suggested_next_action": "APPROVE_PAYMENT"}
    e = ai_resolver.explain(b, stub_mode=False, generate=lambda s, u: resp)
    assert e.status == "AI_ABSTAIN"


def test_timeout_unavailable() -> None:
    def slow(s, u):
        time.sleep(2)
        return "{}"

    e = ai_resolver.explain(bundle("po_AT04.json", "inv_AT04.json"), stub_mode=False,
                            generate=slow, timeout_seconds=0.2)
    assert e.status == "AI_UNAVAILABLE"


def test_invalid_schema_unavailable() -> None:
    e = ai_resolver.explain(bundle("po_AT04.json", "inv_AT04.json"), stub_mode=False,
                            generate=lambda s, u: "Sure! The price went up.")
    assert e.status == "AI_UNAVAILABLE"


def test_provider_exception_unavailable() -> None:
    def boom(s, u):
        raise ConnectionError("down")

    e = ai_resolver.explain(bundle("po_AT04.json", "inv_AT04.json"), stub_mode=False,
                            generate=boom)
    assert e.status == "AI_UNAVAILABLE"


def test_bundle_excludes_description_text() -> None:
    b = bundle("po_AT15.json", "inv_AT15.json")
    import json
    text = json.dumps(b)
    assert "IGNORE" not in text and "CTR-FAKE-999" not in text


@pytest.mark.integration
def test_watsonx_live() -> None:
    if not (os.getenv("WATSONX_API_KEY") and os.getenv("WATSONX_PROJECT_ID")):
        from dotenv import load_dotenv

        load_dotenv()
    if not (os.getenv("WATSONX_API_KEY") and os.getenv("WATSONX_PROJECT_ID")):
        pytest.skip("watsonx credentials not configured")
    e = ai_resolver.explain(bundle("po_AT05.json", "inv_AT05.json"), stub_mode=False,
                            generate=ai_resolver.watsonx_generate, timeout_seconds=60)
    print(e)
    assert e.status in {"OK", "AI_ABSTAIN", "AI_UNAVAILABLE"}
    assert e.status != "AI_UNAVAILABLE", e.uncertainty_note
