"""URL routes. The HTML page is for people; the /api/ routes are for the AJAX client."""

from django.contrib import admin
from django.urls import include, path

from prefkit import views

prefkit_patterns = [
    path("datasets/<slug:name>/", views.dataset_page, name="dataset"),
    path("api/datasets/<slug:name>/ingest/", views.start_ingest, name="start-ingest"),
    path("api/jobs/<uuid:job_id>/", views.job_status, name="job-status"),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    # The tuple form sets the "prefkit" namespace, so reverse("prefkit:job-status") works.
    path("", include((prefkit_patterns, "prefkit"))),
]
