from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from tradingcodex_service.application.common import local_or_staff_source
from tradingcodex_service.runtime_profile import LOCAL_PROFILE


@require_GET
@ensure_csrf_cookie
def spa_index(request: HttpRequest) -> HttpResponse:
    if not local_or_staff_source(request, allow_local_readonly=settings.SERVICE_PROFILE == LOCAL_PROFILE):
        return HttpResponseForbidden("TradingCodex web is local or staff only.")
    index = finders.find("tradingcodex_web/index.html")
    if not index:
        return HttpResponse("TradingCodex web build is unavailable.", status=503)
    return HttpResponse(Path(index).read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
