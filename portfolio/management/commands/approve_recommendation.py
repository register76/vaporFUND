from django.core.management.base import BaseCommand, CommandError

from portfolio.workflows import (
    WorkflowError,
    approve_recommendation,
)


class Command(BaseCommand):
    help = (
        "Mark a vaporFUND buy recommendation "
        "as compliance-approved"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--contribution",
            type=int,
            required=True,
            help="Contribution sequence number",
        )

    def handle(self, *args, **options):
        sequence_number = options["contribution"]

        try:
            recommendation = approve_recommendation(
                sequence_number=sequence_number
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error

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
