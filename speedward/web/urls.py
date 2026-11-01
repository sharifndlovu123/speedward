from django.urls import path, re_path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("estimate", views.estimate, name="estimate"),
    path("scan", views.scan, name="scan"),
    path("scan/<str:scan_id>/status", views.scan_status, name="scan_status"),
    path("render", views.render_result, name="render"),
    re_path(r"^export/(?P<fmt>json|md|html)$", views.export, name="export"),
]
