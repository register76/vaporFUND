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

class ApprovalConfirmationForm(forms.Form):
    confirm_approval = forms.BooleanField(
        label=(
            "I have reviewed this recommendation and "
            "authorize it for manual execution."
        ),
        required=True,
    )

