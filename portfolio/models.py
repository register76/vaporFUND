from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class ETF(models.Model):
    class AssetClass(models.TextChoices):
        STOCK = "STOCK", "Stock"
        BOND = "BOND", "Bond"

    ticker = models.CharField(
        max_length=10,
        unique=True,
    )
    name = models.CharField(max_length=120)
    asset_class = models.CharField(
        max_length=10,
        choices=AssetClass.choices,
    )
    target_percent = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(0),
            MaxValueValidator(100),
        ],
        help_text="Target percentage of the vaporFUND portfolio.",
    )
    enabled = models.BooleanField(default=True)
    expense_ratio = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Annual expense ratio expressed as a percentage.",
    )

    class Meta:
        ordering = ["ticker"]
        verbose_name = "ETF"
        verbose_name_plural = "ETFs"

    def __str__(self):
        return f"{self.ticker} — {self.name}"


class PriceHistory(models.Model):
    etf = models.ForeignKey(
        ETF,
        on_delete=models.CASCADE,
        related_name="prices",
    )
    date = models.DateField()
    close = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        help_text="Unadjusted closing price used for whole-share purchases.",
    )
    adjusted_close = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        help_text="Adjusted close used for total-return analysis.",
    )

    class Meta:
        ordering = ["etf__ticker", "-date"]
        verbose_name = "Price history"
        verbose_name_plural = "Price history"
        constraints = [
            models.UniqueConstraint(
                fields=["etf", "date"],
                name="unique_etf_price_date",
            ),
        ]

    def __str__(self):
        return (
            f"{self.etf.ticker} — {self.date}: "
            f"${self.close}"
        )


class Contribution(models.Model):
    date = models.DateField(unique=True)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=100,
    )
    sequence_number = models.PositiveIntegerField(
        unique=True,
    )
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return (
            f"Contribution {self.sequence_number}: "
            f"${self.amount} on {self.date}"
        )


class CashTransaction(models.Model):
    class TransactionType(models.TextChoices):
        DEPOSIT = "DEPOSIT", "Deposit"
        PURCHASE = "PURCHASE", "Purchase"
        DIVIDEND = "DIVIDEND", "Dividend"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    date = models.DateField()
    transaction_type = models.CharField(
        max_length=12,
        choices=TransactionType.choices,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text=(
            "Deposits and dividends are positive; "
            "purchases are negative."
        ),
    )
    contribution = models.OneToOneField(
        Contribution,
        on_delete=models.PROTECT,
        related_name="cash_transaction",
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]

    def __str__(self):
        return (
            f"{self.date} — "
            f"{self.get_transaction_type_display()}: "
            f"${self.amount}"
        )


class Recommendation(models.Model):
    class Action(models.TextChoices):
        BUY = "BUY", "Buy"
        HOLD_CASH = "HOLD_CASH", "Hold cash"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COMPLIANCE_APPROVED = (
            "COMPLIANCE_APPROVED",
            "Compliance approved",
        )
        EXECUTED = "EXECUTED", "Executed"
        CANCELLED = "CANCELLED", "Cancelled"

    contribution = models.OneToOneField(
        Contribution,
        on_delete=models.PROTECT,
        related_name="recommendation",
    )
    etf = models.ForeignKey(
        ETF,
        on_delete=models.PROTECT,
        related_name="recommendations",
    )
    action = models.CharField(
        max_length=12,
        choices=Action.choices,
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    available_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    portfolio_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    current_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    target_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )
    target_shortfall = models.DecimalField(
        max_digits=14,
        decimal_places=2,
    )

    reference_price = models.DecimalField(
        max_digits=14,
        decimal_places=6,
    )
    price_date = models.DateField()
    shares = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    reason = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-contribution__date"]

    def __str__(self):
        return (
            f"Contribution {self.contribution.sequence_number}: "
            f"{self.get_action_display()} {self.etf.ticker}"
        )


class TradeExecution(models.Model):
    recommendation = models.OneToOneField(
        Recommendation,
        on_delete=models.PROTECT,
        related_name="execution",
    )
    trade_date = models.DateField()
    etf = models.ForeignKey(
        ETF,
        on_delete=models.PROTECT,
        related_name="trade_executions",
    )
    shares = models.PositiveIntegerField()
    price_per_share = models.DecimalField(
        max_digits=14,
        decimal_places=6,
    )
    fees = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    cash_transaction = models.OneToOneField(
        CashTransaction,
        on_delete=models.PROTECT,
        related_name="trade_execution",
    )
    broker_reference = models.CharField(
        max_length=100,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-trade_date", "-created_at"]

    def __str__(self):
        return (
            f"{self.trade_date}: bought {self.shares} "
            f"{self.etf.ticker} at ${self.price_per_share}"
        )


class HoldingLot(models.Model):
    execution = models.OneToOneField(
        TradeExecution,
        on_delete=models.PROTECT,
        related_name="holding_lot",
    )
    etf = models.ForeignKey(
        ETF,
        on_delete=models.PROTECT,
        related_name="holding_lots",
    )
    purchase_date = models.DateField()
    shares_acquired = models.PositiveIntegerField()
    shares_remaining = models.PositiveIntegerField()
    price_per_share = models.DecimalField(
        max_digits=14,
        decimal_places=6,
    )
    total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )
    compliance_eligible_date = models.DateField(
        help_text=(
            "Earliest system-calculated date for a possible sale. "
            "Employer compliance approval still takes precedence."
        )
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-purchase_date", "-created_at"]

    def __str__(self):
        return (
            f"{self.etf.ticker}: "
            f"{self.shares_remaining} shares "
            f"from {self.purchase_date}"
        )
