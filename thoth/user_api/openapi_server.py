#!/usr/bin/env python3
# Stub
# Copyright(C) 2019, 2020 Christoph Görn
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Thoth User API entrypoint."""


import os
import sys
import logging
from datetime import datetime
from typing import List

import connexion
from connexion.resolver import RestyResolver

from flask import redirect, jsonify, request
from flask_script import Manager
from prometheus_flask_exporter import PrometheusMetrics
from flask_cors import CORS


from thoth.common import __version__ as __common__version__
from thoth.common import datetime2datetime_str
from thoth.common import init_logging
from thoth.storages import __version__ as __storages__version__
from thoth.python import __version__ as __python__version__
from thoth.messaging import __version__ as __messaging__version__
from thoth.storages import GraphDatabase
from thoth.storages.exceptions import DatabaseNotInitialized
from thoth.user_api import __version__
from thoth.user_api.configuration import Configuration


# Configure global application logging using Thoth's init_logging.
init_logging(logging_env_var_start="THOTH_USER_API_LOG_")

_LOGGER = logging.getLogger("thoth.user_api")
_LOGGER.setLevel(logging.DEBUG if bool(int(os.getenv("THOTH_USER_API_DEBUG", 0))) else logging.INFO)

__service_version__ = (
    f"{__version__}+"
    f"messaging.{__messaging__version__}.storages.{__storages__version__}."
    f"common.{__common__version__}.python.{__python__version__}"
)


_LOGGER.info(f"This is User API v%s", __service_version__)
_LOGGER.debug("DEBUG mode is enabled!")

_THOTH_API_HTTPS = bool(int(os.getenv("THOTH_API_HTTPS", 1)))

# Expose for uWSGI.
app = connexion.FlaskApp(__name__, specification_dir=Configuration.SWAGGER_YAML_PATH, debug=True)

# Add Cross Origin Request Policy to all
CORS(app.app)

app.add_api(
    "openapi.yaml",
    options={"swagger_ui": True},
    arguments={"title": "User API"},
    resolver=RestyResolver(default_module_name="thoth.user_api.api_v1"),
    strict_validation=True,
    validate_responses=False,
)


application = app.app

# create metrics and manager
metrics = PrometheusMetrics(
    application,
    group_by="endpoint",
    excluded_paths=[
        "/liveness",
        "/readiness",
        "/api/v1/ui",
        "/api/v1/openapi",
    ],
)
manager = Manager(application)

# Needed for session.
application.secret_key = Configuration.APP_SECRET_KEY

# static information as metric
metrics.info("user_api_info", "User API info", version=__service_version__)
_API_GAUGE_METRIC = metrics.info("user_api_schema_up2date", "User API schema up2date")


class _GraphDatabaseWrapper:
    """A wrapper for lazy graph database adapter handling."""

    _graph = GraphDatabase()

    def __getattr__(self, item):
        """Connect to the database lazily on first call."""
        if not self._graph.is_connected():
            self._graph.connect()

        return getattr(self._graph, item)


# Instantiate one GraphDatabase adapter in the whole application (one per wsgi worker) to correctly
# reuse connection pooling from one instance. Any call to this wrapper has to be done after the wsgi fork
# (hence the wrapper logic).
GRAPH = _GraphDatabaseWrapper()


@application.before_request
def before_request_callback():
    """Register this callback, so it is run before each request to this service."""
    method = request.method
    path = request.path

    # Update up2date metric exposed.
    if method == "GET" and path == "/metrics":
        try:
            _API_GAUGE_METRIC.set(int(GRAPH.is_schema_up2date()))
        except DatabaseNotInitialized as exc:
            # This can happen if database is erased after the service has been started as we
            # have passed readiness probe with this check.
            _LOGGER.exception("Cannot determine database schema as database is not initialized: %s", str(exc))
            _API_GAUGE_METRIC.set(0)


@app.route("/")
def base_url():
    """Redirect to UI by default."""
    # https://github.com/pallets/flask/issues/773
    request.environ["wsgi.url_scheme"] = "https" if _THOTH_API_HTTPS else "http"
    return redirect("api/v1/ui/")


def _list_registered_paths() -> List[str]:
    """List available paths registerd to this service."""
    paths = []
    for rule in application.url_map.iter_rules():
        rule = str(rule)
        if rule.startswith("/api/v1"):
            paths.append(rule)

    return paths


@app.route("/api/v1")
def api_v1():
    """Provide a listing of all available endpoints."""
    return jsonify({"paths": _list_registered_paths()})


def _healthiness():
    return jsonify({"status": "ready", "version": __service_version__}), 200, {"ContentType": "application/json"}


@app.route("/readiness")
def api_readiness():
    """Report readiness for OpenShift readiness probe."""
    if "/api/v1/advise/python" not in _list_registered_paths():
        raise RuntimeError("Advise endpoint was not registered, service not ready")
    return _healthiness()


@app.route("/liveness")
def api_liveness():
    """Report liveness for OpenShift readiness probe."""
    return _healthiness()


@application.errorhandler(404)
@metrics.do_not_track()
def page_not_found(exc):
    """Adjust 404 page to be consistent with errors reported back from API."""
    # Flask has a nice error message - reuse it.
    return jsonify({"error": str(exc)}), 404


@application.errorhandler(500)
@metrics.do_not_track()
def internal_server_error(exc):
    """Adjust 500 page to be consistent with errors reported back from API."""
    # Provide some additional information so we can easily find exceptions in logs (time and exception type).
    # Later we should remove exception type (for security reasons).
    return (
        jsonify(
            {
                "error": "Internal server error occurred, please contact administrator with provided details.",
                "details": {"type": exc.__class__.__name__, "datetime": datetime2datetime_str(datetime.utcnow())},
            }
        ),
        500,
    )


@application.after_request
def apply_headers(response):
    """Add headers to each response."""
    response.headers["X-Thoth-Version"] = __version__
    response.headers["X-User-API-Service-Version"] = __service_version__
    return response


if __name__ == "__main__":
    app.run()

    sys.exit(1)
