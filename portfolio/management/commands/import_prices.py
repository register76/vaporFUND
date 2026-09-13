from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portfolio.models import ETF, PriceHistory


class Command(BaseCommand):
    help = "Import raw and adjusted daily prices for vaporFUND ETFs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            default="2000-01-01",
            help="First date to request in YYYY-MM-DD format",
        )
        parser.add_argument(
            "--ticker",
            action="append",
            help=(
                "Import one ticker. May be supplied more than once. "
                "Defaults to every enabled target ETF."
            ),
        )

    def handle(self, *args, **options):
        try:
            start_date = date.fromisoformat(options["start"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        requested_tickers = options["ticker"]

        etfs = ETF.objects.filter(
            enabled=True,
            account_targets__target_percent__gt=0,
        ).distinct()

        if requested_tickers:
            normalized_tickers = {
                ticker.strip().upper()
                for ticker in requested_tickers
            }

            etfs = etfs.filter(
                ticker__in=normalized_tickers
            )

            found_tickers = set(
                etfs.values_list("ticker", flat=True)
            )

            missing_tickers = (
                normalized_tickers - found_tickers
            )

            if missing_tickers:
                raise CommandError(
                    "No enabled target ETF exists for: "
                    + ", ".join(sorted(missing_tickers))
                )

        etfs = list(etfs.order_by("ticker"))

        if not etfs:
            raise CommandError(
                "No enabled target ETFs are configured."
            )

        # Yahoo treats the end date as exclusive.
        end_date = date.today() + timedelta(days=1)

        for etf in etfs:
            self.import_etf(
                etf=etf,
                start_date=start_date,
                end_date=end_date,
            )

        self.stdout.write(
            self.style.SUCCESS("Price import complete.")
        )

    @transaction.atomic
    def import_etf(self, etf, start_date, end_date):
        self.stdout.write(
            f"Downloading {etf.ticker}..."
        )

        try:
            history = yf.download(
                etf.ticker,
                start=start_date.isoformat(),
                end=end_date.isoformat(),
                auto_adjust=False,
                progress=False,
                actions=False,
                threads=False,
                multi_level_index=False,
            )
        except Exception as error:
            raise CommandError(
                f"Unable to download {etf.ticker}: {error}"
            ) from error

        if history.empty:
            raise CommandError(
                f"No price history was returned for {etf.ticker}."
            )

        # Some yfinance versions still return a MultiIndex for a
        # single ticker. Normalize it to ordinary column names.
        if isinstance(history.columns, pd.MultiIndex):
            history.columns = (
                history.columns.get_level_values(0)
            )

        required_columns = {
            "Close",
            "Adj Close",
        }

        missing_columns = (
            required_columns - set(history.columns)
        )

        if missing_columns:
            raise CommandError(
                f"{etf.ticker} is missing price columns: "
                + ", ".join(sorted(missing_columns))
            )

        existing_dates = set(
            PriceHistory.objects.filter(
                etf=etf,
            ).values_list("date", flat=True)
        )

        records = []
        created_count = 0
        updated_count = 0

        for timestamp, row in history.iterrows():
            raw_close = row["Close"]
            adjusted_close = row["Adj Close"]

            if (
                pd.isna(raw_close)
                or pd.isna(adjusted_close)
            ):
                continue

            price_date = timestamp.date()

            records.append(
                PriceHistory(
                    etf=etf,
                    date=price_date,
                    close=Decimal(
                        str(round(float(raw_close), 6))
                    ),
                    adjusted_close=Decimal(
                        str(
                            round(
                                float(adjusted_close),
                                6,
                            )
                        )
                    ),
                )
            )

            if price_date in existing_dates:
                updated_count += 1
            else:
                created_count += 1

        if not records:
            raise CommandError(
                f"No usable price rows were returned for "
                f"{etf.ticker}."
            )

        PriceHistory.objects.bulk_create(
            records,
            batch_size=1000,
            update_conflicts=True,
            update_fields=[
                "close",
                "adjusted_close",
            ],
            unique_fields=[
                "etf",
                "date",
            ],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"{etf.ticker}: "
                f"{created_count} created, "
                f"{updated_count} updated"
            )
        )
