from datetime import date

from decimal import Decimal

from django.contrib import messages

from django.views.decorators.http import (
    require_GET,
    require_http_methods,
)

from django.shortcuts import (
    get_list_or_404,
    get_object_or_404,
    redirect,
    render,
)

from django.db.models import Sum

from .forms import (
    ContributionForm,
    PurchaseExecutionForm,
)

from .models import (
    Account,
    AccountTarget,
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
    resolve_account,
)

from .workflows import (
    WorkflowError,
    add_contribution_and_recommend,
    execute_recommendation,
)

def dashboard(request):

    active_accounts = get_list_or_404(
        Account.objects.order_by("name"),
        is_active=True,
    )

    selected_account_id = request.GET.get("account")

    if selected_account_id:
        account = get_object_or_404(
            Account,
            pk=selected_account_id,
            is_active=True,
        )
    else:
        account = active_accounts[0]

    if request.method == "POST":
        contribution_form = ContributionForm(
            request.POST
        )

        if contribution_form.is_valid():
            try:
                contribution, recommendation, decision = (
                    add_contribution_and_recommend(
                        account=account,
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
                recommendations = list(
                    contribution.recommendations
                    .select_related("etf")
                    .order_by("plan_order")
                )

                buy_recommendations = [
                    item
                    for item in recommendations
                    if item.action
                    == Recommendation.Action.BUY
                ]

                if not buy_recommendations:
                    result = (
                        f"Contribution "
                        f"{contribution.sequence_number} added. "
                        f"The recommendation is to hold cash."
                    )
                elif len(buy_recommendations) == 1:
                    item = buy_recommendations[0]

                    result = (
                        f"Contribution "
                        f"{contribution.sequence_number} added. "
                        f"Draft purchase plan: buy "
                        f"{item.shares} {item.etf.ticker}."
                    )
                else:
                    purchase_summary = ", then ".join(
                        f"buy {item.shares} "
                        f"{item.etf.ticker}"
                        for item in buy_recommendations
                    )

                    result = (
                        f"Contribution "
                        f"{contribution.sequence_number} added. "
                        f"Draft purchase plan: "
                        f"{purchase_summary}."
                    )

                messages.success(
                    request,
                    result,
                )

                return redirect(
                    f"{request.path}?account={account.pk}"
                )

    else:
        contribution_form = ContributionForm()

    as_of_date = date.today()

    cash_balance = (
        CashTransaction.objects.filter(
            account=account,
            date__lte=as_of_date
        ).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )

    holding_totals = list(
        HoldingLot.objects.filter(
            execution__recommendation__contribution__account=account,
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
                as_of_date,
                account=account,
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

    latest_plan_contribution = (
        Contribution.objects
        .filter(
            account=account,
            recommendations__isnull=False,
        )
        .distinct()
        .order_by("-sequence_number")
        .first()
    )

    latest_recommendations = []
    latest_plan_total = Decimal("0.00")
    latest_plan_remaining_cash = Decimal("0.00")

    if latest_plan_contribution:
        latest_recommendations = list(
            Recommendation.objects
            .filter(
                contribution=latest_plan_contribution
            )
            .select_related(
                "contribution",
                "etf",
            )
            .order_by("plan_order")
        )

        latest_plan_total = sum(
            (
                recommendation.estimated_cost
                for recommendation
                in latest_recommendations
            ),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))

        final_recommendation = (
            latest_recommendations[-1]
        )

        latest_plan_remaining_cash = (
            final_recommendation.available_cash
            - final_recommendation.estimated_cost
        ).quantize(Decimal("0.01"))

    latest_recommendation = (
        latest_recommendations[0]
        if latest_recommendations
        else None
    )

    contributions = (
        Contribution.objects
        .filter(account=account)
        .order_by("-sequence_number")[:10]
    )

    configured_targets = list(
        AccountTarget.objects.filter(
            account=account,
            target_percent__gt=0,
            etf__enabled=True,
        )
        .select_related("etf")
        .order_by("etf__ticker")
    )

    stock_target_percent = sum(
        target.target_percent
        for target in configured_targets
        if target.etf.asset_class == ETF.AssetClass.STOCK
    )

    bond_target_percent = sum(
        target.target_percent
        for target in configured_targets
        if target.etf.asset_class == ETF.AssetClass.BOND
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
        "stock_target_percent": stock_target_percent,
        "bond_target_percent": bond_target_percent,
        "cash_percent": cash_percent,
        "decision": decision,
        "allocation_rows": allocation_rows,
        "allocation_error": allocation_error,
        "next_priority": next_priority,
        "latest_recommendation": latest_recommendation,
        "latest_plan_contribution": latest_plan_contribution,
        "latest_recommendations": latest_recommendations,
        "latest_plan_total": latest_plan_total,
        "latest_plan_remaining_cash": (
            latest_plan_remaining_cash
        ),
        "contributions": contributions,
        "holdings": holdings,
        "configured_targets": configured_targets,
        "contribution_form": contribution_form,
        "account": account,
        "active_accounts": active_accounts,
    }

    return render(
        request,
        "portfolio/dashboard.html",
        context,
    )

@require_GET
def holding_detail(
    request,
    ticker,
):
    active_accounts = get_list_or_404(
        Account.objects.order_by("name"),
        is_active=True,
    )

    selected_account_id = request.GET.get("account")

    if selected_account_id:
        account = get_object_or_404(
            Account,
            pk=selected_account_id,
            is_active=True,
        )
    else:
        account = active_accounts[0]

    etf = get_object_or_404(
        ETF,
        ticker__iexact=ticker,
    )

    as_of_date = date.today()

    lots = (
        HoldingLot.objects
        .filter(
            execution__recommendation__contribution__account=account,
            etf=etf,
            shares_remaining__gt=0,
        )
        .select_related(
            "execution",
            "execution__recommendation",
            "execution__recommendation__contribution",
        )
        .order_by(
            "-purchase_date",
            "-created_at",
        )
    )

    holding_totals = lots.aggregate(
        total_shares=Sum("shares_remaining"),
        total_cost=Sum("total_cost"),
    )

    total_shares = (
        holding_totals["total_shares"]
        or 0
    )

    total_cost = (
        holding_totals["total_cost"]
        or Decimal("0.00")
    )

    if total_shares:
        average_cost_basis = (
            total_cost
            / Decimal(total_shares)
        ).quantize(
            Decimal("0.01")
        )
    else:
        average_cost_basis = Decimal("0.00")

    try:
        price = latest_price(
            etf,
            as_of_date,
        )
        current_price = price.close
        price_date = price.date
    except AllocationError:
        current_price = Decimal("0.00")
        price_date = None

    current_value = (
        current_price
        * Decimal(total_shares)
    ).quantize(
        Decimal("0.01")
    )

    account_holding_totals = (
        HoldingLot.objects
        .filter(
            execution__recommendation__contribution__account=account,
            shares_remaining__gt=0,
        )
        .values("etf_id")
        .annotate(
            total_shares=Sum("shares_remaining")
        )
    )

    total_holding_value = Decimal("0.00")

    for holding in account_holding_totals:
        holding_etf = ETF.objects.get(
            pk=holding["etf_id"]
        )

        try:
            holding_price = latest_price(
                holding_etf,
                as_of_date,
            )
            holding_value = (
                holding_price.close
                * Decimal(
                    holding["total_shares"]
                )
            ).quantize(
                Decimal("0.01")
            )
        except AllocationError:
            holding_value = Decimal("0.00")

        total_holding_value += holding_value

    if total_holding_value > 0:
        current_allocation = (
            current_value
            / total_holding_value
            * Decimal("100")
        ).quantize(
            Decimal("0.01")
        )
    else:
        current_allocation = Decimal("0.00")

    target = (
        AccountTarget.objects
        .filter(
            account=account,
            etf=etf,
        )
        .first()
    )

    target_allocation = Decimal(
        target.target_percent
        if target
        else 0
    )

    allocation_drift = (
        current_allocation
        - target_allocation
    ).quantize(
        Decimal("0.01")
    )

    context = {
        "account": account,
        "active_accounts": active_accounts,
        "as_of_date": as_of_date,
        "etf": etf,
        "lots": lots,
        "total_shares": total_shares,
        "total_cost": total_cost,
        "average_cost_basis": average_cost_basis,
        "current_price": current_price,
        "price_date": price_date,
        "current_value": current_value,
        "current_allocation": current_allocation,
        "target_allocation": target_allocation,
        "allocation_drift": allocation_drift,
    }

    return render(
        request,
        "portfolio/holding_detail.html",
        context,
    )


@require_GET
def recommendation_review(
    request,
    sequence_number,
):
    recommendations = get_list_or_404(
        Recommendation.objects
        .select_related(
            "contribution",
            "etf",
        )
        .order_by("plan_order"),
        contribution__sequence_number=sequence_number,
    )

    contribution = recommendations[0].contribution

    recommendation_rows = []

    for recommendation in recommendations:
        estimated_remaining_cash = (
            recommendation.available_cash
            - recommendation.estimated_cost
        ).quantize(
            Decimal("0.01")
        )

        recommendation_rows.append(
            {
                "recommendation": recommendation,
                "estimated_remaining_cash": (
                    estimated_remaining_cash
                ),
            }
        )

    estimated_plan_total = sum(
        (
            recommendation.estimated_cost
            for recommendation in recommendations
        ),
        Decimal("0.00"),
    ).quantize(
        Decimal("0.01")
    )

    context = {
        "contribution": contribution,
        "recommendations": recommendations,
        "recommendation_rows": recommendation_rows,
        "estimated_plan_total": estimated_plan_total,
        "estimated_remaining_cash": (
            recommendation_rows[-1][
                "estimated_remaining_cash"
            ]
        ),
    }

    return render(
        request,
        "portfolio/recommendation_review.html",
        context,
    )

@require_http_methods(["GET", "POST"])
def recommendation_execute(
    request,
    sequence_number,
    plan_order,
):
    recommendation = get_object_or_404(
        Recommendation.objects.select_related(
            "contribution",
            "etf",
        ),
        contribution__sequence_number=sequence_number,
        plan_order=plan_order,
    )

    if request.method == "POST":
        execution_form = PurchaseExecutionForm(
            request.POST,
            recommendation=recommendation,
        )

        if execution_form.is_valid():
            try:
                execution, holding_lot, remaining_cash = (
                    execute_recommendation(
                        recommendation=recommendation,
                        trade_date=(
                            execution_form.cleaned_data[
                                "trade_date"
                            ]
                        ),
                        price_per_share=(
                            execution_form.cleaned_data[
                                "price_per_share"
                            ]
                        ),
                        shares=(
                            execution_form.cleaned_data[
                                "shares"
                            ]
                        ),
                        fees=(
                            execution_form.cleaned_data[
                                "fees"
                            ]
                        ),
                        broker_reference=(
                            execution_form.cleaned_data[
                                "broker_reference"
                            ]
                        ),
                    )
                )
            except WorkflowError as error:
                execution_form.add_error(
                    None,
                    str(error),
                )
            else:
                messages.success(
                    request,
                    (
                        f"Recorded purchase "
                        f"{recommendation.plan_order}: "
                        f"{execution.shares} "
                        f"{execution.etf.ticker} at "
                        f"${execution.price_per_share:.2f}. "
                        f"Remaining cash: "
                        f"${remaining_cash:.2f}."
                    ),
                )

                return redirect(
                    "portfolio:recommendation_review",
                    sequence_number=sequence_number,
                )
    else:
        execution_form = PurchaseExecutionForm(
            recommendation=recommendation,
        )

    context = {
        "recommendation": recommendation,
        "contribution": recommendation.contribution,
        "execution_form": execution_form,
    }

    return render(
        request,
        "portfolio/recommendation_execute.html",
        context,
    )

@require_http_methods(["GET", "POST"])
def recommendation_approve(
    request,
    sequence_number,
):
    get_list_or_404(
        Recommendation,
        contribution__sequence_number=sequence_number,
    )

    messages.info(
        request,
        (
            "Separate approval is no longer required "
            "for ETF purchases."
        ),
    )

    return redirect(
        "portfolio:recommendation_review",
        sequence_number=sequence_number,
    )
