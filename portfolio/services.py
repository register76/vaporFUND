from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, ROUND_FLOOR

from django.db.models import Sum

from .models import (
    Account,
    CashTransaction,
    ETF,
    HoldingLot,
    PriceHistory,
)


MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")


class AllocationError(Exception):
    pass

def resolve_account(account=None):
    if account is not None:
        return account

    accounts = list(
        Account.objects.filter(
            is_active=True
        ).order_by("id")[:2]
    )

    if not accounts:
        raise AllocationError(
            "No active vaporFUND account is configured."
        )

    if len(accounts) > 1:
        raise AllocationError(
            "Multiple active accounts exist. "
            "An account must be selected."
        )

    return accounts[0]

@dataclass(frozen=True)
class AllocationRow:
    etf: ETF
    current_shares: int
    reference_price: Decimal
    price_date: date
    current_value: Decimal
    target_percent: int
    actual_percent: Decimal
    target_value: Decimal
    shortfall: Decimal


@dataclass(frozen=True)
class AllocationDecision:
    as_of_date: date
    available_cash: Decimal
    holding_value: Decimal
    portfolio_value: Decimal
    rows: tuple
    selected: AllocationRow
    action: str
    shares: int
    estimated_cost: Decimal
    remaining_cash: Decimal
    reason: str


@dataclass(frozen=True)
class AllocationPlan:
    as_of_date: date
    decisions: tuple
    remaining_cash: Decimal

def latest_price(etf, as_of_date):
    price = (
        PriceHistory.objects.filter(
            etf=etf,
            date__lte=as_of_date,
        )
        .order_by("-date")
        .first()
    )

    if not price:
        raise AllocationError(
            f"No price exists for {etf.ticker} "
            f"on or before {as_of_date}."
        )

    if price.close <= 0:
        raise AllocationError(
            f"{etf.ticker} has a nonpositive closing price "
            f"on {price.date}."
        )

    return price


def calculate_allocation(as_of_date, account=None):
    account = resolve_account(account)

    target_etfs = list(
        ETF.objects.filter(
            enabled=True,
            target_percent__gt=0,
        ).order_by("ticker")
    )

    if not target_etfs:
        raise AllocationError(
            "No enabled target ETFs are configured."
        )

    total_target = sum(
        etf.target_percent
        for etf in target_etfs
    )

    if total_target != 100:
        raise AllocationError(
            f"Enabled ETF targets total "
            f"{total_target}%, not 100%."
        )

    available_cash = (
        CashTransaction.objects.filter(
            account=account,
            date__lte=as_of_date,
        ).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    ).quantize(MONEY)

    if available_cash < 0:
        raise AllocationError(
            f"Shared cash balance is negative: "
            f"${available_cash:.2f}."
        )

    lot_totals = list(
        HoldingLot.objects.filter(
            execution__recommendation__contribution__account=account,
            purchase_date__lte=as_of_date,
            shares_remaining__gt=0,
        )
        .values("etf_id")
        .annotate(
            shares=Sum("shares_remaining")
        )
    )

    held_etfs = ETF.objects.in_bulk(
        row["etf_id"]
        for row in lot_totals
    )

    shares_by_etf = {}
    value_by_etf = {}
    holding_value = Decimal("0.00")

    for row in lot_totals:
        etf = held_etfs[row["etf_id"]]
        shares = int(row["shares"])
        price = latest_price(etf, as_of_date)

        market_value = (
            price.close * shares
        ).quantize(MONEY)

        shares_by_etf[etf.id] = shares
        value_by_etf[etf.id] = market_value
        holding_value += market_value

    holding_value = holding_value.quantize(MONEY)

    portfolio_value = (
        holding_value + available_cash
    ).quantize(MONEY)

    if portfolio_value <= 0:
        raise AllocationError(
            "Portfolio value must be greater than zero."
        )

    rows = []

    for etf in target_etfs:
        price = latest_price(etf, as_of_date)

        current_shares = shares_by_etf.get(
            etf.id,
            0,
        )

        current_value = value_by_etf.get(
            etf.id,
            Decimal("0.00"),
        )

        target_value = (
            portfolio_value
            * Decimal(etf.target_percent)
            / Decimal("100")
        ).quantize(MONEY)

        shortfall = (
            target_value - current_value
        ).quantize(MONEY)

        actual_percent = (
            current_value
            / portfolio_value
            * Decimal("100")
        ).quantize(PERCENT)

        rows.append(
            AllocationRow(
                etf=etf,
                current_shares=current_shares,
                reference_price=price.close,
                price_date=price.date,
                current_value=current_value,
                target_percent=etf.target_percent,
                actual_percent=actual_percent,
                target_value=target_value,
                shortfall=shortfall,
            )
        )

    selected = max(
        rows,
        key=lambda row: (
            row.shortfall,
            row.etf.ticker,
        ),
    )

    shares = choose_share_quantity(
        shortfall=selected.shortfall,
        share_price=selected.reference_price,
        available_cash=available_cash,
    )

    estimated_cost = (
        selected.reference_price * shares
    ).quantize(MONEY)

    remaining_cash = (
        available_cash - estimated_cost
    ).quantize(MONEY)

    if shares > 0:
        action = "BUY"
        reason = (
            f"{selected.etf.ticker} has the largest "
            f"target-dollar shortfall at "
            f"${selected.shortfall:.2f}. Its current "
            f"allocation is {selected.actual_percent:.2f}% "
            f"against a {selected.target_percent}% target. "
            f"Buying {shares} whole "
            f"share{'s' if shares != 1 else ''} most reduces "
            f"the allocation deviation."
        )
    else:
        action = "HOLD_CASH"

        if available_cash < selected.reference_price:
            reason = (
                f"{selected.etf.ticker} has the largest "
                f"target-dollar shortfall at "
                f"${selected.shortfall:.2f}, but shared cash "
                f"cannot purchase one whole share. Cash will "
                f"carry forward; no substitute ETF will be "
                f"purchased."
            )
        else:
            reason = (
                f"{selected.etf.ticker} has the largest "
                f"target-dollar shortfall at "
                f"${selected.shortfall:.2f}, but purchasing "
                f"one whole share would increase allocation "
                f"deviation. Cash will carry forward."
            )

    return AllocationDecision(
        as_of_date=as_of_date,
        available_cash=available_cash,
        holding_value=holding_value,
        portfolio_value=portfolio_value,
        rows=tuple(rows),
        selected=selected,
        action=action,
        shares=shares,
        estimated_cost=estimated_cost,
        remaining_cash=remaining_cash,
        reason=reason,
    )


def choose_share_quantity(
    shortfall,
    share_price,
    available_cash,
):
    if (
        shortfall <= 0
        or share_price <= 0
        or available_cash < share_price
    ):
        return 0

    max_affordable = int(
        available_cash // share_price
    )

    ideal_shares = (
        shortfall / share_price
    )

    floor_shares = int(
        ideal_shares.to_integral_value(
            rounding=ROUND_FLOOR
        )
    )

    candidates = {
        1,
        max(1, floor_shares),
        max(1, floor_shares + 1),
    }

    candidates = {
        min(candidate, max_affordable)
        for candidate in candidates
        if candidate > 0
    }

    best_shares = min(
        candidates,
        key=lambda candidate: (
            abs(
                shortfall
                - (
                    share_price * candidate
                )
            ),
            candidate,
        ),
    )

    before_error = abs(shortfall)
    after_error = abs(
        shortfall
        - (
            share_price * best_shares
        )
    )

    if after_error >= before_error:
        return 0

    return best_shares

def calculate_purchase_plan(as_of_date, account=None):
    account = resolve_account(account)

    decision = calculate_allocation(
        as_of_date,
        account=account,
    )
    decisions = []

    while decision.action == "BUY":
        decisions.append(decision)

        if decision.remaining_cash <= 0:
            break

        decision = simulate_completed_purchase(decision)

    if not decisions:
        decisions.append(decision)

    return AllocationPlan(
        as_of_date=as_of_date,
        decisions=tuple(decisions),
        remaining_cash=decision.remaining_cash,
    )


def simulate_completed_purchase(decision):
    rows = []

    for row in decision.rows:
        if row.etf.pk == decision.selected.etf.pk:
            current_shares = (
                row.current_shares + decision.shares
            )
            current_value = (
                row.current_value
                + decision.estimated_cost
            ).quantize(MONEY)
        else:
            current_shares = row.current_shares
            current_value = row.current_value

        actual_percent = (
            current_value
            / decision.portfolio_value
            * Decimal("100")
        ).quantize(PERCENT)

        shortfall = (
            row.target_value - current_value
        ).quantize(MONEY)

        rows.append(
            replace(
                row,
                current_shares=current_shares,
                current_value=current_value,
                actual_percent=actual_percent,
                shortfall=shortfall,
            )
        )

    selected = max(
        rows,
        key=lambda row: (
            row.shortfall,
            row.etf.ticker,
        ),
    )

    available_cash = decision.remaining_cash

    shares = choose_share_quantity(
        shortfall=selected.shortfall,
        share_price=selected.reference_price,
        available_cash=available_cash,
    )

    estimated_cost = (
        selected.reference_price * shares
    ).quantize(MONEY)

    remaining_cash = (
        available_cash - estimated_cost
    ).quantize(MONEY)

    if shares > 0:
        action = "BUY"
        reason = (
            f"After the preceding planned purchase, "
            f"{selected.etf.ticker} has the largest "
            f"target-dollar shortfall at "
            f"${selected.shortfall:.2f}. Buying {shares} "
            f"whole share{'s' if shares != 1 else ''} "
            f"most reduces the allocation deviation."
        )
    else:
        action = "HOLD_CASH"
        reason = (
            f"After the planned purchases, "
            f"{selected.etf.ticker} has the largest "
            f"target-dollar shortfall at "
            f"${selected.shortfall:.2f}, but another "
            f"whole-share purchase would not reduce the "
            f"allocation deviation or cannot be afforded. "
            f"Cash will carry forward; no substitute ETF "
            f"will be purchased."
        )

    return AllocationDecision(
        as_of_date=decision.as_of_date,
        available_cash=available_cash,
        holding_value=(
            decision.holding_value
            + decision.estimated_cost
        ).quantize(MONEY),
        portfolio_value=decision.portfolio_value,
        rows=tuple(rows),
        selected=selected,
        action=action,
        shares=shares,
        estimated_cost=estimated_cost,
        remaining_cash=remaining_cash,
        reason=reason,
    )
