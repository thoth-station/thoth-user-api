#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# thoth-user-api
# Copyright(C) 2018 Fridolin Pokorny
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

import logging

import requests

from flask import redirect, jsonify
import connexion
from flask_script import Manager

import prometheus_client
from prometheus_flask_exporter import PrometheusMetrics

from thoth.common import SafeJSONEncoder
from thoth.common import init_logging
import thoth_user_api

from .configuration import Configuration

# Expose for uWSGI.
app = connexion.App(__name__)
application = app.app
init_logging()
_LOGGER = logging.getLogger('thoth.result_api')

app.add_api(Configuration.SWAGGER_YAML_PATH)
application.json_encoder = SafeJSONEncoder
manager = Manager(application)

# Needed for session.
application.secret_key = Configuration.APP_SECRET_KEY

metrics = PrometheusMetrics(app)
metrics.info('app_info', 'Thoth User API', version=thoth_user_api.__version__)


@app.route('/')
def base_url():
    # Be nice with user access
    return redirect('api/v1/ui')


@app.route('/api/v1')
def api_v1():
    paths = []

    for rule in application.url_map.iter_rules():
        rule = str(rule)
        if rule.startswith('/api/v1'):
            paths.append(rule)

    return jsonify({'paths': paths})


@app.route('/readiness')
def api_readiness():
    return jsonify({'status': 'ready', 'version': thoth_user_api.__version__}), 200, {'ContentType': 'application/json'}


@app.route('/liveness')
def api_liveness():
    response = requests.get(Configuration.KUBERNETES_API_URL,
                            verify=Configuration.KUBERNETES_VERIFY_TLS)
    response.raise_for_status()
    return jsonify(None)


if __name__ == '__main__':
    _LOGGER.info(f'Thoth User API v{thoth_user_api.__version__}')

    manager.run()
