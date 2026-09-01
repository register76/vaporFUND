from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    CashTransaction,
    Contribution,
    Recommendation,
)
from .services import AllocationError, calculate_allocation


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


def generate_draft_recommendation(contribution):
    if contribution.processed:
        raise WorkflowError(
            "This contribution has already been processed."
        )

    existing = Recommendation.objects.filter(
        contribution=contribution
    ).first()

    if (
        existing
        and existing.status
        != Recommendation.Status.DRAFT
    ):
        raise WorkflowError(
            "The existing recommendation is no longer a "
            "draft and cannot be regenerated."
        )

    try:
        decision = calculate_allocation(
            contribution.date
        )
    except AllocationError as error:
        raise WorkflowError(str(error)) from error

    selected = decision.selected

    recommendation, created = (
        Recommendation.objects.update_or_create(
            contribution=contribution,
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

    return recommendation, decision, created


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
