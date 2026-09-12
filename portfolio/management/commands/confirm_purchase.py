from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from portfolio.models import Recommendation
from portfolio.workflows import (
    WorkflowError,
    execute_recommendation,
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
            "--plan-order",
            type=int,
            default=1,
            help="Purchase-plan order number; defaults to 1",
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

    def handle(self, *args, **options):
        sequence_number = options["contribution"]
        plan_order = options["plan_order"]

        recommendation = (
            Recommendation.objects
            .select_related(
                "contribution",
                "etf",
            )
            .filter(
                contribution__sequence_number=sequence_number,
                plan_order=plan_order,
            )
            .first()
        )

        if not recommendation:
            raise CommandError(
                f"No recommendation exists for contribution "
                f"{sequence_number}, plan order {plan_order}."
            )

        try:
            trade_date = date.fromisoformat(
                options["trade_date"]
            )
            price = Decimal(options["price"])
            fees = Decimal(options["fees"]).quantize(
                Decimal("0.01")
            )
        except (
            ValueError,
            InvalidOperation,
            TypeError,
        ) as error:
            raise CommandError(str(error)) from error

        try:
            execution, holding_lot, remaining_cash = (
                execute_recommendation(
                    recommendation=recommendation,
                    trade_date=trade_date,
                    price_per_share=price,
                    shares=options["shares"],
                    fees=fees,
                    broker_reference=options[
                        "broker_reference"
                    ],
                )
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Confirmed purchase: {execution.shares} "
                f"{execution.etf.ticker} "
                f"at ${execution.price_per_share:.2f}"
            )
        )
        self.stdout.write(
            f"Trade date: {execution.trade_date}"
        )
        self.stdout.write(
            f"Total cost: ${execution.total_cost:.2f}"
        )
        self.stdout.write(
            f"Remaining shared cash: "
            f"${remaining_cash:.2f}"
        )
        self.stdout.write(
            f"30-day system eligibility date: "
            f"{holding_lot.compliance_eligible_date}"
        )
        self.stdout.write(
            self.style.WARNING(
                "System eligibility does not replace employer "
                "compliance approval for a future sale."
            )
        )
