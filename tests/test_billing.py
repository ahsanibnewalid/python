from decimal import Decimal

from billing import PLANS, BillingInterval, feature_enabled, get_plan


def test_initial_plans_are_valid():
    assert set(PLANS) == {"student_pro", "recruiter_pro", "university"}
    assert all(plan.currency == "BDT" for plan in PLANS.values())
    assert all(plan.amount > Decimal("0") for plan in PLANS.values())
    assert all(plan.interval == BillingInterval.MONTH for plan in PLANS.values())


def test_entitlements_are_server_side_data():
    assert feature_enabled("student_pro", "Advanced CV profile")
    assert not feature_enabled("student_pro", "Candidate search")
    assert feature_enabled("recruiter_pro", "Candidate search")
    assert feature_enabled("university", "Branded campus")
    assert not feature_enabled(None, "Branded campus")


def test_unknown_plan_is_rejected():
    try:
        get_plan("does_not_exist")
    except Exception as exc:
        assert "Unknown billing plan" in str(exc)
    else:
        raise AssertionError("Unknown plan should be rejected")
