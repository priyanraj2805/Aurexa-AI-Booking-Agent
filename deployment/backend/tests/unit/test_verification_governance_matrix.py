from app.core.verification_governance import normalize_req_id, compute_requirement_status


def test_normalize_req_id_variants():
    assert normalize_req_id("gov-1") == "GOV-01"
    assert normalize_req_id("GOV_01") == "GOV-01"
    assert normalize_req_id(" GOV  2 ") == "GOV-02"


def test_status_matrix_satisfied():
    assert compute_requirement_status("passed", in_summary=True, in_requirements=True) == "satisfied"


def test_status_matrix_partial():
    assert (
        compute_requirement_status("passed", in_summary=False, in_requirements=True)
        == "partial"
    )
    assert (
        compute_requirement_status("missing", in_summary=True, in_requirements=True)
        == "partial"
    )


def test_status_matrix_unsatisfied():
    assert (
        compute_requirement_status("gaps_found", in_summary=False, in_requirements=True)
        == "unsatisfied"
    )
    assert (
        compute_requirement_status("missing", in_summary=False, in_requirements=True)
        == "unsatisfied"
    )


def test_status_matrix_orphaned():
    assert (
        compute_requirement_status(
            "missing", in_summary=False, in_requirements=True, orphaned=True
        )
        == "orphaned"
    )
    assert (
        compute_requirement_status("passed", in_summary=True, in_requirements=False)
        == "orphaned"
    )

