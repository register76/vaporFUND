from django.core.management.base import BaseCommand, CommandError

from portfolio.models import Contribution
from portfolio.workflows import (
    WorkflowError,
    generate_draft_recommendations,
)


class Command(BaseCommand):
    help = (
        "Generate a target-allocation purchase plan "
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
            recommendations, plan, created = (
                generate_draft_recommendations(
                    contribution
                )
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error

        initial_decision = plan.decisions[0]
        verb = "Created" if created else "Updated"

        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} recommendation plan for "
                f"contribution "
                f"{contribution.sequence_number}"
            )
        )
        self.stdout.write("")
        self.stdout.write(
            f"Portfolio value: "
            f"${initial_decision.portfolio_value:.2f}"
        )
        self.stdout.write(
            f"Holding value: "
            f"${initial_decision.holding_value:.2f}"
        )
        self.stdout.write(
            f"Shared cash: "
            f"${initial_decision.available_cash:.2f}"
        )
        self.stdout.write("")
        self.stdout.write("Initial target allocation:")

        for row in initial_decision.rows:
            self.stdout.write(
                f"  {row.etf.ticker:<5} "
                f"{row.actual_percent:>6.2f}% / "
                f"{row.target_percent:>2}% target  "
                f"shortfall ${row.shortfall:>9.2f}"
            )

        self.stdout.write("")
        self.stdout.write("Purchase plan:")

        total_estimated_cost = sum(
            (
                decision.estimated_cost
                for decision in plan.decisions
            ),
            start=0,
        )

        for recommendation, decision in zip(
            recommendations,
            plan.decisions,
        ):
            if decision.action == "BUY":
                self.stdout.write(
                    f"  Purchase "
                    f"{recommendation.plan_order}: "
                    f"BUY {decision.shares} "
                    f"{decision.selected.etf.ticker} "
                    f"at approximately "
                    f"${decision.selected.reference_price:.2f} "
                    f"${decision.estimated_cost:.2f}"
                )
            else:
                self.stdout.write(
                    f"  Purchase "
                    f"{recommendation.plan_order}: "
                    f"HOLD CASH"
                )

            self.stdout.write(
                f"    {decision.reason}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"Total estimated purchases: "
            f"${total_estimated_cost:.2f}"
        )
        self.stdout.write(
            f"Estimated remaining cash: "
            f"${plan.remaining_cash:.2f}"
        )
        self.stdout.write(
            self.style.WARNING(
                "Draft purchase plan only. Execute purchases "
                "manually at Merrill, then record each actual "
                "execution in vaporFUND."
            )
        )
