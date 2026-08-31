from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR

from django.db.models import Sum

from .models import (
    CashTransaction,
    ETF,
    HoldingLot,
    PriceHistory,
)


MONEY = Decimal("0.01")
PERCENT = Decimal("0.01")


class AllocationError(Exception):
    pass


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


def calculate_allocation(as_of_date):
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
