from django.shortcuts import redirect
from django.urls import reverse


class LoginRequiredMiddleware:
    """Require an authenticated user for the application while preserving admin login."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            not request.user.is_authenticated
            and not request.path.startswith("/login/")
            and not request.path.startswith("/admin/")
            and not request.path.startswith("/static/")
            and not request.path.startswith("/unsubscribe/")
        ):
            return redirect(f"{reverse('login')}?next={request.get_full_path()}")
        return self.get_response(request)