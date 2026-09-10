from datetime import date
from decimal import Decimal

from django import forms

class ContributionForm(forms.Form):
    contribution_date = forms.DateField(
        label="Contribution date",
        initial=date.today,
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    amount = forms.DecimalField(
        label="Amount",
        initial="100.00",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "inputmode": "decimal",
            }
        ),
    )

class PurchaseExecutionForm(forms.Form):
    trade_date = forms.DateField(
        label="Trade date",
        initial=date.today,
        widget=forms.DateInput(
            attrs={
                "type": "date",
            }
        ),
    )

    price_per_share = forms.DecimalField(
        label="Actual price per share",
        min_value=Decimal("0.000001"),
        max_digits=14,
        decimal_places=6,
        widget=forms.NumberInput(
            attrs={
                "step": "0.000001",
                "inputmode": "decimal",
            }
        ),
    )

    shares = forms.IntegerField(
        label="Shares purchased",
        min_value=1,
    )

    fees = forms.DecimalField(
        label="Fees",
        initial="0.00",
        min_value=Decimal("0.00"),
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "inputmode": "decimal",
            }
        ),
    )

    broker_reference = forms.CharField(
        label="Merrill confirmation or reference",
        required=False,
        max_length=100,
    )

    confirm_execution = forms.BooleanField(
        label=(
            "I confirm this purchase was completed "
            "at Merrill."
        ),
        required=True,
    )

    def __init__(
        self,
        *args,
        recommendation=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.recommendation = recommendation

        if recommendation is not None:
            self.fields[
                "price_per_share"
            ].initial = recommendation.reference_price
            self.fields[
                "shares"
            ].initial = recommendation.shares
            self.fields[
                "shares"
            ].widget.attrs[
                "max"
            ] = recommendation.shares

    def clean_shares(self):
        shares = self.cleaned_data["shares"]

        if (
            self.recommendation is not None
            and shares > self.recommendation.shares
        ):
            raise forms.ValidationError(
                "Actual shares cannot exceed the "
                f"recommended "
                f"{self.recommendation.shares} shares."
            )

        return shares
