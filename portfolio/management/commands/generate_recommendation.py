from django.core.management.base import BaseCommand, CommandError

from portfolio.models import Contribution
from portfolio.workflows import (
    WorkflowError,
    generate_draft_recommendation,
)


class Command(BaseCommand):
    help = (
        "Generate a target-allocation recommendation "
        "for an unprocessed contribution"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--contribution",
            type=int,
            help=(
                "Contribution sequence number; defaults to "
                "the earliest unprocessed contribution"
            ),
        )

    def handle(self, *args, **options):
        sequence_number = options["contribution"]

        contributions = Contribution.objects.filter(
            processed=False
        )

        if sequence_number is not None:
            contributions = contributions.filter(
                sequence_number=sequence_number
            )

        contribution = (
            contributions
            .order_by("sequence_number")
            .first()
        )

        if not contribution:
            raise CommandError(
                "No matching unprocessed contribution exists."
            )

        try:
            recommendation, decision, created = (
                generate_draft_recommendation(
                    contribution
                )
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error

        selected = decision.selected
        verb = "Created" if created else "Updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} recommendation for contribution "
                f"{contribution.sequence_number}"
            )
        )
        self.stdout.write("")
        self.stdout.write(
            f"Portfolio value: "
            f"${decision.portfolio_value:.2f}"
        )
        self.stdout.write(
            f"Holding value: "
            f"${decision.holding_value:.2f}"
        )
        self.stdout.write(
            f"Shared cash: "
            f"${decision.available_cash:.2f}"
        )
        self.stdout.write("")
        self.stdout.write("Current target allocation:")

        for row in decision.rows:
            self.stdout.write(
                f"  {row.etf.ticker:<5} "
                f"{row.actual_percent:>6.2f}% / "
                f"{row.target_percent:>2}% target  "
                f"shortfall ${row.shortfall:>9.2f}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"Selected ETF: {selected.etf.ticker}"
        )
        self.stdout.write(
            f"Reference price: "
            f"${selected.reference_price:.2f} "
            f"as of {selected.price_date}"
        )
        self.stdout.write(
            f"Action: {decision.action}"
        )
        self.stdout.write(
            f"Shares: {decision.shares}"
        )
        self.stdout.write(
            f"Estimated cost: "
            f"${decision.estimated_cost:.2f}"
        )
        self.stdout.write(
            f"Estimated remaining cash: "
            f"${decision.remaining_cash:.2f}"
        )
        self.stdout.write(
            f"Reason: {decision.reason}"
        )
        self.stdout.write(
            self.style.WARNING(
                "Draft recommendation only. Compliance "
                "approval and manual Merrill execution "
                "are required."
            )
        )
