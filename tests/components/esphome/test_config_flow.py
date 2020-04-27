"""Test config flow."""
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.components.esphome import DATA_KEY, config_flow
from homeassistant.data_entry_flow import (
    RESULT_TYPE_ABORT,
    RESULT_TYPE_FORM,
    RESULT_TYPE_CREATE_ENTRY,
)

from tests.common import MockConfigEntry, mock_coro

MockDeviceInfo = namedtuple("DeviceInfo", ["uses_password", "name"])


@pytest.fixture
def mock_client():
    """Mock APIClient."""
    with patch("homeassistant.components.esphome.config_flow.APIClient") as mock_client:

        def mock_constructor(loop, host, port, password):
            """Fake the client constructor."""
            mock_client.host = host
            mock_client.port = port
            mock_client.password = password
            return mock_client

        mock_client.side_effect = mock_constructor
        mock_client.connect.return_value = mock_coro()
        mock_client.disconnect.return_value = mock_coro()

        yield mock_client


@pytest.fixture(autouse=True)
def mock_api_connection_error():
    """Mock out the try login method."""
    with patch(
        "homeassistant.components.esphome.config_flow.APIConnectionError",
        new_callable=lambda: OSError,
    ) as mock_error:
        yield mock_error


def _setup_flow_handler(hass):
    flow = config_flow.EsphomeFlowHandler()
    flow.hass = hass
    flow.context = {}
    return flow


async def test_user_connection_works(hass, mock_client):
    """Test we can finish a config flow."""
    flow = _setup_flow_handler(hass)
    result = await flow.async_step_user(user_input=None)
    assert result["type"] == RESULT_TYPE_FORM

    mock_client.device_info.return_value = mock_coro(MockDeviceInfo(False, "test"))

    result = await flow.async_step_user(user_input={"host": "127.0.0.1", "port": 80})

    assert result["type"] == RESULT_TYPE_CREATE_ENTRY
    assert result["data"] == {"host": "127.0.0.1", "port": 80, "password": ""}
    assert result["title"] == "test"
    assert len(mock_client.connect.mock_calls) == 1
    assert len(mock_client.device_info.mock_calls) == 1
    assert len(mock_client.disconnect.mock_calls) == 1
    assert mock_client.host == "127.0.0.1"
    assert mock_client.port == 80
    assert mock_client.password == ""


async def test_user_resolve_error(hass, mock_api_connection_error, mock_client):
    """Test user step with IP resolve error."""
    flow = _setup_flow_handler(hass)
    await flow.async_step_user(user_input=None)

    class MockResolveError(mock_api_connection_error):
        """Create an exception with a specific error message."""

        def __init__(self):
            """Initialize."""
            super().__init__("Error resolving IP address")

    with patch(
        "homeassistant.components.esphome.config_flow.APIConnectionError",
        new_callable=lambda: MockResolveError,
    ) as exc:
        mock_client.device_info.side_effect = exc
        result = await flow.async_step_user(
            user_input={"host": "127.0.0.1", "port": 6053}
        )

    assert result["type"] == RESULT_TYPE_FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "resolve_error"}
    assert len(mock_client.connect.mock_calls) == 1
    assert len(mock_client.device_info.mock_calls) == 1
    assert len(mock_client.disconnect.mock_calls) == 1


async def test_user_connection_error(hass, mock_api_connection_error, mock_client):
    """Test user step with connection error."""
    flow = _setup_flow_handler(hass)
    await flow.async_step_user(user_input=None)

    mock_client.device_info.side_effect = mock_api_connection_error

    result = await flow.async_step_user(user_input={"host": "127.0.0.1", "port": 6053})

    assert result["type"] == RESULT_TYPE_FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "connection_error"}
    assert len(mock_client.connect.mock_calls) == 1
    assert len(mock_client.device_info.mock_calls) == 1
    assert len(mock_client.disconnect.mock_calls) == 1


async def test_user_with_password(hass, mock_client):
    """Test user step with password."""
    flow = _setup_flow_handler(hass)
    await flow.async_step_user(user_input=None)

    mock_client.device_info.return_value = mock_coro(MockDeviceInfo(True, "test"))

    result = await flow.async_step_user(user_input={"host": "127.0.0.1", "port": 6053})

    assert result["type"] == RESULT_TYPE_FORM
    assert result["step_id"] == "authenticate"

    result = await flow.async_step_authenticate(user_input={"password": "password1"})

    assert result["type"] == RESULT_TYPE_CREATE_ENTRY
    assert result["data"] == {
        "host": "127.0.0.1",
        "port": 6053,
        "password": "password1",
    }
    assert mock_client.password == "password1"


async def test_user_invalid_password(hass, mock_api_connection_error, mock_client):
    """Test user step with invalid password."""
    flow = _setup_flow_handler(hass)
    await flow.async_step_user(user_input=None)

    mock_client.device_info.return_value = mock_coro(MockDeviceInfo(True, "test"))

    await flow.async_step_user(user_input={"host": "127.0.0.1", "port": 6053})
    mock_client.connect.side_effect = mock_api_connection_error
    result = await flow.async_step_authenticate(user_input={"password": "invalid"})

    assert result["type"] == RESULT_TYPE_FORM
    assert result["step_id"] == "authenticate"
    assert result["errors"] == {"base": "invalid_password"}


async def test_discovery_initiation(hass, mock_client):
    """Test discovery importing works."""
    mock_client.device_info.return_value = mock_coro(MockDeviceInfo(False, "test8266"))

    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {},
    }

    flow = await hass.config_entries.flow.async_init(
        "esphome", context={"source": "zeroconf"}, data=service_info
    )

    assert flow["type"] == RESULT_TYPE_FORM
    assert flow["step_id"] == "discovery_confirm"
    assert flow["description_placeholders"]["name"] == "test8266"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input={}
    )

    assert result["type"] == RESULT_TYPE_CREATE_ENTRY
    assert result["title"] == "test8266"
    assert result["data"]["host"] == "test8266.local"
    assert result["data"]["port"] == 6053

    assert result["result"]
    assert result["result"].unique_id == "test8266"


async def test_discovery_already_configured_hostname(hass, mock_client):
    """Test discovery aborts if already configured via hostname."""
    MockConfigEntry(
        domain="esphome", data={"host": "test8266.local", "port": 6053, "password": ""}
    ).add_to_hass(hass)

    flow = _setup_flow_handler(hass)
    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {},
    }
    result = await flow.async_step_zeroconf(user_input=service_info)
    assert result["type"] == RESULT_TYPE_ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_already_configured_ip(hass, mock_client):
    """Test discovery aborts if already configured via static IP."""
    MockConfigEntry(
        domain="esphome", data={"host": "192.168.43.183", "port": 6053, "password": ""}
    ).add_to_hass(hass)

    flow = _setup_flow_handler(hass)
    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {"address": "192.168.43.183"},
    }
    result = await flow.async_step_zeroconf(user_input=service_info)
    assert result["type"] == RESULT_TYPE_ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_already_configured_name(hass, mock_client):
    """Test discovery aborts if already configured via name."""
    entry = MockConfigEntry(
        domain="esphome", data={"host": "192.168.43.183", "port": 6053, "password": ""}
    )
    entry.add_to_hass(hass)
    mock_entry_data = MagicMock()
    mock_entry_data.device_info.name = "test8266"
    hass.data[DATA_KEY] = {entry.entry_id: mock_entry_data}

    flow = _setup_flow_handler(hass)
    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {"address": "test8266.local"},
    }
    result = await flow.async_step_zeroconf(user_input=service_info)
    assert result["type"] == RESULT_TYPE_ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_duplicate_data(hass, mock_client):
    """Test discovery aborts if same mDNS packet arrives."""
    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {"address": "test8266.local"},
    }

    mock_client.device_info.return_value = mock_coro(MockDeviceInfo(False, "test8266"))

    result = await hass.config_entries.flow.async_init(
        "esphome", data=service_info, context={"source": "zeroconf"}
    )
    assert result["type"] == RESULT_TYPE_FORM
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_init(
        "esphome", data=service_info, context={"source": "zeroconf"}
    )
    assert result["type"] == RESULT_TYPE_ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_updates_unique_id(hass, mock_client):
    """Test a duplicate discovery host aborts and updates existing entry."""
    entry = MockConfigEntry(
        domain="esphome", data={"host": "192.168.43.183", "port": 6053, "password": ""}
    )

    entry.add_to_hass(hass)

    service_info = {
        "host": "192.168.43.183",
        "port": 6053,
        "hostname": "test8266.local.",
        "properties": {"address": "test8266.local"},
    }
    flow = await hass.config_entries.flow.async_init(
        "esphome", context={"source": "zeroconf"}, data=service_info
    )

    assert flow["type"] == RESULT_TYPE_ABORT
    assert flow["reason"] == "already_configured"

    assert entry.unique_id == "test8266"
