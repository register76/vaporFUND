from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pandas as pd
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.core.management.base import CommandError
from django.db.models import Sum

from .models import (
    CashTransaction,
    Contribution,
    ETF, 
    PriceHistory,
)


class PortfolioConfigurationTests(TestCase):
    def run_seed(self):
        call_command(
            "seed_portfolio",
            stdout=StringIO(),
        )

    def test_seed_creates_expected_allocation(self):
        self.run_seed()

        targets = dict(
            ETF.objects.filter(
                enabled=True
            ).values_list(
                "ticker",
                "target_percent",
            )
        )

        self.assertEqual(
            targets,
            {
                "SCHB": 40,
                "XMMO": 5,
                "AVUV": 10,
                "VEA": 15,
                "VWO": 5,
                "VTEB": 25,
            },
        )

        self.assertEqual(sum(targets.values()), 100)

    def test_seed_is_idempotent(self):
        self.run_seed()
        self.run_seed()

        self.assertEqual(ETF.objects.count(), 6)

    def test_stock_and_bond_targets_are_75_25(self):
        self.run_seed()

        stock_target = sum(
            ETF.objects.filter(
                enabled=True,
                asset_class=ETF.AssetClass.STOCK,
            ).values_list(
                "target_percent",
                flat=True,
            )
        )

        bond_target = sum(
            ETF.objects.filter(
                enabled=True,
                asset_class=ETF.AssetClass.BOND,
            ).values_list(
                "target_percent",
                flat=True,
            )
        )

        self.assertEqual(stock_target, 75)
        self.assertEqual(bond_target, 25)


class PriceHistoryTests(TestCase):
    def setUp(self):
        call_command(
            "seed_portfolio",
            stdout=StringIO(),
        )

        self.schb = ETF.objects.get(ticker="SCHB")

    def test_price_date_is_unique_per_etf(self):
        PriceHistory.objects.create(
            etf=self.schb,
            date=date(2026, 8, 28),
            close=Decimal("29.70"),
            adjusted_close=Decimal("29.50"),
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PriceHistory.objects.create(
                    etf=self.schb,
                    date=date(2026, 8, 28),
                    close=Decimal("30.00"),
                    adjusted_close=Decimal("29.80"),
                )

    @patch(
        "portfolio.management.commands.import_prices."
        "yf.download"
    )
    def test_import_prices_creates_and_updates_rows(
        self,
        mock_download,
    ):
        index = pd.to_datetime(
            [
                "2026-08-27",
                "2026-08-28",
            ]
        )

        mock_download.return_value = pd.DataFrame(
            {
                "Close": [29.50, 29.70],
                "Adj Close": [29.30, 29.50],
            },
            index=index,
        )

        call_command(
            "import_prices",
            ticker=["SCHB"],
            start="2026-08-01",
            stdout=StringIO(),
        )

        self.assertEqual(
            PriceHistory.objects.filter(
                etf=self.schb
            ).count(),
            2,
        )

        latest = PriceHistory.objects.get(
            etf=self.schb,
            date=date(2026, 8, 28),
        )

        self.assertEqual(
            latest.close,
            Decimal("29.700000"),
        )
        self.assertEqual(
            latest.adjusted_close,
            Decimal("29.500000"),
        )

        mock_download.return_value = pd.DataFrame(
            {
                "Close": [29.60, 30.10],
                "Adj Close": [29.40, 29.90],
            },
            index=index,
        )

        call_command(
            "import_prices",
            ticker=["SCHB"],
            start="2026-08-01",
            stdout=StringIO(),
        )

        self.assertEqual(
            PriceHistory.objects.filter(
                etf=self.schb
            ).count(),
            2,
        )

        latest.refresh_from_db()

        self.assertEqual(
            latest.close,
            Decimal("30.100000"),
        )
        self.assertEqual(
            latest.adjusted_close,
            Decimal("29.900000"),
        )

class ContributionTests(TestCase):
    def add_contribution(
        self,
        contribution_date="2026-08-31",
        amount="100.00",
    ):
        output = StringIO()

        call_command(
            "add_contribution",
            contribution_date=contribution_date,
            amount=amount,
            stdout=output,
        )

        return output.getvalue()

    def test_contribution_creates_shared_cash_deposit(self):
        output = self.add_contribution()

        contribution = Contribution.objects.get()
        transaction = CashTransaction.objects.get()

        self.assertEqual(contribution.sequence_number, 1)
        self.assertEqual(
            contribution.date,
            date(2026, 8, 31),
        )
        self.assertEqual(
            contribution.amount,
            Decimal("100.00"),
        )
        self.assertFalse(contribution.processed)

        self.assertEqual(
            transaction.transaction_type,
            CashTransaction.TransactionType.DEPOSIT,
        )
        self.assertEqual(
            transaction.amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            transaction.contribution,
            contribution,
        )

        self.assertIn(
            "Shared cash balance: $100.00",
            output,
        )

    def test_contributions_receive_sequential_numbers(self):
        self.add_contribution(
            contribution_date="2026-08-24",
        )
        self.add_contribution(
            contribution_date="2026-08-31",
        )

        sequence_numbers = list(
            Contribution.objects
            .order_by("sequence_number")
            .values_list(
                "sequence_number",
                flat=True,
            )
        )

        self.assertEqual(sequence_numbers, [1, 2])

    def test_duplicate_date_is_rejected(self):
        self.add_contribution()

        with self.assertRaisesMessage(
            CommandError,
            "already exists",
        ):
            self.add_contribution()

        self.assertEqual(
            Contribution.objects.count(),
            1,
        )
        self.assertEqual(
            CashTransaction.objects.count(),
            1,
        )

    def test_nonpositive_amount_is_rejected(self):
        with self.assertRaisesMessage(
            CommandError,
            "must be positive",
        ):
            self.add_contribution(amount="0.00")

        self.assertFalse(
            Contribution.objects.exists()
        )
        self.assertFalse(
            CashTransaction.objects.exists()
        )

    def test_all_contributions_share_one_cash_balance(self):
        self.add_contribution(
            contribution_date="2026-08-24",
            amount="100.00",
        )
        self.add_contribution(
            contribution_date="2026-08-31",
            amount="150.00",
        )

        shared_cash = (
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"]
        )

        self.assertEqual(
            shared_cash,
            Decimal("250.00"),
        )
