"""Headers Django's own settings do not cover."""

# 'unsafe-inline' styles: the map SVG carries its own <style> and the stage bars use inline widths.
# Scripts are external files only (no inline script), so script-src stays 'self'.
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "script-src 'self'; frame-ancestors 'none'")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.get_host()  # Django validates Host lazily; do it for every request so a forged Host is a 400
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CSP)
        response.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        return response
