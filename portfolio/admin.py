from django.contrib import admin

from .models import (
    Account,
    AccountTarget,
    CashTransaction,
    Contribution,
    ETF,
    HoldingLot,
    PriceHistory,
    Recommendation,
    TradeExecution,
)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(AccountTarget)
class AccountTargetAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "etf",
        "target_percent",
    )
    list_filter = ("account",)
    search_fields = (
        "account__name",
        "etf__ticker",
        "etf__name",
    )
    ordering = (
        "account__name",
        "etf__ticker",
    )


@admin.register(ETF)
class ETFAdmin(admin.ModelAdmin):
    list_display = (
        "ticker",
        "name",
        "asset_class",
        "enabled",
        "expense_ratio",
    )
    list_filter = (
        "asset_class",
        "enabled",
    )
    search_fields = (
        "ticker",
        "name",
    )
    ordering = ("ticker",)


@admin.register(PriceHistory)
class PriceHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "etf",
        "date",
        "close",
        "adjusted_close",
    )
    list_filter = ("etf",)
    search_fields = ("etf__ticker",)
    date_hierarchy = "date"
    ordering = (
        "-date",
        "etf__ticker",
    )


@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = (
        "sequence_number",
        "date",
        "amount",
        "processed",
        "created_at",
    )
    list_filter = ("processed",)
    date_hierarchy = "date"
    ordering = ("-sequence_number",)


@admin.register(CashTransaction)
class CashTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "transaction_type",
        "amount",
        "contribution",
        "description",
    )
    list_filter = ("transaction_type",)
    date_hierarchy = "date"
    ordering = (
        "-date",
        "-created_at",
    )


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = (
        "contribution",
        "etf",
        "action",
        "status",
        "available_cash",
        "shares",
        "estimated_cost",
    )
    list_filter = (
        "action",
        "status",
        "etf",
    )
    search_fields = (
        "etf__ticker",
        "reason",
    )
    ordering = ("-contribution__date",)


@admin.register(TradeExecution)
class TradeExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "trade_date",
        "etf",
        "shares",
        "price_per_share",
        "fees",
        "total_cost",
        "broker_reference",
    )
    list_filter = ("etf",)
    search_fields = (
        "etf__ticker",
        "broker_reference",
    )
    date_hierarchy = "trade_date"
    ordering = ("-trade_date",)


@admin.register(HoldingLot)
class HoldingLotAdmin(admin.ModelAdmin):
    list_display = (
        "etf",
        "purchase_date",
        "shares_acquired",
        "shares_remaining",
        "price_per_share",
        "total_cost",
        "compliance_eligible_date",
    )
    list_filter = ("etf",)
    search_fields = ("etf__ticker",)
    date_hierarchy = "purchase_date"
    ordering = ("-purchase_date",)
