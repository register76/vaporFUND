from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portfolio.models import Recommendation


class Command(BaseCommand):
    help = "Mark a vaporFUND buy recommendation as compliance-approved"

    def add_arguments(self, parser):
        parser.add_argument(
            "--contribution",
            type=int,
            required=True,
            help="Contribution sequence number",
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

        if recommendation.status != (
            Recommendation.Status.DRAFT
        ):
            raise CommandError(
                "Only a draft recommendation can be approved."
            )

        if recommendation.action != (
            Recommendation.Action.BUY
        ):
            raise CommandError(
                "A hold-cash recommendation has no purchase "
                "to approve."
            )

        if recommendation.shares <= 0:
            raise CommandError(
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

        self.stdout.write(
            self.style.SUCCESS(
                f"Compliance-approved contribution "
                f"{sequence_number}: buy "
                f"{recommendation.shares} "
                f"{recommendation.etf.ticker}"
            )
        )
        self.stdout.write(
            "Approval records authorization only; "
            "no brokerage order has been placed."
        )
