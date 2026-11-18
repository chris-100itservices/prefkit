"""URL routes. The HTML pages are for people; the /api/ routes are for the page's JavaScript."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from prefkit import views

prefkit_patterns = [
    path("", views.index, name="index"),
    # Old per-dataset address. It redirects to the single page.
    path("datasets/<slug:name>/", views.dataset_page, name="dataset"),
    path("api/active/", views.active_job, name="active-job"),
    path("api/datasets/<slug:name>/ingest/", views.start_ingest, name="start-ingest"),
    path("api/jobs/<uuid:job_id>/", views.job_status, name="job-status"),
    path("api/jobs/<uuid:job_id>/cancel/", views.cancel_job, name="cancel-job"),
    # Person labelling: the page, the next row to show, and saving a label.
    path("label/<slug:name>/", views.label_page, name="label"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
    path("api/label/<slug:name>/next/", views.next_for_person, name="label-next"),
    path("api/label/rows/<str:comparison_id>/", views.save_person_label, name="label-save"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    # Signing in and out, for the labelling page. Django's built-in views; the sign-in page
    # sends the person back to the page they came from.
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(next_page="prefkit:index"),
        name="login",
    ),
    path(
        "accounts/logout/",
        auth_views.LogoutView.as_view(next_page="prefkit:index"),
        name="logout",
    ),
    # Changing your own password. Django's built-in views; you must be signed in. The
    # templates are named here because the admin app has its own with the default names.
    path(
        "accounts/password/",
        auth_views.PasswordChangeView.as_view(template_name="prefkit/password_change.html"),
        name="password_change",
    ),
    path(
        "accounts/password/done/",
        auth_views.PasswordChangeDoneView.as_view(template_name="prefkit/password_change_done.html"),
        name="password_change_done",
    ),
    # The tuple form sets the "prefkit" namespace, so reverse("prefkit:job-status") works.
    path("", include((prefkit_patterns, "prefkit"))),
]
