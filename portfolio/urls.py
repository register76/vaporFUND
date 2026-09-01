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
        "recommendations/<int:sequence_number>/",
        views.recommendation_review,
        name="recommendation_review",
    ),
]
