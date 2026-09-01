from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.views.decorators.http import require_GET

from django.db.models import Sum
from django.shortcuts import (
    get_object_or_404,
    redirect, 
    render,
)

from .forms import ContributionForm

from .models import (
    CashTransaction,
    Contribution,
    ETF,
    HoldingLot,
    Recommendation,
)
from .services import (
    AllocationError,
    calculate_allocation,
    latest_price,
)
from .workflows import (
    WorkflowError,
    add_contribution_and_recommend,
)

def dashboard(request):
    if request.method == "POST":
        contribution_form = ContributionForm(
            request.POST
        )

        if contribution_form.is_valid():
            try:
                contribution, recommendation, decision = (
                    add_contribution_and_recommend(
                        contribution_date=(
                            contribution_form.cleaned_data[
                                "contribution_date"
                            ]
                        ),
                        amount=(
                            contribution_form.cleaned_data[
                                "amount"
                            ]
                        ),
                    )
                )
            except WorkflowError as error:
                messages.error(
                    request,
                    str(error),
                )
            else:
                if recommendation.action == "BUY":
                    result = (
                        f"Contribution "
                        f"{contribution.sequence_number} added. "
                        f"Draft recommendation: buy "
                        f"{recommendation.shares} "
                        f"{recommendation.etf.ticker}."
                    )
                else:
                    result = (
                        f"Contribution "
                        f"{contribution.sequence_number} added. "
                        f"The recommendation is to hold cash."
                    )

                messages.success(
                    request,
                    result,
                )

                return redirect(request.path)
    else:
        contribution_form = ContributionForm()

    as_of_date = date.today()

    cash_balance = (
        CashTransaction.objects.filter(
            date__lte=as_of_date
        ).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )

    holding_totals = list(
        HoldingLot.objects.filter(
            purchase_date__lte=as_of_date,
            shares_remaining__gt=0,
        )
        .values(
            "etf_id",
            "etf__ticker",
            "etf__name",
        )
        .annotate(
            total_shares=Sum("shares_remaining"),
            total_cost=Sum("total_cost"),
        )
        .order_by("etf__ticker")
    )

    holdings = []
    total_holding_value = Decimal("0.00")

    for holding in holding_totals:
        etf = ETF.objects.get(
            id=holding["etf_id"]
        )

        try:
            price = latest_price(
                etf,
                as_of_date,
            )
            reference_price = price.close
            price_date = price.date
        except AllocationError:
            reference_price = Decimal("0.00")
            price_date = None

        market_value = (
            reference_price
            * holding["total_shares"]
        ).quantize(
            Decimal("0.01")
        )

        total_holding_value += market_value

        holdings.append(
            {
                "ticker": etf.ticker,
                "name": etf.name,
                "shares": holding["total_shares"],
                "total_cost": holding["total_cost"],
                "reference_price": reference_price,
                "price_date": price_date,
                "market_value": market_value,
            }
        )

    total_holding_value = (
        total_holding_value.quantize(
            Decimal("0.01")
        )
    )

    portfolio_value = (
        total_holding_value + cash_balance
    ).quantize(
        Decimal("0.01")
    )

    decision = None
    allocation_error = ""

    if portfolio_value > 0:
        try:
            decision = calculate_allocation(
                as_of_date
            )
        except AllocationError as error:
            allocation_error = str(error)

    if decision:
        allocation_rows = decision.rows
        next_priority = decision.selected

        stock_value = sum(
            (
                row.current_value
                for row in decision.rows
                if row.etf.asset_class
                == ETF.AssetClass.STOCK
            ),
            Decimal("0.00"),
        )

        bond_value = sum(
            (
                row.current_value
                for row in decision.rows
                if row.etf.asset_class
                == ETF.AssetClass.BOND
            ),
            Decimal("0.00"),
        )
    else:
        allocation_rows = []
        next_priority = None
        stock_value = Decimal("0.00")
        bond_value = Decimal("0.00")

    if portfolio_value > 0:
        stock_percent = (
            stock_value
            / portfolio_value
            * Decimal("100")
        )
        bond_percent = (
            bond_value
            / portfolio_value
            * Decimal("100")
        )
        cash_percent = (
            cash_balance
            / portfolio_value
            * Decimal("100")
        )
    else:
        stock_percent = Decimal("0.00")
        bond_percent = Decimal("0.00")
        cash_percent = Decimal("0.00")

    latest_recommendation = (
        Recommendation.objects
        .select_related(
            "contribution",
            "etf",
        )
        .order_by("-created_at")
        .first()
    )

    contributions = (
        Contribution.objects
        .order_by("-sequence_number")[:10]
    )

    configured_targets = (
        ETF.objects.filter(
            enabled=True,
            target_percent__gt=0,
        ).order_by("ticker")
    )

    context = {
        "as_of_date": as_of_date,
        "cash_balance": cash_balance,
        "total_holding_value": total_holding_value,
        "portfolio_value": portfolio_value,
        "stock_value": stock_value,
        "bond_value": bond_value,
        "stock_percent": stock_percent,
        "bond_percent": bond_percent,
        "cash_percent": cash_percent,
        "decision": decision,
        "allocation_rows": allocation_rows,
        "allocation_error": allocation_error,
        "next_priority": next_priority,
        "latest_recommendation": latest_recommendation,
        "contributions": contributions,
        "holdings": holdings,
        "configured_targets": configured_targets,
        "contribution_form": contribution_form,
    }

    return render(
        request,
        "portfolio/dashboard.html",
        context,
    )

@require_GET
def recommendation_review(
    request,
    sequence_number,
):
    recommendation = get_object_or_404(
        Recommendation.objects.select_related(
            "contribution",
            "etf",
        ),
        contribution__sequence_number=sequence_number,
    )

    estimated_remaining_cash = (
        recommendation.available_cash
        - recommendation.estimated_cost
    ).quantize(
        Decimal("0.01")
    )

    context = {
        "recommendation": recommendation,
        "contribution": recommendation.contribution,
        "estimated_remaining_cash": (
            estimated_remaining_cash
        ),
    }

    return render(
        request,
        "portfolio/recommendation_review.html",
        context,
    )
