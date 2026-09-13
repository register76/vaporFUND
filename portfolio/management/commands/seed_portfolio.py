from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portfolio.models import Account, AccountTarget, ETF

DEFAULT_ACCOUNT_NAME = "vaporFUND Test"

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

        account, _ = Account.objects.get_or_create(
            name=DEFAULT_ACCOUNT_NAME,
            defaults={
                "description": (
                    "Original vaporFUND account migrated "
                    "from the single-account system."
                 ),
                "is_active": True,
            },
        )

        configured_tickers = {
            item["ticker"]
            for item in self.TARGETS
        }

        AccountTarget.objects.filter(
            account=account,
        ).exclude(
            etf__ticker__in=configured_tickers,
        ).delete()

        for item in self.TARGETS:
            etf, created = ETF.objects.update_or_create(
                ticker=item["ticker"],
                defaults={
                    "name": item["name"],
                    "asset_class": item["asset_class"],
                    "target_percent": 0,
                    "enabled": True,
                },
            )

            account_target, _ = AccountTarget.objects.update_or_create(
                account=account,
                etf=etf,
                defaults={
                    "target_percent": item["target_percent"],
                },
            )

            verb = "Created" if created else "Updated"

            self.stdout.write(
                f"{verb}: {etf.ticker} "
                f"{account_target.target_percent}% "
                f"{etf.get_asset_class_display()}"
            )

        database_total = sum(
            AccountTarget.objects.filter(
                account=account,
                target_percent__gt=0,
                etf__enabled=True,
            ).values_list(
                "target_percent",
                flat=True,
            )
        )

        if database_total != 100:
            raise CommandError(
                f"Account targets for {account.name} total "
                f"{database_total}%, not 100%."
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"{account.name} target allocation configured: 100%"
            )
        )
