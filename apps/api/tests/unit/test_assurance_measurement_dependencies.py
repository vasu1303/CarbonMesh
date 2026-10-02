from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from app.modules.assurance.errors import AssuranceValidationError
from app.modules.assurance.service import _base_context, _hash
from app.modules.measurement.domain import sha256_payload
from tests.unit.test_assurance_service import _dependencies, _id, _requirement, _service


def _calculation():
    return {
        "id": _id(50),
        "status": "completed",
        "reporting_period_id": _id(4),
        "run_method_version": "2.0.0",
        "run_code_version": "carbonmesh-api-0.1.0",
        "rounding_policy": "ROUND_HALF_EVEN",
        "method_id": _id(51),
        "method_key": "measurement.scope2.location_based.hourly",
        "method_version": "2.0.0",
        "code_version": "carbonmesh-api-0.1.0",
        "configuration": {"formula": "kWh * gCO2e_per_kWh / 1000"},
        "effective_from": date(2026, 1, 1),
        "effective_to": None,
        "is_active": True,
        "frozen_method": {
            "method_definition_id": str(_id(51)),
            "method_key": "measurement.scope2.location_based.hourly",
            "method_version": "2.0.0",
            "code_version": "carbonmesh-api-0.1.0",
            "method_configuration": {"formula": "kWh * gCO2e_per_kWh / 1000"},
        },
    }


@pytest.mark.parametrize(("field", "value"), [
    ("status", "failed"),
    ("reporting_period_id", _id(100)),
    ("is_active", False),
    ("effective_from", date(2027, 1, 1)),
    ("effective_to", date(2026, 6, 30)),
    ("run_method_version", "1.0.0"),
    ("run_code_version", "different-code"),
    ("method_key", "renamed-method"),
    ("configuration", {"formula": "changed"}),
])
def test_new_disclosure_rejects_changed_or_invalid_calculation_dependencies(field, value):
    calculation = _calculation()
    dependencies = replace(_dependencies(), measurement_calculation=calculation)
    _service()._validate_draft_dependencies(dependencies, [_requirement()])
    calculation[field] = value
    with pytest.raises(AssuranceValidationError):
        _service()._validate_draft_dependencies(dependencies, [_requirement()])


def test_recorded_method_hash_detects_configuration_changes_without_full_snapshot():
    calculation = _calculation()
    calculation["frozen_method"] = None
    calculation["configuration"] = {"label": "Synthetic aluminium méthode"}
    calculation["recorded_method_hash"] = sha256_payload({
        "key": calculation["method_key"],
        "version": calculation["method_version"],
        "configuration": calculation["configuration"],
    })
    dependencies = replace(_dependencies(), measurement_calculation=calculation)
    _service()._validate_draft_dependencies(dependencies, [_requirement()])
    calculation["configuration"] = {"label": "Changed synthetic method"}
    with pytest.raises(AssuranceValidationError, match="method has changed"):
        _service()._validate_draft_dependencies(dependencies, [_requirement()])


def test_effective_dates_bind_draft_hash_even_when_new_dates_still_overlap_period():
    original = replace(_dependencies(), measurement_calculation=_calculation())
    changed = deepcopy(original)
    changed.measurement_calculation["effective_to"] = date(2026, 12, 31)
    _service()._validate_draft_dependencies(changed, [_requirement()])
    assert _hash(_base_context(original, [_requirement()])) != _hash(
        _base_context(changed, [_requirement()])
    )
