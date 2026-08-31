from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from portfolio.models import CashTransaction, Contribution


class Command(BaseCommand):
    help = "Add a contribution to the vaporFUND shared cash balance"

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

    @transaction.atomic
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

        if amount <= 0:
            raise CommandError(
                "Contribution amount must be positive."
            )

        if Contribution.objects.filter(
            date=contribution_date
        ).exists():
            raise CommandError(
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

        shared_cash = (
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"]
            or Decimal("0.00")
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Added contribution {sequence_number}: "
                f"${amount:.2f} on {contribution_date}"
            )
        )
        self.stdout.write(
            f"Shared cash balance: ${shared_cash:.2f}"
        )
        self.stdout.write(
            "Ready for target-allocation analysis."
        )
