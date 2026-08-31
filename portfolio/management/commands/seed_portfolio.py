from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portfolio.models import ETF


class Command(BaseCommand):
    help = "Create or update the vaporFUND target allocation"

    TARGETS = (
        {
            "ticker": "SCHB",
            "name": "Schwab U.S. Broad Market ETF",
            "asset_class": ETF.AssetClass.STOCK,
            "target_percent": 40,
        },
        {
            "ticker": "XMMO",
            "name": "Invesco S&P MidCap Momentum ETF",
            "asset_class": ETF.AssetClass.STOCK,
            "target_percent": 5,
        },
        {
            "ticker": "AVUV",
            "name": "Avantis U.S. Small Cap Value ETF",
            "asset_class": ETF.AssetClass.STOCK,
            "target_percent": 10,
        },
        {
            "ticker": "VEA",
            "name": "Vanguard FTSE Developed Markets ETF",
            "asset_class": ETF.AssetClass.STOCK,
            "target_percent": 15,
        },
        {
            "ticker": "VWO",
            "name": "Vanguard FTSE Emerging Markets ETF",
            "asset_class": ETF.AssetClass.STOCK,
            "target_percent": 5,
        },
        {
            "ticker": "VTEB",
            "name": "Vanguard Tax-Exempt Bond ETF",
            "asset_class": ETF.AssetClass.BOND,
            "target_percent": 25,
        },
    )

    @transaction.atomic
    def handle(self, *args, **options):
        total_target = sum(
            item["target_percent"]
            for item in self.TARGETS
        )

        if total_target != 100:
            raise CommandError(
                f"Configured targets total {total_target}%, not 100%."
            )

        configured_tickers = {
            item["ticker"]
            for item in self.TARGETS
        }

        ETF.objects.exclude(
            ticker__in=configured_tickers
        ).update(
            enabled=False,
            target_percent=0,
        )

        for item in self.TARGETS:
            etf, created = ETF.objects.update_or_create(
                ticker=item["ticker"],
                defaults={
                    "name": item["name"],
                    "asset_class": item["asset_class"],
                    "target_percent": item["target_percent"],
                    "enabled": True,
                },
            )

            verb = "Created" if created else "Updated"

            self.stdout.write(
                f"{verb}: {etf.ticker} "
                f"{etf.target_percent}% "
                f"{etf.get_asset_class_display()}"
            )

        database_total = sum(
            ETF.objects.filter(
                enabled=True
            ).values_list(
                "target_percent",
                flat=True,
            )
        )

        if database_total != 100:
            raise CommandError(
                f"Enabled database targets total "
                f"{database_total}%, not 100%."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "vaporFUND target allocation configured: 100%"
            )
        )
