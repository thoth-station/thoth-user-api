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

"""Common library-wide utilities."""

import os
import logging
import requests
from functools import wraps

from .configuration import Configuration

_LOGGER = logging.getLogger('thoth.user_api.utils')
_RSYSLOG_HOST = os.getenv('RSYSLOG_HOST')
_RSYSLOG_PORT = os.getenv('RSYSLOG_PORT')


def _set_env_var(env_config: list, name: str, value: str) -> None:
    """Overwrite env variable configuration if already exists in configuration, otherwise append it."""
    for item in env_config:
        if item['name'] == name:
            item['value'] = str(value)
            item.pop('valueFrom', None)
            break
    else:
        env_config.append({
            'name': name,
            'value': str(value)
        })


def _do_run_pod(template: dict, namespace: str) -> str:
    """Run defined template in Kubernetes."""
    # We don't care about secret as we run inside the cluster. All builds should hard-code it to secret.
    endpoint = "{}/api/v1/namespaces/{}/pods".format(Configuration.KUBERNETES_API_URL,
                                                     namespace)
    _LOGGER.debug("Sending POST request to Kubernetes master %r", Configuration.KUBERNETES_API_URL)
    response = requests.post(
        endpoint,
        headers={
            'Authorization': 'Bearer {}'.format(Configuration.KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        },
        json=template,
        verify=Configuration.KUBERNETES_VERIFY_TLS
    )
    _LOGGER.debug("Kubernetes master response (%d) from %r: %r",
                  response.status_code, Configuration.KUBERNETES_API_URL, response.text)
    if response.status_code / 100 != 2:
        _LOGGER.error(response.text)
    response.raise_for_status()

    if _RSYSLOG_HOST:
        # We use only one container per pod.
        _set_env_var(template['spec']['containers'][0]['env'], 'RSYSLOG_HOST', _RSYSLOG_HOST)
        _set_env_var(template['spec']['containers'][0]['env'], 'RSYSLOG_PORT', _RSYSLOG_PORT)

    return response.json()['metadata']['name']


def run_analyzer(image: str, analyzer: str, debug: bool=False, timeout: int=None,
                 cpu_request: str=None, memory_request: str=None,
                 registry_user: str=None, registry_password: str=None, tls_verify: bool=True) -> str:
    """Run an analyzer for the given image."""
    name_prefix = "{}-{}".format(analyzer, image.rsplit('/', maxsplit=1)[-1]).replace(':', '-').replace('/', '-')
    template = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "generateName": name_prefix + '-',
            "namespace": Configuration.THOTH_MIDDLETIER_NAMESPACE,
            "labels": {
                "thothtype": "userpod",
                "thothpod": "analyzer"
            }
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [{
                "name": analyzer.rsplit('/', maxsplit=1)[-1],
                "image": analyzer,
                "livenessProbe": {
                    "tcpSocket": {
                        "port": 8080
                    },
                    "initialDelaySeconds": Configuration.THOTH_ANALYZER_HARD_TIMEOUT,
                    "failureThreshold": 1,
                    "periodSeconds": 10
                },
                "env": [
                    {"name": "THOTH_ANALYZED_IMAGE", "value": str(image)},
                    {"name": "THOTH_ANALYZER", "value": str(analyzer)},
                    {"name": "THOTH_ANALYZER_DEBUG", "value": str(int(debug))},
                    {"name": "THOTH_ANALYZER_TIMEOUT", "value": str(timeout or 0)},
                    {"name": "THOTH_ANALYZER_OUTPUT", "value": Configuration.THOTH_ANALYZER_OUTPUT},
                    {"name": "THOTH_ANALYZER_NO_TLS_VERIFY", "value": str(int(not tls_verify))}
                ],
                "resources": {
                    "limits": {
                        "memory": Configuration.THOTH_MIDDLETIER_POD_MEMORY_LIMIT,
                        "cpu": Configuration.THOTH_MIDDLETIER_POD_CPU_LIMIT
                    },
                    "requests": {
                        "memory": memory_request or Configuration.THOTH_MIDDLETIER_POD_MEMORY_REQUEST,
                        "cpu": cpu_request or Configuration.THOTH_MIDDLETIER_POD_CPU_REQUEST
                    }
                }
            }]
        }
    }

    if bool(registry_user) + bool(registry_password) == 1:
        raise ValueError('Please specify both registry user and password in order to use registry authentication.')

    if registry_user and registry_password:
        _set_env_var(
            template['spec']['containers'][0]['env'],
            "THOTH_REGISTRY_CREDENTIALS",
            f"{registry_user}:{registry_password}"
        )

    _LOGGER.debug("Requesting to run analyzer %r with payload %s", analyzer, template)
    return _do_run_pod(template, Configuration.THOTH_MIDDLETIER_NAMESPACE)


def run_solver(solver: str, packages: str, debug: bool=False, transitive: bool=True,
               cpu_request: str=None, memory_request: str=None) -> str:
    """Run a solver for the given packages."""
    name_prefix = "{}-{}".format(solver, solver.rsplit('/', maxsplit=1)[-1]).replace(':', '-').replace('/', '-')
    template = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "generateName": name_prefix + '-',
            "namespace": Configuration.THOTH_MIDDLETIER_NAMESPACE,
            "labels": {
                "thothtype": "userpod",
                "thothpod": "analyzer"
            }
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [{
                "name": solver.rsplit('/', maxsplit=1)[-1],
                "image": solver,
                "livenessProbe": {
                    "tcpSocket": {
                        "port": 80
                    },
                    "initialDelaySeconds": Configuration.THOTH_ANALYZER_HARD_TIMEOUT,
                    "failureThreshold": 1,
                    "periodSeconds": 10
                },
                "env": [
                    {"name": "THOTH_SOLVER", "value": str(solver)},
                    {"name": "THOTH_SOLVER_NO_TRANSITIVE", "value": str(int(not transitive))},
                    {"name": "THOTH_SOLVER_PACKAGES", "value": str(packages.replace('\n', '\\n'))},
                    {"name": "THOTH_SOLVER_DEBUG", "value": str(int(debug))},
                    {"name": "THOTH_SOLVER_OUTPUT", "value": Configuration.THOTH_SOLVER_OUTPUT}
                ],
                "resources": {
                    "limits": {
                        "memory": Configuration.THOTH_MIDDLETIER_POD_MEMORY_LIMIT,
                        "cpu": Configuration.THOTH_MIDDLETIER_POD_CPU_LIMIT
                    },
                    "requests": {
                        "memory": memory_request or Configuration.THOTH_MIDDLETIER_POD_MEMORY_REQUEST,
                        "cpu": cpu_request or Configuration.THOTH_MIDDLETIER_POD_CPU_REQUEST
                    }
                }
            }]
        }
    }

    _LOGGER.debug("Requesting to run solver %r with payload %s", solver, template)
    return _do_run_pod(template, Configuration.THOTH_MIDDLETIER_NAMESPACE)


def run_adviser(packages: str, debug: bool=False, packages_only: bool=False) -> str:
    """Request to run adviser in the backend part."""
    template = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "generateName": 'fridex-thoth-adviser-',
            "namespace": Configuration.THOTH_BACKEND_NAMESPACE,
            "labels": {
                "thothpod": "analyzer"
            }
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [{
                "name": "thoth-adviser",
                "image": "fridex/thoth-adviser",
                "livenessProbe": {
                    "tcpSocket": {
                        "port": 80
                    },
                    "initialDelaySeconds": Configuration.THOTH_ANALYZER_HARD_TIMEOUT,
                    "failureThreshold": 1,
                    "periodSeconds": 10
                },
                "env": [
                    {"name": "THOTH_ADVISER_PACKAGES", "value": str(packages.replace('\n', '\\n'))},
                    {"name": "THOTH_ADVISER_DEBUG", "value": str(int(debug))},
                    {"name": "THOTH_ADVISER_PACKAGES_ONLY", "value": str(int(packages_only))},
                    {"name": "THOTH_ADVISER_OUTPUT", "value": Configuration.THOTH_ADVISER_OUTPUT}
                ],
            }]
        }
    }

    _LOGGER.debug("Requesting to run adviser with payload %s", template)
    return _do_run_pod(template, Configuration.THOTH_BACKEND_NAMESPACE)


def run_sync(sync_observations: bool=False, *,
             force_analysis_results_sync: bool=False, force_solver_results_sync: bool=False):
    """Run a graph sync."""
    # Let's reuse pod definition from the cronjob definition so any changes in deployed application work out of the box.
    cronjob_def = get_cronjob('graph-sync')
    pod_spec = cronjob_def['spec']['jobTemplate']['spec']['template']['spec']

    # We silently assume that the first container is actually the syncing container.
    # We need to assign values that are passed from configmaps explicitly.
    # TODO: get rid of this once we will use custom objects.
    env = pod_spec['containers'][0]['env']
    _set_env_var(env, 'THOTH_SYNC_OBSERVATIONS', str(int(sync_observations)))
    _set_env_var(env, 'THOTH_GRAPH_SYNC_FORCE_ANALYSIS_RESULTS_SYNC', str(int(force_analysis_results_sync)))
    _set_env_var(env, 'THOTH_GRAPH_SYNC_FORCE_SOLVER_RESULTS_SYNC', str(int(force_solver_results_sync)))
    _set_env_var(env, 'THOTH_MIDDLETIER_NAMESPACE', Configuration.THOTH_MIDDLETIER_NAMESPACE)
    _set_env_var(env, 'THOTH_DEPLOYMENT_NAME', os.environ['THOTH_DEPLOYMENT_NAME'])
    _set_env_var(env, 'THOTH_S3_ENDPOINT_URL ', os.environ['THOTH_S3_ENDPOINT_URL '])
    _set_env_var(env, 'THOTH_CEPH_BUCKET', os.environ['THOTH_CEPH_BUCKET'])
    _set_env_var(env, 'THOTH_CEPH_BUCKET_PREFIX', os.environ['THOTH_CEPH_BUCKET_PREFIX'])
    _set_env_var(env, 'THOTH_CEPH_KEY_ID', os.environ['THOTH_CEPH_KEY_ID'])
    _set_env_var(env, 'THOTH_CEPH_SECRET_KEY', os.environ['THOTH_CEPH_SECRET_KEY'])

    template = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "generateName": 'graph-sync-',
            "namespace": Configuration.THOTH_BACKEND_NAMESPACE,
            "labels": {
                "thothtype": "userpod",
                "thothpod": "pod",
                "name": "thoth-graph-sync"
            }
        },
        "spec": pod_spec
    }
    _LOGGER.debug("Requesting to run graph sync")
    return _do_run_pod(template, Configuration.THOTH_BACKEND_NAMESPACE)


def get_pod_log(pod_id: str) -> str:
    """Get log of a pod based on assigned pod ID."""
    endpoint = "{}/api/v1/namespaces/{}/pods/{}/log".format(Configuration.KUBERNETES_API_URL,
                                                            Configuration.THOTH_MIDDLETIER_NAMESPACE,
                                                            pod_id)
    response = requests.get(
        endpoint,
        headers={
            'Authorization': 'Bearer {}'.format(Configuration.KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        },
        verify=Configuration.KUBERNETES_VERIFY_TLS
    )
    _LOGGER.debug("Kubernetes master response for pod log (%d): %r", response.status_code, response.text)
    if response.status_code / 100 != 2:
        _LOGGER.error(response.text)
    response.raise_for_status()

    return response.text


def get_pod_status(pod_id: str) -> dict:
    """Get status entry for a pod."""
    endpoint = "{}/api/v1/namespaces/{}/pods/{}".format(Configuration.KUBERNETES_API_URL,
                                                        Configuration.THOTH_MIDDLETIER_NAMESPACE,
                                                        pod_id)
    response = requests.get(
        endpoint,
        headers={
            'Authorization': 'Bearer {}'.format(Configuration.KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        },
        verify=Configuration.KUBERNETES_VERIFY_TLS
    )
    _LOGGER.debug("Kubernetes master response for pod status (%d): %r", response.status_code, response.text)
    if response.status_code / 100 != 2:
        _LOGGER.error(response.text)
    response.raise_for_status()
    return response.json()['status']['containerStatuses'][0]['state']


def get_cronjob(cronjob_name: str) -> dict:
    """Retrieve a cron job based on its name."""
    endpoint = '{}/apis/batch/v2alpha1/namespaces/{}/cronjobs/{}'.format(Configuration.KUBERNETES_API_URL,
                                                                         Configuration.THOTH_BACKEND_NAMESPACE,
                                                                         cronjob_name)
    response = requests.get(
        endpoint,
        headers={
            'Authorization': 'Bearer {}'.format(Configuration.KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        },
        verify=Configuration.KUBERNETES_VERIFY_TLS
    )
    _LOGGER.debug("Kubernetes master response for cronjob query with HTTP status code %d", response.status_code)
    if 200 <= response.status_code <= 399:
        _LOGGER.error(response.text)
    response.raise_for_status()
    return response.json()


def logger_warning(function):
    """Decorator function which sets the logger to warning only mode."""
    @wraps(function)
    def wrapper_func():
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        return function()

    return wrapper_func


def logger_info(function):
    """Decorator function which sets the logger to info mode to print all the contents."""
    @wraps(function)
    def wrapper_func(*args, **kwargs):
        logging.getLogger('werkzeug').setLevel(logging.INFO)
        return function(*args, **kwargs)

    return wrapper_func
