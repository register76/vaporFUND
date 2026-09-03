from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    CashTransaction,
    Contribution,
    Recommendation,
)
from .services import (
    AllocationError,
    calculate_purchase_plan,
)


MONEY = Decimal("0.01")


class WorkflowError(Exception):
    pass


@transaction.atomic
def create_contribution(
    contribution_date,
    amount,
):
    if not isinstance(contribution_date, date):
        raise WorkflowError(
            "A valid contribution date is required."
        )

    try:
        amount = Decimal(amount).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise WorkflowError(
            "Enter a valid contribution amount."
        ) from error

    if amount <= 0:
        raise WorkflowError(
            "Contribution amount must be positive."
        )

    if Contribution.objects.filter(
        date=contribution_date
    ).exists():
        raise WorkflowError(
            f"A contribution already exists for "
            f"{contribution_date}."
        )

    previous = (
        Contribution.objects
        .select_for_update()
        .order_by("-sequence_number")
        .first()
    )

    sequence_number = (
        previous.sequence_number + 1
        if previous
        else 1
    )

    contribution = Contribution.objects.create(
        date=contribution_date,
        amount=amount,
        sequence_number=sequence_number,
    )

    CashTransaction.objects.create(
        date=contribution_date,
        transaction_type=(
            CashTransaction.TransactionType.DEPOSIT
        ),
        amount=amount,
        contribution=contribution,
        description=(
            f"Weekly vaporFUND contribution "
            f"{sequence_number}"
        ),
    )

    return contribution


@transaction.atomic
def generate_draft_recommendations(contribution):
    if contribution.processed:
        raise WorkflowError(
            "This contribution has already been processed."
        )

    existing = list(
        Recommendation.objects
        .select_for_update()
        .filter(contribution=contribution)
        .order_by("plan_order")
    )

    if any(
        recommendation.status
        != Recommendation.Status.DRAFT
        for recommendation in existing
    ):
        raise WorkflowError(
            "An existing recommendation is no longer a "
            "draft, so this purchase plan cannot be "
            "regenerated."
        )

    try:
        plan = calculate_purchase_plan(
            contribution.date
        )
    except AllocationError as error:
        raise WorkflowError(str(error)) from error

    recommendations = []

    for plan_order, decision in enumerate(
        plan.decisions,
        start=1,
    ):
        selected = decision.selected

        recommendation, _ = (
            Recommendation.objects.update_or_create(
                contribution=contribution,
                plan_order=plan_order,
                defaults={
                    "etf": selected.etf,
                    "action": decision.action,
                    "status": Recommendation.Status.DRAFT,
                    "available_cash": decision.available_cash,
                    "portfolio_value": decision.portfolio_value,
                    "current_value": selected.current_value,
                    "target_value": selected.target_value,
                    "target_shortfall": selected.shortfall,
                    "reference_price": selected.reference_price,
                    "price_date": selected.price_date,
                    "shares": decision.shares,
                    "estimated_cost": decision.estimated_cost,
                    "reason": decision.reason,
                },
            )
        )

        recommendations.append(recommendation)

    Recommendation.objects.filter(
        contribution=contribution,
        plan_order__gt=len(recommendations),
    ).delete()

    created = not existing

    return tuple(recommendations), plan, created


def generate_draft_recommendation(contribution):
    recommendations, plan, created = (
        generate_draft_recommendations(contribution)
    )

    return (
        recommendations[0],
        plan.decisions[0],
        created,
    )


@transaction.atomic
def add_contribution_and_recommend(
    contribution_date,
    amount,
):
    contribution = create_contribution(
        contribution_date=contribution_date,
        amount=amount,
    )

    recommendation, decision, created = (
        generate_draft_recommendation(
            contribution
        )
    )

    return contribution, recommendation, decision


@transaction.atomic
def approve_recommendation(
    sequence_number,
):
    recommendation = (
        Recommendation.objects
        .select_for_update()
        .select_related(
            "contribution",
            "etf",
        )
        .filter(
            contribution__sequence_number=sequence_number
        )
        .first()
    )

    if not recommendation:
        raise WorkflowError(
            f"No recommendation exists for contribution "
            f"{sequence_number}."
        )

    if recommendation.status != (
        Recommendation.Status.DRAFT
    ):
        raise WorkflowError(
            "Only a draft recommendation can be approved."
        )

    if recommendation.action != (
        Recommendation.Action.BUY
    ):
        raise WorkflowError(
            "A hold-cash recommendation has no purchase "
            "to approve."
        )

    if recommendation.shares <= 0:
        raise WorkflowError(
            "The recommendation does not contain a "
            "positive share quantity."
        )

    recommendation.status = (
        Recommendation.Status.COMPLIANCE_APPROVED
    )

    recommendation.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    return recommendation
