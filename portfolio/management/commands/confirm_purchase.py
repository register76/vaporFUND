from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from portfolio.models import (
    CashTransaction,
    HoldingLot,
    Recommendation,
    TradeExecution,
)


class Command(BaseCommand):
    help = "Record an actual manually executed Merrill purchase"

    def add_arguments(self, parser):
        parser.add_argument(
            "--contribution",
            type=int,
            required=True,
            help="Contribution sequence number",
        )
        parser.add_argument(
            "--price",
            required=True,
            help="Actual Merrill fill price per share",
        )
        parser.add_argument(
            "--shares",
            type=int,
            help=(
                "Actual shares purchased; defaults to "
                "the recommended quantity"
            ),
        )
        parser.add_argument(
            "--date",
            dest="trade_date",
            default=date.today().isoformat(),
            help="Trade date in YYYY-MM-DD format",
        )
        parser.add_argument(
            "--fees",
            default="0.00",
            help="Total transaction fees",
        )
        parser.add_argument(
            "--broker-reference",
            default="",
            help="Optional Merrill order or confirmation reference",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        sequence_number = options["contribution"]

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
            raise CommandError(
                f"No recommendation exists for contribution "
                f"{sequence_number}."
            )

        if TradeExecution.objects.filter(
            recommendation=recommendation
        ).exists():
            raise CommandError(
                "This recommendation already has an execution."
            )

        if recommendation.status != (
            Recommendation.Status.COMPLIANCE_APPROVED
        ):
            raise CommandError(
                "The recommendation has not been "
                "compliance-approved."
            )

        if recommendation.action != (
            Recommendation.Action.BUY
        ):
            raise CommandError(
                "The recommendation does not authorize "
                "a purchase."
            )

        try:
            trade_date = date.fromisoformat(
                options["trade_date"]
            )
            price = Decimal(
                options["price"]
            )
            fees = Decimal(
                options["fees"]
            ).quantize(
                Decimal("0.01")
            )
        except (ValueError, InvalidOperation) as error:
            raise CommandError(str(error)) from error

        shares = (
            options["shares"]
            if options["shares"] is not None
            else recommendation.shares
        )

        if trade_date < recommendation.contribution.date:
            raise CommandError(
                "Trade date cannot precede the "
                "contribution date."
            )

        if shares <= 0:
            raise CommandError(
                "Shares must be greater than zero."
            )

        if shares > recommendation.shares:
            raise CommandError(
                f"Actual shares cannot exceed the recommended "
                f"{recommendation.shares} shares."
            )

        if price <= 0:
            raise CommandError(
                "Price must be greater than zero."
            )

        if fees < 0:
            raise CommandError(
                "Fees cannot be negative."
            )

        total_cost = (
            (price * shares) + fees
        ).quantize(
            Decimal("0.01")
        )

        available_cash = (
            CashTransaction.objects.filter(
                date__lte=trade_date
            ).aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0.00")
        )

        if total_cost > available_cash:
            raise CommandError(
                f"Insufficient cash. Cost is "
                f"${total_cost:.2f}; available cash is "
                f"${available_cash:.2f}."
            )

        cash_transaction = (
            CashTransaction.objects.create(
                date=trade_date,
                transaction_type=(
                    CashTransaction
                    .TransactionType
                    .PURCHASE
                ),
                amount=-total_cost,
                description=(
                    f"Purchased {shares} "
                    f"{recommendation.etf.ticker} "
                    f"at ${price}"
                ),
            )
        )

        execution = TradeExecution.objects.create(
            recommendation=recommendation,
            trade_date=trade_date,
            etf=recommendation.etf,
            shares=shares,
            price_per_share=price,
            fees=fees,
            total_cost=total_cost,
            cash_transaction=cash_transaction,
            broker_reference=options[
                "broker_reference"
            ],
        )

        eligible_date = trade_date + timedelta(days=30)

        HoldingLot.objects.create(
            execution=execution,
            etf=recommendation.etf,
            purchase_date=trade_date,
            shares_acquired=shares,
            shares_remaining=shares,
            price_per_share=price,
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

        contribution = recommendation.contribution
        contribution.processed = True
        contribution.save(
            update_fields=["processed"]
        )

        remaining_cash = (
            available_cash - total_cost
        ).quantize(
            Decimal("0.01")
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Confirmed purchase: {shares} "
                f"{recommendation.etf.ticker} "
                f"at ${price:.2f}"
            )
        )
        self.stdout.write(
            f"Trade date: {trade_date}"
        )
        self.stdout.write(
            f"Total cost: ${total_cost:.2f}"
        )
        self.stdout.write(
            f"Remaining shared cash: "
            f"${remaining_cash:.2f}"
        )
        self.stdout.write(
            f"30-day system eligibility date: "
            f"{eligible_date}"
        )
        self.stdout.write(
            self.style.WARNING(
                "System eligibility does not replace employer "
                "compliance approval for a future sale."
            )
        )
