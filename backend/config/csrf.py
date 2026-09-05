from django.http import JsonResponse


def csrf_failure(_request, reason=""):
    return JsonResponse(
        {
            "detail": (
                "CSRF check failed: "
                f"{reason or 'token missing or origin not allowed.'}"
            )
        },
        status=403,
    )
