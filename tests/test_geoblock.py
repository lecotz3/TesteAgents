import httpx
import respx

from desk.geoblock import GEOBLOCK_URL, check_geoblock


def _check(**mock):
    with respx.mock:
        respx.get(GEOBLOCK_URL).mock(**mock)
        with httpx.Client() as http:
            return check_geoblock(http)


def test_allowed_region():
    status = _check(return_value=httpx.Response(200, json={"blocked": False, "ip": "1.2.3.4", "country": "PT", "region": "11"}))
    assert not status.blocked and status.country == "PT" and status.error is None


def test_blocked_region():
    status = _check(return_value=httpx.Response(200, json={"blocked": True, "ip": "1.2.3.4", "country": "BR", "region": "SP"}))
    assert status.blocked and status.country == "BR"


def test_network_error_fails_closed():
    status = _check(side_effect=httpx.ConnectError("dns"))
    assert status.blocked and status.error


def test_unexpected_payload_fails_closed():
    status = _check(return_value=httpx.Response(200, json={"foo": 1}))
    assert status.blocked and status.error
