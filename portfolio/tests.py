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
from django.urls import reverse

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
    calculate_purchase_plan,
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

    def test_purchase_plan_recalculates_after_each_buy(self):
        ETF.objects.update(target_percent=0)

        schb = self.etfs["SCHB"]
        schb.target_percent = 50
        schb.save(update_fields=["target_percent"])

        vteb = self.etfs["VTEB"]
        vteb.target_percent = 50
        vteb.save(update_fields=["target_percent"])

        self.add_cash(amount="200.00")

        plan = calculate_purchase_plan(
            date(2026, 8, 31)
        )

        self.assertEqual(len(plan.decisions), 2)

        first, second = plan.decisions

        self.assertEqual(first.selected.etf, vteb)
        self.assertEqual(first.shares, 2)
        self.assertEqual(
            first.estimated_cost,
            Decimal("100.00"),
        )

        self.assertEqual(second.selected.etf, schb)
        self.assertEqual(second.shares, 3)
        self.assertEqual(
            second.estimated_cost,
            Decimal("90.00"),
        )

        self.assertEqual(
            plan.remaining_cash,
            Decimal("10.00"),
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

    def test_command_creates_ordered_purchase_plan(self):
        ETF.objects.update(target_percent=0)

        schb = self.etfs["SCHB"]
        schb.target_percent = 50
        schb.save(update_fields=["target_percent"])

        vteb = self.etfs["VTEB"]
        vteb.target_percent = 50
        vteb.save(update_fields=["target_percent"])

        self.contribution.amount = Decimal("200.00")
        self.contribution.save(update_fields=["amount"])

        cash_transaction = (
            self.contribution.cash_transaction
        )
        cash_transaction.amount = Decimal("200.00")
        cash_transaction.save(update_fields=["amount"])

        output = self.generate()

        recommendations = list(
            Recommendation.objects.filter(
                contribution=self.contribution
            ).order_by("plan_order")
        )

        self.assertEqual(len(recommendations), 2)

        first, second = recommendations

        self.assertEqual(first.plan_order, 1)
        self.assertEqual(first.etf, vteb)
        self.assertEqual(first.shares, 2)
        self.assertEqual(
            first.available_cash,
            Decimal("200.00"),
        )
        self.assertEqual(
            first.estimated_cost,
            Decimal("100.00"),
        )

        self.assertEqual(second.plan_order, 2)
        self.assertEqual(second.etf, schb)
        self.assertEqual(second.shares, 3)
        self.assertEqual(
            second.available_cash,
            Decimal("100.00"),
        )
        self.assertEqual(
            second.estimated_cost,
            Decimal("90.00"),
        )

        self.assertIn(
            "Purchase 1: BUY 2 VTEB",
            output,
        )
        self.assertIn(
            "Purchase 2: BUY 3 SCHB",
            output,
        )
        self.assertIn(
            "Total estimated purchases: $190.00",
            output,
        )
        self.assertIn(
            "Estimated remaining cash: $10.00",
            output,
        )
        self.assertNotIn(
            "Compliance approval",
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

class ApprovalCommandTests(TestCase):
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
        call_command(
            "generate_recommendation",
            contribution=1,
            stdout=StringIO(),
        )

        return Recommendation.objects.get(
            contribution=self.contribution
        )

    def test_buy_recommendation_can_be_approved(self):
        recommendation = self.generate()

        output = StringIO()

        call_command(
            "approve_recommendation",
            contribution=1,
            stdout=output,
        )

        recommendation.refresh_from_db()

        self.assertEqual(
            recommendation.status,
            Recommendation.Status.COMPLIANCE_APPROVED,
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
            "no brokerage order has been placed",
            output.getvalue(),
        )

    def test_hold_cash_cannot_be_approved(self):
        ETF.objects.update(target_percent=0)

        xmmo = self.etfs["XMMO"]
        xmmo.target_percent = 100
        xmmo.save(update_fields=["target_percent"])

        recommendation = self.generate()

        self.assertEqual(
            recommendation.action,
            Recommendation.Action.HOLD_CASH,
        )

        with self.assertRaisesMessage(
            CommandError,
            "no purchase to approve",
        ):
            call_command(
                "approve_recommendation",
                contribution=1,
                stdout=StringIO(),
            )

        recommendation.refresh_from_db()

        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )

    def test_missing_recommendation_is_rejected(self):
        with self.assertRaisesMessage(
            CommandError,
            "No recommendation exists",
        ):
            call_command(
                "approve_recommendation",
                contribution=1,
                stdout=StringIO(),
            )

class PurchaseConfirmationTests(TestCase):
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

        call_command(
            "generate_recommendation",
            contribution=1,
            stdout=StringIO(),
        )

        self.contribution = Contribution.objects.get(
            sequence_number=1
        )
        self.recommendation = Recommendation.objects.get(
            contribution=self.contribution
        )

    def approve(self):
        call_command(
            "approve_recommendation",
            contribution=1,
            stdout=StringIO(),
        )

    def confirm(
        self,
        price="30.00",
        shares=1,
    ):
        output = StringIO()

        call_command(
            "confirm_purchase",
            contribution=1,
            price=price,
            shares=shares,
            trade_date="2026-08-31",
            fees="0.00",
            stdout=output,
        )

        return output.getvalue()

    def test_purchase_requires_approval(self):
        with self.assertRaisesMessage(
            CommandError,
            "has not been compliance-approved",
        ):
            self.confirm()

        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_confirmed_purchase_updates_complete_ledger(
        self,
    ):
        self.approve()
        output = self.confirm()

        self.recommendation.refresh_from_db()
        self.contribution.refresh_from_db()

        execution = TradeExecution.objects.get()
        lot = HoldingLot.objects.get()

        shared_cash = (
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"]
        )

        self.assertEqual(
            self.recommendation.status,
            Recommendation.Status.EXECUTED,
        )
        self.assertTrue(self.contribution.processed)
        self.assertEqual(
            execution.etf,
            self.etfs["SCHB"],
        )
        self.assertEqual(execution.shares, 1)
        self.assertEqual(
            execution.total_cost,
            Decimal("30.00"),
        )
        self.assertEqual(
            shared_cash,
            Decimal("70.00"),
        )
        self.assertEqual(
            lot.shares_acquired,
            1,
        )
        self.assertEqual(
            lot.shares_remaining,
            1,
        )
        self.assertEqual(
            lot.compliance_eligible_date,
            date(2026, 9, 30),
        )
        self.assertIn(
            "Remaining shared cash: $70.00",
            output,
        )

    def test_purchase_cannot_exceed_recommendation(self):
        self.approve()

        with self.assertRaisesMessage(
            CommandError,
            "cannot exceed",
        ):
            self.confirm(shares=2)

        self.assertFalse(
            TradeExecution.objects.exists()
        )

    def test_purchase_cannot_exceed_available_cash(self):
        self.approve()

        with self.assertRaisesMessage(
            CommandError,
            "Insufficient cash",
        ):
            self.confirm(price="101.00")

        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_recommendation_cannot_execute_twice(self):
        self.approve()
        self.confirm()

        with self.assertRaisesMessage(
            CommandError,
            "already has an execution",
        ):
            self.confirm()

        self.assertEqual(
            TradeExecution.objects.count(),
            1,
        )
        self.assertEqual(
            HoldingLot.objects.count(),
            1,
        )


class DashboardContributionTests(TestCase):
    def setUp(self):
        targets = [
            (
                "SCHB",
                "Schwab U.S. Broad Market ETF",
                ETF.AssetClass.STOCK,
                40,
                "30.00",
            ),
            (
                "XMMO",
                "Invesco S&P MidCap Momentum ETF",
                ETF.AssetClass.STOCK,
                5,
                "120.00",
            ),
            (
                "AVUV",
                "Avantis U.S. Small Cap Value ETF",
                ETF.AssetClass.STOCK,
                10,
                "100.00",
            ),
            (
                "VEA",
                "Vanguard FTSE Developed Markets ETF",
                ETF.AssetClass.STOCK,
                15,
                "55.00",
            ),
            (
                "VWO",
                "Vanguard FTSE Emerging Markets ETF",
                ETF.AssetClass.STOCK,
                5,
                "50.00",
            ),
            (
                "VTEB",
                "Vanguard Tax-Exempt Bond ETF",
                ETF.AssetClass.BOND,
                25,
                "50.00",
            ),
        ]

        self.etfs = {}

        for ticker, name, asset_class, target, price in targets:
            etf = ETF.objects.create(
                ticker=ticker,
                name=name,
                asset_class=asset_class,
                target_percent=target,
                enabled=True,
            )

            PriceHistory.objects.create(
                etf=etf,
                date=date(2026, 8, 31),
                close=Decimal(price),
                adjusted_close=Decimal(price),
            )

            self.etfs[ticker] = etf

        self.url = reverse(
            "portfolio:dashboard"
        )

    def post_contribution(
        self,
        contribution_date="2026-09-01",
        amount="100.00",
        follow=True,
    ):
        return self.client.post(
            self.url,
            {
                "contribution_date": contribution_date,
                "amount": amount,
            },
            follow=follow,
        )

    def test_dashboard_displays_contribution_form(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Add contribution",
        )
        self.assertContains(
            response,
            "generate purchase plan",
        )

    def test_post_creates_contribution_deposit_and_draft(self):
        response = self.post_contribution()

        self.assertEqual(response.status_code, 200)

        contribution = Contribution.objects.get()
        transaction = CashTransaction.objects.get()
        recommendation = Recommendation.objects.get()

        self.assertEqual(
            contribution.date,
            date(2026, 9, 1),
        )
        self.assertEqual(
            contribution.amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            contribution.sequence_number,
            1,
        )
        self.assertFalse(contribution.processed)

        self.assertEqual(
            transaction.amount,
            Decimal("100.00"),
        )
        self.assertEqual(
            transaction.transaction_type,
            CashTransaction.TransactionType.DEPOSIT,
        )
        self.assertEqual(
            transaction.contribution,
            contribution,
        )

        self.assertEqual(
            recommendation.contribution,
            contribution,
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )
        self.assertEqual(
            recommendation.action,
            Recommendation.Action.BUY,
        )
        self.assertEqual(
            recommendation.etf,
            self.etfs["SCHB"],
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

        self.assertContains(
            response,
            "Draft purchase plan: buy 1 SCHB",
        )

    def test_dashboard_displays_complete_purchase_plan(self):
        ETF.objects.update(target_percent=0)

        schb = self.etfs["SCHB"]
        schb.target_percent = 50
        schb.save(update_fields=["target_percent"])

        vteb = self.etfs["VTEB"]
        vteb.target_percent = 50
        vteb.save(update_fields=["target_percent"])

        response = self.post_contribution(
            amount="200.00",
        )

        recommendations = list(
            response.context["latest_recommendations"]
        )

        self.assertEqual(len(recommendations), 2)
        self.assertEqual(
            [item.plan_order for item in recommendations],
            [1, 2],
        )

        self.assertContains(
            response,
            "Latest purchase plan",
        )
        self.assertContains(
            response,
            "Contribution #1",
        )
        self.assertContains(
            response,
            "Purchase 1",
        )
        self.assertContains(
            response,
            "2 VTEB",
        )
        self.assertContains(
            response,
            "Purchase 2",
        )
        self.assertContains(
            response,
            "3 SCHB",
        )
        self.assertContains(
            response,
            "Total planned",
        )
        self.assertContains(
            response,
            "$190.00",
        )
        self.assertContains(
            response,
            "Cash carried forward",
        )
        self.assertContains(
            response,
            "$10.00",
        )
        self.assertNotContains(
            response,
            "Compliance approval",
        )
        self.assertContains(
            response,
            (
                "Draft purchase plan: buy 2 VTEB, "
                "then buy 3 SCHB"
            ),
        )

    def test_zero_amount_is_rejected_by_server(self):
        response = self.post_contribution(
            amount="0.00",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Ensure this value is greater than or equal to 0.01",
        )

        self.assertFalse(
            Contribution.objects.exists()
        )
        self.assertFalse(
            CashTransaction.objects.exists()
        )
        self.assertFalse(
            Recommendation.objects.exists()
        )

    def test_duplicate_date_is_rejected(self):
        first_response = self.post_contribution()
        second_response = self.post_contribution()

        self.assertEqual(
            first_response.status_code,
            200,
        )
        self.assertEqual(
            second_response.status_code,
            200,
        )

        self.assertContains(
            second_response,
            "A contribution already exists for 2026-09-01",
        )

        self.assertEqual(
            Contribution.objects.count(),
            1,
        )
        self.assertEqual(
            CashTransaction.objects.count(),
            1,
        )
        self.assertEqual(
            Recommendation.objects.count(),
            1,
        )

    def test_new_contribution_is_blocked_while_plan_pending(
        self,
    ):
        self.post_contribution(
            contribution_date="2026-09-01",
        )

        response = self.post_contribution(
            contribution_date="2026-09-08",
        )

        self.assertContains(
            response,
            (
                "Contribution 1 still has an active "
                "purchase plan"
            ),
        )
        self.assertEqual(
            Contribution.objects.count(),
            1,
        )
        self.assertEqual(
            CashTransaction.objects.count(),
            1,
        )
        self.assertEqual(
            Recommendation.objects.count(),
            1,
        )

    def test_failure_to_generate_rolls_back_deposit(self):
        PriceHistory.objects.filter(
            etf=self.etfs["VTEB"]
        ).delete()

        response = self.post_contribution()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No price exists for VTEB",
        )

        self.assertFalse(
            Contribution.objects.exists()
        )
        self.assertFalse(
            CashTransaction.objects.exists()
        )
        self.assertFalse(
            Recommendation.objects.exists()
        )

    def test_review_page_displays_stored_recommendation(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()

        url = reverse(
            "portfolio:recommendation_review",
            args=[
                recommendation.contribution.sequence_number
            ],
        )

        before = {
            "contributions": Contribution.objects.count(),
            "transactions": CashTransaction.objects.count(),
            "recommendations": Recommendation.objects.count(),
            "executions": TradeExecution.objects.count(),
            "lots": HoldingLot.objects.count(),
        }

        response = self.client.get(url)

        after = {
            "contributions": Contribution.objects.count(),
            "transactions": CashTransaction.objects.count(),
            "recommendations": Recommendation.objects.count(),
            "executions": TradeExecution.objects.count(),
            "lots": HoldingLot.objects.count(),
        }

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "portfolio/recommendation_review.html",
        )
        self.assertContains(
            response,
            "Recommendation review",
        )
        self.assertContains(response, "SCHB")
        self.assertContains(
            response,
            "No brokerage action has occurred",
        )
        self.assertEqual(before, after)

    def test_review_page_returns_404_for_unknown_contribution(self):
        url = reverse(
            "portfolio:recommendation_review",
            args=[999],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_review_page_rejects_post_requests(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()

        url = reverse(
            "portfolio:recommendation_review",
            args=[
                recommendation.contribution.sequence_number
            ],
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 405)
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )

    def approval_url(self):
        recommendation = Recommendation.objects.get()

        return reverse(
            "portfolio:recommendation_approve",
            args=[
                recommendation.contribution.sequence_number
            ],
        )

    def test_approval_page_is_read_only_on_get(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()
        cash_before = CashTransaction.objects.count()

        response = self.client.get(
            self.approval_url()
        )

        recommendation.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "portfolio/recommendation_approve.html",
        )
        self.assertContains(
            response,
            "Approve this recommendation?",
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )
        self.assertEqual(
            CashTransaction.objects.count(),
            cash_before,
        )
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_approval_requires_confirmation_checkbox(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()

        response = self.client.post(
            self.approval_url(),
            {},
        )

        recommendation.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "This field is required.",
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_confirmed_web_approval_changes_only_status(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()

        transaction_count = (
            CashTransaction.objects.count()
        )
        cash_total = (
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"]
        )

        response = self.client.post(
            self.approval_url(),
            {
                "confirm_approval": "on",
            },
            follow=True,
        )

        recommendation.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.COMPLIANCE_APPROVED,
        )
        self.assertContains(
            response,
            "No brokerage order was placed",
        )
        self.assertEqual(
            CashTransaction.objects.count(),
            transaction_count,
        )
        self.assertEqual(
            CashTransaction.objects.aggregate(
                total=Sum("amount")
            )["total"],
            cash_total,
        )
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_web_approval_cannot_be_repeated(self):
        self.post_contribution()

        url = self.approval_url()

        first_response = self.client.post(
            url,
            {
                "confirm_approval": "on",
            },
            follow=True,
        )

        second_response = self.client.post(
            url,
            {
                "confirm_approval": "on",
            },
            follow=True,
        )

        recommendation = Recommendation.objects.get()

        self.assertEqual(
            first_response.status_code,
            200,
        )
        self.assertEqual(
            second_response.status_code,
            200,
        )
        self.assertContains(
            second_response,
            "Only a draft recommendation can be approved.",
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.COMPLIANCE_APPROVED,
        )
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )

    def test_hold_cash_cannot_be_web_approved(self):
        self.post_contribution()

        recommendation = Recommendation.objects.get()
        recommendation.action = (
            Recommendation.Action.HOLD_CASH
        )
        recommendation.shares = 0
        recommendation.estimated_cost = Decimal("0.00")
        recommendation.save(
            update_fields=[
                "action",
                "shares",
                "estimated_cost",
                "updated_at",
            ]
        )

        response = self.client.post(
            self.approval_url(),
            {
                "confirm_approval": "on",
            },
            follow=True,
        )

        recommendation.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "A hold-cash recommendation has no purchase to approve.",
        )
        self.assertEqual(
            recommendation.status,
            Recommendation.Status.DRAFT,
        )
        self.assertFalse(
            TradeExecution.objects.exists()
        )
        self.assertFalse(
            HoldingLot.objects.exists()
        )
