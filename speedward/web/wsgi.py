import os

from django.core.wsgi import get_wsgi_application

from speedward.core.config import build_environment

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "speedward.web.settings")
if os.environ["DJANGO_SETTINGS_MODULE"] == "speedward.web.settings":
    from speedward.web.settings import require_secret_key

    require_secret_key(build_environment())  # fail clearly at start, not on the first request
application = get_wsgi_application()
