"""
Deploy-path tests: infra/docker-compose.yml, infra/nginx/nginx.conf, deploy.sh.

Static checks against the files' text/YAML rather than a live docker daemon —
same reasoning as the schema-drift tests: no Docker required to run pytest,
and a config typo is caught here instead of after a multi-minute build.
"""

from __future__ import annotations

import stat
import subprocess

import yaml

from app.config import REPO_ROOT

COMPOSE_PATH = REPO_ROOT / "infra" / "docker-compose.yml"
NGINX_CONF_PATH = REPO_ROOT / "infra" / "nginx" / "nginx.conf"
DEPLOY_SH_PATH = REPO_ROOT / "deploy.sh"

PROXY_PORT = "5082"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


# --- docker-compose.yml ----------------------------------------------------


def test_compose_file_is_valid_yaml_with_the_expected_services():
    assert set(_compose()["services"]) == {"postgres", "backend", "frontend", "nginx"}


def test_only_nginx_is_published_beyond_loopback():
    """postgres/backend/frontend must stay off the network — nginx on
    PROXY_PORT is the one port meant to be reachable from another machine."""
    compose = _compose()
    for name in ("postgres", "backend", "frontend"):
        ports = compose["services"][name]["ports"]
        assert all(p.startswith("127.0.0.1:") for p in ports), (
            f"{name} publishes a non-loopback port: {ports}"
        )
    assert _compose()["services"]["nginx"]["ports"] == [f"{PROXY_PORT}:80"]


def test_nginx_mounts_its_config_with_the_selinux_relabel_flag():
    """Without the :z relabel, Fedora/SELinux bind-mounts fail with EACCES
    and nginx never boots — regression test for exactly that failure."""
    volumes = _compose()["services"]["nginx"]["volumes"]
    assert any(v.endswith(":z") or v.endswith(",z") for v in volumes), volumes


def test_frontend_calls_the_api_through_the_proxy_same_origin():
    env = _compose()["services"]["frontend"]["environment"]
    assert env["VITE_API_BASE_URL"] == "/api"


def test_backend_allows_the_proxied_origin():
    env = _compose()["services"]["backend"]["environment"]
    assert f":{PROXY_PORT}" in env["ALLOWED_ORIGINS"]


# --- nginx.conf --------------------------------------------------------


def test_nginx_conf_proxies_api_to_backend_and_everything_else_to_frontend():
    text_ = NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert "location /api/ {" in text_
    assert "proxy_pass http://backend:8000/;" in text_
    assert "proxy_pass http://frontend:5173/;" in text_


def test_nginx_conf_listen_port_matches_the_compose_target_port():
    container_port = _compose()["services"]["nginx"]["ports"][0].split(":")[1]
    text_ = NGINX_CONF_PATH.read_text(encoding="utf-8")
    assert f"listen {container_port};" in text_


# --- deploy.sh -----------------------------------------------------------


def test_deploy_sh_exists_and_is_executable():
    mode = DEPLOY_SH_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, "deploy.sh must be chmod +x"


def test_deploy_sh_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SH_PATH)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_deploy_sh_targets_the_compose_file_and_proxy_port():
    text_ = DEPLOY_SH_PATH.read_text(encoding="utf-8")
    assert "infra/docker-compose.yml" in text_
    assert f"PROXY_PORT={PROXY_PORT}" in text_
    assert "set -euo pipefail" in text_


def test_deploy_sh_fails_loudly_when_host_ip_cannot_be_detected():
    """A box with no default route (or a broken `ip` call) must error out,
    not silently deploy something nobody can reach."""
    text_ = DEPLOY_SH_PATH.read_text(encoding="utf-8")
    assert "Could not auto-detect" in text_
    assert "exit 1" in text_
