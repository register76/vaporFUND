from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum

from .models import (
    CashTransaction,
    Contribution,
    HoldingLot,
    Recommendation,
    TradeExecution,
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

    active_contribution = (
        Contribution.objects
        .select_for_update()
        .filter(
            processed=False,
            recommendations__status__in=[
                Recommendation.Status.DRAFT,
                Recommendation.Status.COMPLIANCE_APPROVED,
            ],
        )
        .exclude(pk=contribution.pk)
        .distinct()
        .order_by("sequence_number")
        .first()
    )

    if active_contribution:
        raise WorkflowError(
            f"Contribution "
            f"{active_contribution.sequence_number} "
            f"still has an active purchase plan. "
            f"Record or cancel it before adding "
            f"another contribution."
        )

    recommendation, decision, created = (
        generate_draft_recommendation(
            contribution
        )
    )

    return contribution, recommendation, decision

@transaction.atomic
def update_contribution_processed(contribution):
    has_pending_purchases = (
        contribution.recommendations
        .filter(
            action=Recommendation.Action.BUY
        )
        .exclude(
            status__in=[
                Recommendation.Status.EXECUTED,
                Recommendation.Status.CANCELLED,
            ]
        )
        .exists()
    )

    contribution.processed = (
        not has_pending_purchases
    )
    contribution.save(
        update_fields=["processed"]
    )

    return contribution.processed


@transaction.atomic
def execute_recommendation(
    recommendation,
    trade_date,
    price_per_share,
    shares=None,
    fees=Decimal("0.00"),
    broker_reference="",
):
    recommendation = (
        Recommendation.objects
        .select_for_update()
        .select_related(
            "contribution",
            "etf",
        )
        .get(pk=recommendation.pk)
    )

    if TradeExecution.objects.filter(
        recommendation=recommendation
    ).exists():
        raise WorkflowError(
            "This recommendation already has an execution."
        )

    if recommendation.status not in [
        Recommendation.Status.DRAFT,
        Recommendation.Status.COMPLIANCE_APPROVED,
    ]:
        raise WorkflowError(
            "Only draft or compliance-approved "
            "recommendations can be executed."
        )

    if recommendation.action != Recommendation.Action.BUY:
        raise WorkflowError(
            "The recommendation does not authorize "
            "a purchase."
        )

    if not isinstance(trade_date, date):
        raise WorkflowError(
            "A valid trade date is required."
        )

    try:
        price_per_share = Decimal(price_per_share)
        fees = Decimal(fees).quantize(MONEY)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise WorkflowError(
            "Enter valid price and fee amounts."
        ) from error

    if shares is None:
        shares = recommendation.shares

    if trade_date < recommendation.contribution.date:
        raise WorkflowError(
            "Trade date cannot precede the "
            "contribution date."
        )

    if shares <= 0:
        raise WorkflowError(
            "Shares must be greater than zero."
        )

    if shares > recommendation.shares:
        raise WorkflowError(
            f"Actual shares cannot exceed the recommended "
            f"{recommendation.shares} shares."
        )

    if price_per_share <= 0:
        raise WorkflowError(
            "Price must be greater than zero."
        )

    if fees < 0:
        raise WorkflowError(
            "Fees cannot be negative."
        )

    total_cost = (
        (price_per_share * shares) + fees
    ).quantize(MONEY)

    available_cash = (
        CashTransaction.objects.filter(
            date__lte=trade_date
        ).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )

    if total_cost > available_cash:
        raise WorkflowError(
            f"Insufficient cash. Cost is "
            f"${total_cost:.2f}; available cash is "
            f"${available_cash:.2f}."
        )

    cash_transaction = CashTransaction.objects.create(
        date=trade_date,
        transaction_type=(
            CashTransaction.TransactionType.PURCHASE
        ),
        amount=-total_cost,
        description=(
            f"Purchased {shares} "
            f"{recommendation.etf.ticker} "
            f"at ${price_per_share}"
        ),
    )

    execution = TradeExecution.objects.create(
        recommendation=recommendation,
        trade_date=trade_date,
        etf=recommendation.etf,
        shares=shares,
        price_per_share=price_per_share,
        fees=fees,
        total_cost=total_cost,
        cash_transaction=cash_transaction,
        broker_reference=broker_reference,
    )

    eligible_date = trade_date + timedelta(days=30)

    holding_lot = HoldingLot.objects.create(
        execution=execution,
        etf=recommendation.etf,
        purchase_date=trade_date,
        shares_acquired=shares,
        shares_remaining=shares,
        price_per_share=price_per_share,
        total_cost=total_cost,
        compliance_eligible_date=eligible_date,
    )

    recommendation.status = (
        Recommendation.Status.EXECUTED
    )
    recommendation.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    update_contribution_processed(
        recommendation.contribution
    )

    remaining_cash = (
        available_cash - total_cost
    ).quantize(MONEY)

    return execution, holding_lot, remaining_cash


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
