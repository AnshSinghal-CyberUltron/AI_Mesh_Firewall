from django.urls import path

from .notification_views import NotificationMarkReadView, NotificationsListView

urlpatterns = [
    path("", NotificationsListView.as_view(), name="notifications-list"),
    path("<int:pk>/read/", NotificationMarkReadView.as_view(), name="notification-mark-read"),
    path("mark-all-read/", NotificationMarkReadView.as_view(), name="notifications-mark-all-read"),
]
