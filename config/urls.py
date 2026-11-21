from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from monografias.views import MonografiaViewSet


def health(request):
    status = {}
    try:
        connection.ensure_connection()
        status["db"] = "ok"
    except Exception:
        status["db"] = "erro"
    try:
        cache.set("health", "1", 5)
        status["redis"] = "ok"
    except Exception:
        status["redis"] = "erro"
    code = 200 if all(v == "ok" for v in status.values()) else 503
    return JsonResponse(status, status=code)


router = DefaultRouter()
router.register("monografias", MonografiaViewSet, basename="monografia")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health),
    path("api/", include(router.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
]