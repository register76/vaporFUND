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
    HoldingLot,
    PriceHistory,
    Recommendation,
    TradeExecution,
)

from .services import (
    AllocationError,
    calculate_allocation,
    choose_share_quantity,
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

class AllocationServiceTests(TestCase):
    def setUp(self):
        call_command(
            "seed_portfolio",
            stdout=StringIO(),
        )

        prices = {
            "SCHB": "30.00",
            "XMMO": "120.00",
            "AVUV": "90.00",
            "VEA": "50.00",
            "VWO": "40.00",
            "VTEB": "50.00",
        }

        self.etfs = {}

        for ticker, value in prices.items():
            etf = ETF.objects.get(ticker=ticker)
            self.etfs[ticker] = etf

            PriceHistory.objects.create(
                etf=etf,
                date=date(2026, 8, 28),
                close=Decimal(value),
                adjusted_close=Decimal(value),
            )

    def add_cash(self, amount="100.00"):
        call_command(
            "add_contribution",
            contribution_date="2026-08-31",
            amount=amount,
            stdout=StringIO(),
        )

        return Contribution.objects.get(
            date=date(2026, 8, 31)
        )

    def test_empty_portfolio_selects_one_schb_share(self):
        self.add_cash()

        decision = calculate_allocation(
            date(2026, 8, 31)
        )

        self.assertEqual(
            decision.selected.etf,
            self.etfs["SCHB"],
        )
        self.assertEqual(decision.action, "BUY")
        self.assertEqual(decision.shares, 1)
        self.assertEqual(
            decision.estimated_cost,
            Decimal("30.00"),
        )
        self.assertEqual(
            decision.remaining_cash,
            Decimal("70.00"),
        )

    def test_unaffordable_selected_etf_holds_cash(self):
        ETF.objects.update(target_percent=0)

        xmmo = self.etfs["XMMO"]
        xmmo.target_percent = 100
        xmmo.save(update_fields=["target_percent"])

        self.add_cash()

        decision = calculate_allocation(
            date(2026, 8, 31)
        )

        self.assertEqual(
            decision.selected.etf,
            xmmo,
        )
        self.assertEqual(
            decision.action,
            "HOLD_CASH",
        )
        self.assertEqual(decision.shares, 0)
        self.assertEqual(
            decision.remaining_cash,
            Decimal("100.00"),
        )
        self.assertIn(
            "no substitute ETF",
            decision.reason,
        )

    def test_invalid_target_total_is_rejected(self):
        schb = self.etfs["SCHB"]
        schb.target_percent = 39
        schb.save(update_fields=["target_percent"])

        self.add_cash()

        with self.assertRaisesMessage(
            AllocationError,
            "not 100%",
        ):
            calculate_allocation(
                date(2026, 8, 31)
            )

    def test_whole_share_quantity_minimizes_error(self):
        self.assertEqual(
            choose_share_quantity(
                shortfall=Decimal("40.00"),
                share_price=Decimal("29.70"),
                available_cash=Decimal("100.00"),
            ),
            1,
        )

        self.assertEqual(
            choose_share_quantity(
                shortfall=Decimal("80.00"),
                share_price=Decimal("30.00"),
                available_cash=Decimal("200.00"),
            ),
            3,
        )

        self.assertEqual(
            choose_share_quantity(
                shortfall=Decimal("5.00"),
                share_price=Decimal("120.00"),
                available_cash=Decimal("100.00"),
            ),
            0,
        )

        self.assertEqual(
            choose_share_quantity(
                shortfall=Decimal("10.00"),
                share_price=Decimal("30.00"),
                available_cash=Decimal("100.00"),
            ),
            0,
        )

    def test_existing_schb_holding_redirects_to_vteb(self):
        contribution = self.add_cash(
            amount="1000.00"
        )

        initial = calculate_allocation(
            date(2026, 8, 31)
        )

        self.assertEqual(
            initial.selected.etf,
            self.etfs["SCHB"],
        )
        self.assertEqual(initial.shares, 13)

        recommendation = Recommendation.objects.create(
            contribution=contribution,
            etf=self.etfs["SCHB"],
            action=Recommendation.Action.BUY,
            status=Recommendation.Status.EXECUTED,
            available_cash=initial.available_cash,
            portfolio_value=initial.portfolio_value,
            current_value=initial.selected.current_value,
            target_value=initial.selected.target_value,
            target_shortfall=initial.selected.shortfall,
            reference_price=Decimal("30.00"),
            price_date=date(2026, 8, 28),
            shares=13,
            estimated_cost=Decimal("390.00"),
            reason="Test SCHB purchase",
        )

        purchase_cash = CashTransaction.objects.create(
            date=date(2026, 8, 31),
            transaction_type=(
                CashTransaction.TransactionType.PURCHASE
            ),
            amount=Decimal("-390.00"),
            description="Test purchase",
        )

        execution = TradeExecution.objects.create(
            recommendation=recommendation,
            trade_date=date(2026, 8, 31),
            etf=self.etfs["SCHB"],
            shares=13,
            price_per_share=Decimal("30.00"),
            fees=Decimal("0.00"),
            total_cost=Decimal("390.00"),
            cash_transaction=purchase_cash,
        )

        HoldingLot.objects.create(
            execution=execution,
            etf=self.etfs["SCHB"],
            purchase_date=date(2026, 8, 31),
            shares_acquired=13,
            shares_remaining=13,
            price_per_share=Decimal("30.00"),
            total_cost=Decimal("390.00"),
            compliance_eligible_date=date(
                2026,
                9,
                30,
            ),
        )

        contribution.processed = True
        contribution.save(update_fields=["processed"])

        next_decision = calculate_allocation(
            date(2026, 8, 31)
        )

        self.assertEqual(
            next_decision.portfolio_value,
            Decimal("1000.00"),
        )
        self.assertEqual(
            next_decision.selected.etf,
            self.etfs["VTEB"],
        )
        self.assertEqual(
            next_decision.selected.shortfall,
            Decimal("250.00"),
        )
        self.assertEqual(next_decision.shares, 5)


class RecommendationCommandTests(TestCase):
    def setUp(self):
        call_command(
            "seed_portfolio",
            stdout=StringIO(),
        )

        prices = {
            "SCHB": "30.00",
            "XMMO": "120.00",
            "AVUV": "90.00",
            "VEA": "50.00",
            "VWO": "40.00",
            "VTEB": "50.00",
        }

        self.etfs = {}

        for ticker, value in prices.items():
            etf = ETF.objects.get(ticker=ticker)
            self.etfs[ticker] = etf

            PriceHistory.objects.create(
                etf=etf,
                date=date(2026, 8, 28),
                close=Decimal(value),
                adjusted_close=Decimal(value),
            )

        call_command(
            "add_contribution",
            contribution_date="2026-08-31",
            amount="100.00",
            stdout=StringIO(),
        )

        self.contribution = Contribution.objects.get(
            sequence_number=1
        )

    def generate(self):
        output = StringIO()

        call_command(
            "generate_recommendation",
            contribution=1,
            stdout=output,
        )

        return output.getvalue()

    def test_command_creates_schb_draft(self):
        output = self.generate()

        recommendation = Recommendation.objects.get(
            contribution=self.contribution
        )

        self.assertEqual(
            recommendation.etf,
            self.etfs["SCHB"],
        )
        self.assertEqual(
            recommendation.action,
            Recommendation.Action.BUY,
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )
        self.assertEqual(
            recommendation.available_cash,
            Decimal("100.00"),
        )
        self.assertEqual(
            recommendation.portfolio_value,
            Decimal("100.00"),
        )
        self.assertEqual(
            recommendation.target_shortfall,
            Decimal("40.00"),
        )
        self.assertEqual(recommendation.shares, 1)
        self.assertEqual(
            recommendation.estimated_cost,
            Decimal("30.00"),
        )

        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )
        self.assertFalse(
            self.contribution.processed
        )

        self.assertIn(
            "Created recommendation",
            output,
        )

    def test_regenerating_draft_is_idempotent(self):
        first_output = self.generate()
        first = Recommendation.objects.get()

        second_output = self.generate()
        second = Recommendation.objects.get()

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            Recommendation.objects.count(),
            1,
        )
        self.assertIn(
            "Created recommendation",
            first_output,
        )
        self.assertIn(
            "Updated recommendation",
            second_output,
        )

    def test_command_records_hold_cash_without_substitution(
        self,
    ):
        ETF.objects.update(target_percent=0)

        xmmo = self.etfs["XMMO"]
        xmmo.target_percent = 100
        xmmo.save(update_fields=["target_percent"])

        self.generate()

        recommendation = Recommendation.objects.get()

        self.assertEqual(recommendation.etf, xmmo)
        self.assertEqual(
            recommendation.action,
            Recommendation.Action.HOLD_CASH,
        )
        self.assertEqual(recommendation.shares, 0)
        self.assertEqual(
            recommendation.estimated_cost,
            Decimal("0.00"),
        )
        self.assertIn(
            "no substitute ETF",
            recommendation.reason,
        )

    def test_approved_recommendation_cannot_be_regenerated(
        self,
    ):
        self.generate()

        recommendation = Recommendation.objects.get()
        recommendation.status = (
            Recommendation.Status.COMPLIANCE_APPROVED
        )
        recommendation.save(update_fields=["status"])

        with self.assertRaisesMessage(
            CommandError,
            "no longer a draft",
        ):
            self.generate()
