from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from portfolio.models import CashTransaction
from portfolio.workflows import (
    WorkflowError,
    create_contribution,
)


class Command(BaseCommand):
    help = (
        "Add a contribution to the vaporFUND "
        "shared cash balance"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="contribution_date",
            default=date.today().isoformat(),
            help="Contribution date in YYYY-MM-DD format",
        )
        parser.add_argument(
            "--amount",
            default="100.00",
            help="Contribution amount",
        )

    def handle(self, *args, **options):
        try:
            contribution_date = date.fromisoformat(
                options["contribution_date"]
            )

            amount = Decimal(
                options["amount"]
            ).quantize(
                Decimal("0.01")
            )
        except (ValueError, InvalidOperation) as error:
            raise CommandError(str(error)) from error

        try:
            contribution = create_contribution(
                contribution_date=contribution_date,
                amount=amount,
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error

        shared_cash = (
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0.00")
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Added contribution "
                f"{contribution.sequence_number}: "
                f"${contribution.amount:.2f} "
                f"on {contribution.date}"
            )
        )
        self.stdout.write(
            f"Shared cash balance: ${shared_cash:.2f}"
        )
        self.stdout.write(
            "Ready for target-allocation analysis."
        )
