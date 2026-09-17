from django.urls import path

from . import views


app_name = "portfolio"

urlpatterns = [
    path(
        "",
        views.dashboard,
        name="dashboard",
    ),
    path(
        (
            "accounts/"
            "<int:account_id>/"
            "settings/"
        ),
        views.account_settings,
        name="account_settings",
    ),
    path(
        "holdings/<str:ticker>/",
        views.holding_detail,
        name="holding_detail",
    ),
    path(
        (
            "accounts/"
            "<int:account_id>/"
            "recommendations/"
            "<int:sequence_number>/"
        ),
        views.recommendation_review,
        name="recommendation_review",
    ),
    path(
        (
            "accounts/"
            "<int:account_id>/"
            "recommendations/"
            "<int:sequence_number>/"
            "<int:plan_order>/execute/"
        ),
        views.recommendation_execute,
        name="recommendation_execute",
    ),
    path(
        (
            "accounts/"
            "<int:account_id>/"
            "recommendations/"
            "<int:sequence_number>/approve/"
        ),
        views.recommendation_approve,
        name="recommendation_approve",
    ),
]
