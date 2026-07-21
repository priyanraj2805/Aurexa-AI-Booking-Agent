from app.core.verification_classification import classify_gap_type


def test_classifies_implementation_debt():
    assert (
        classify_gap_type(
            verification_status="gaps_found",
            in_summary=False,
            in_traceability=True,
            tests_green=False,
        )
        == "implementation_debt"
    )


def test_classifies_documentation_debt():
    assert (
        classify_gap_type(
            verification_status="passed",
            in_summary=False,
            in_traceability=True,
            tests_green=True,
        )
        == "documentation_debt"
    )


def test_classifies_process_debt():
    assert (
        classify_gap_type(
            verification_status="missing",
            in_summary=False,
            in_traceability=False,
            tests_green=True,
        )
        == "process_debt"
    )

