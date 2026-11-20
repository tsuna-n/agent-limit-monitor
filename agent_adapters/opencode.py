"""OpenCode v2 project model, provider connection and Console usage limits."""

from pathlib import Path
import re
import subprocess
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit

from quota_monitor import Reading, obj, parse_json, percent, used_window

from .common import Verification, capture


_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:+@/-]{0,199}")
_PROVIDER_HOSTS = {
    "anthropic": {"api.anthropic.com"},
    "google": {"generativelanguage.googleapis.com"},
    "opencode": {"opencode.ai"},
    "opencode-go": {"opencode.ai"},
    "openrouter": {"openrouter.ai"},
}
_ENDPOINT_OVERRIDES = {"baseURL", "baseUrl", "base_url", "url", "endpoint"}


def api(cwd, deadline, route):
    remaining = deadline - monotonic()
    if remaining <= 0:
        return None
    directory = str(Path(cwd).resolve())
    location = urlencode({"location[directory]": directory})
    try:
        # Same configured server as the interactive CLI, with an explicit project location.
        # https://opencode.ai/docs/openapi.json
        result = capture(["opencode", "api", "GET", f"{route}?{location}"], cwd, remaining)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        payload = obj(parse_json(result.stdout))
        if obj(payload.get("location")).get("directory") == directory:
            return payload.get("data")
    return None


def selected_model(cwd, deadline):
    return obj(api(cwd, deadline, "/api/model/default"))


def selected_provider(cwd, deadline, provider_id):
    provider = obj(api(cwd, deadline, f"/api/provider/{quote(provider_id, safe='')}"))
    return provider if provider.get("id") == provider_id and provider.get("activation") in ("auto", "enabled") else {}


def custom_route(model, provider, provider_id):
    hosts = _PROVIDER_HOSTS.get(provider_id, set())
    for settings in (obj(provider.get("settings")), obj(model.get("settings"))):
        for key in _ENDPOINT_OVERRIDES & settings.keys():
            value = settings[key]
            if not isinstance(value, str):
                return True
            try:
                url = urlsplit(value)
                if (url.scheme != "https" or url.hostname not in hosts or url.port not in (None, 443)
                        or url.username is not None or url.password is not None):
                    return True
                if provider_id == "opencode-go" and not url.path.startswith("/inference/go/"):
                    return True
            except ValueError:
                return True
    return False


def active_connection(cwd, deadline, integration_id):
    integration = obj(api(cwd, deadline, f"/api/integration/{quote(integration_id, safe='')}"))
    connections = integration.get("connections")
    if integration.get("id") != integration_id or not isinstance(connections, list) or not connections:
        return None
    # OpenCode's active choice is the first connection in the integration response.
    active = obj(connections[0])
    kind = active.get("type")
    identifier = active.get("id" if kind == "credential" else "name")
    if kind in ("credential", "env") and isinstance(identifier, str) and identifier and active.get("status") is None:
        return kind, identifier
    return None


def configured_key(model, provider):
    return any("apiKey" in obj(route.get("settings")) for route in (model, provider))


def known_go_route(cwd, deadline, model, provider, connection=None):
    costs = model.get("cost")
    free = isinstance(costs, list) and costs and all(obj(cost).get("input") == 0 for cost in costs)
    if not provider or free or custom_route(model, provider, "opencode-go") or configured_key(model, provider):
        return False
    integration_id = provider.get("integrationID", "opencode-go")
    if integration_id == "opencode":
        return True
    if integration_id == "opencode-go":
        go_connection = connection or active_connection(cwd, deadline, "opencode-go")
        return go_connection is not None and go_connection == active_connection(cwd, deadline, "opencode")
    return False


def go_reading(data) -> Reading:
    go = obj(obj(data).get("go"))
    windows = []
    blocked = False
    # https://opencode.ai/docs/console/go/#usage-limits
    for key, label in (("rolling", "5-hour"), ("weekly", "Weekly"), ("monthly", "Monthly")):
        meter = obj(go.get(key))
        used = percent(meter.get("percent"))
        limited = meter.get("limited")
        reset = meter.get("resetsAt")
        if used is None or type(limited) is not bool or type(reset) not in (int, float):
            continue
        if not 0 < reset <= 253402300799000:
            continue
        window = used_window(label, used, int(reset / 1000))
        if window is not None:
            windows.append(window)
            blocked = blocked or limited
    if windows:
        return Reading(windows=tuple(windows), detail="OpenCode Go workspace allowance", blocked=blocked)
    return Reading(detail="OpenCode Go workspace allowance unavailable")


def probe(cwd, timeout) -> Reading:
    deadline = monotonic() + timeout
    model = selected_model(cwd, deadline)
    if not model:
        return Reading(detail="OpenCode project model unavailable")
    if model.get("providerID") != "opencode-go" or model.get("enabled") is not True:
        return Reading(detail="OpenCode has no matching quota source for the selected provider")
    provider = selected_provider(cwd, deadline, "opencode-go")
    if not known_go_route(cwd, deadline, model, provider):
        return Reading(detail="OpenCode Go quota could not be associated with the selected model")
    return go_reading(api(cwd, deadline, "/api/experimental/usage/limits"))


def verify(cwd, timeout) -> Verification:
    deadline = monotonic() + timeout
    model = selected_model(cwd, deadline)
    provider_id, model_id = model.get("providerID"), model.get("id")
    if not (isinstance(provider_id, str) and _ID.fullmatch(provider_id)
            and "/" not in provider_id and isinstance(model_id, str) and _ID.fullmatch(model_id)
            and model.get("enabled") is True):
        return Verification(False, "OpenCode project model could not be verified")

    provider = selected_provider(cwd, deadline, provider_id)
    if not provider:
        return Verification(False, "OpenCode selected provider could not be verified")
    if configured_key(model, provider):
        return Verification(False, "OpenCode project config sets a key whose authentication could not be verified")

    canonical = provider.get("canonical", provider_id)
    integration_id = provider.get("integrationID", provider_id)
    if not isinstance(integration_id, str) or not _ID.fullmatch(integration_id) or "/" in integration_id:
        return Verification(False, "OpenCode selected integration could not be verified")

    may_share = (provider_id not in _PROVIDER_HOSTS or not isinstance(canonical, str)
                 or canonical not in _PROVIDER_HOSTS or integration_id not in _PROVIDER_HOSTS
                 or custom_route(model, provider, provider_id))

    connection = active_connection(cwd, deadline, integration_id)
    if connection is None:
        return Verification(False, "OpenCode selected provider has no verified active connection")

    if provider_id == "opencode-go" and known_go_route(cwd, deadline, model, provider, connection):
        reading = go_reading(api(cwd, deadline, "/api/experimental/usage/limits"))
        if reading.blocked or any(window.remaining <= 0 for window in reading.windows):
            return Verification(False, "OpenCode Go reports an exhausted allowance")

    return Verification(True, "OpenCode selected provider reports an active connection",
                        model=f"{provider_id}/{model_id}", shared_codex_possible=may_share)
