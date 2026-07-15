from django.contrib import admin
from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView

from tradingcodex_service import viewer_api, web
from tradingcodex_service.api import api


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/viewer/", viewer_api.snapshot, name="viewer-snapshot"),
    path("api/viewer/skills/<str:skill_id>/", viewer_api.skill_detail, name="viewer-skill-detail"),
    path("api/viewer/artifacts/<path:artifact_id>/", viewer_api.artifact_detail, name="viewer-artifact-detail"),
    path("api/", api.urls),
    path("favicon.ico", RedirectView.as_view(url=static("tradingcodex_admin/favicon.svg"), permanent=False)),
    path("", web.spa_index, name="web-spa"),
]
