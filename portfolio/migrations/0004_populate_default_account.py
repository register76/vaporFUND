from django.db import migrations


DEFAULT_ACCOUNT_NAME = "vaporFUND Test"


def populate_default_account(apps, schema_editor):
    Account = apps.get_model("portfolio", "Account")
    Contribution = apps.get_model("portfolio", "Contribution")
    CashTransaction = apps.get_model("portfolio", "CashTransaction")

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

    Contribution.objects.filter(
        account__isnull=True
    ).update(account=account)

    CashTransaction.objects.filter(
        account__isnull=True
    ).update(account=account)


def reverse_populate_default_account(apps, schema_editor):
    Account = apps.get_model("portfolio", "Account")
    Contribution = apps.get_model("portfolio", "Contribution")
    CashTransaction = apps.get_model("portfolio", "CashTransaction")

    try:
        account = Account.objects.get(
            name=DEFAULT_ACCOUNT_NAME
        )
    except Account.DoesNotExist:
        return

    Contribution.objects.filter(
        account=account
    ).update(account=None)

    CashTransaction.objects.filter(
        account=account
    ).update(account=None)

    account.delete()


class Migration(migrations.Migration):

    dependencies = [
        (
            "portfolio",
            "0003_account_cashtransaction_account_contribution_account",
        ),
    ]

    operations = [
        migrations.RunPython(
            populate_default_account,
            reverse_populate_default_account,
        ),
    ]
