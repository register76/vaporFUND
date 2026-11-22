from datetime import date

from decimal import Decimal

from django.contrib import messages
from django.http import Http404

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
    AccountTargetFormSet,
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
    TradeExecution,
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

def _select_account(request):
    active_accounts = get_list_or_404(
        Account.objects.order_by("name"),
        is_active=True,
    )

    selected_account_id = request.GET.get("account")

    if selected_account_id:
        try:
            selected_account_id = int(selected_account_id)
        except ValueError as error:
            raise Http404("Invalid account.") from error

        account = get_object_or_404(
            Account,
            pk=selected_account_id,
            is_active=True,
        )
    else:
        account = active_accounts[0]

    return account, active_accounts


def _portfolio_snapshot(account, as_of_date):
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
                "etf_id": etf.pk,
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

    return holdings, total_holding_value, cash_balance, portfolio_value


def dashboard(request):
    account, active_accounts = _select_account(request)

    targets = (
        AccountTarget.objects
        .filter(account=account)
        .select_related("etf")
        .order_by("etf__ticker")
    )


    if request.method == "POST":
        post_action = request.POST.get(
            "action",
            "add_contribution",
        )

        if post_action == "update_targets":
            contribution_form = ContributionForm()

            target_formset = AccountTargetFormSet(
                request.POST,
                queryset=targets,
            )

            if target_formset.is_valid():
                target_formset.save()

                messages.success(
                    request,
                    f"Target allocation updated for {account.name}.",
                )

                return redirect(
                    f"{request.path}?account={account.pk}"
                )

        else:
            target_formset = AccountTargetFormSet(
                queryset=targets,
            )

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

        target_formset = AccountTargetFormSet(
            queryset=targets,
        )

    as_of_date = date.today()

    holdings, total_holding_value, cash_balance, portfolio_value = (
        _portfolio_snapshot(account, as_of_date)
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
        "target_formset": target_formset,
        "account": account,
        "active_accounts": active_accounts,
    }

    return render(
        request,
        "portfolio/dashboard.html",
        context,
    )

@require_http_methods(["GET", "POST"])
def account_settings(
    request,
    account_id,
):
    account = get_object_or_404(
        Account,
        pk=account_id,
        is_active=True,
    )

    targets = (
        AccountTarget.objects
        .filter(account=account)
        .select_related("etf")
        .order_by("etf__ticker")
    )

    if request.method == "POST":
        target_formset = AccountTargetFormSet(
            request.POST,
            queryset=targets,
        )

        if target_formset.is_valid():
            target_formset.save()

            messages.success(
                request,
                f"Target allocation updated for {account.name}.",
            )

            return redirect(
                "portfolio:account_settings",
                account_id=account.pk,
            )
    else:
        target_formset = AccountTargetFormSet(
            queryset=targets,
        )

    target_total = sum(
        target.target_percent
        for target in targets
    )

    context = {
        "account": account,
        "target_formset": target_formset,
        "target_total": target_total,
    }

    return render(
        request,
        "portfolio/account_settings.html",
        context,
    )

@require_GET
def holding_detail(
    request,
    ticker,
):
    account, active_accounts = _select_account(request)
    etf = get_object_or_404(ETF, ticker__iexact=ticker)
    as_of_date = date.today()

    lots = HoldingLot.objects.filter(
        execution__recommendation__contribution__account=account,
        etf=etf,
        purchase_date__lte=as_of_date,
        shares_remaining__gt=0,
    ).order_by("-purchase_date", "-created_at", "-pk")

    executions = TradeExecution.objects.filter(
        recommendation__contribution__account=account,
        etf=etf,
        trade_date__lte=as_of_date,
    ).order_by("-trade_date", "-created_at", "-pk")

    target = AccountTarget.objects.filter(account=account, etf=etf).first()
    holdings, _, _, portfolio_value = _portfolio_snapshot(account, as_of_date)
    holding = next((row for row in holdings if row["etf_id"] == etf.pk), None)

    if holding is None and target is None and not executions:
        raise Http404("Holding not found in this account.")

    total_shares = holding["shares"] if holding else 0
    total_cost = holding["total_cost"] if holding else Decimal("0.00")
    average_cost_basis = (
        (total_cost / total_shares).quantize(Decimal("0.01"))
        if total_shares else Decimal("0.00")
    )
    if holding:
        current_price = holding["reference_price"]
        price_date = holding["price_date"]
    else:
        try:
            price = latest_price(etf, as_of_date)
            current_price = price.close
            price_date = price.date
        except AllocationError:
            current_price = Decimal("0.00")
            price_date = None
    current_value = holding["market_value"] if holding else Decimal("0.00")
    current_allocation = (
        (current_value / portfolio_value * Decimal("100")).quantize(Decimal("0.01"))
        if portfolio_value > 0 else Decimal("0.00")
    )
    target_allocation = target.target_percent if target else 0

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
        "executions": executions,
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
    account_id,
    sequence_number,
):
    recommendations = get_list_or_404(
        Recommendation.objects
        .select_related(
            "contribution",
            "etf",
        )
        .order_by("plan_order"),
        contribution__account_id=account_id,
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
    account_id,
    sequence_number,
    plan_order,
):
    recommendation = get_object_or_404(
        Recommendation.objects.select_related(
            "contribution",
            "etf",
        ),
        contribution__account_id=account_id,
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
                    account_id=account_id,
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
    account_id,
    sequence_number,
):
    get_list_or_404(
        Recommendation,
        contribution__account_id=account_id,
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
        account_id=account_id,
        sequence_number=sequence_number,
    )
