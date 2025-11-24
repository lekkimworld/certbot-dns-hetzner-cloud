import pytest
from types import SimpleNamespace
from certbot_dns_hetzner_cloud.hetzner_cloud_helper import HetznerCloudHelper
import certbot_dns_hetzner_cloud.hetzner_cloud_helper as mod

# ---- Test-Doubles ----

class FakeRRSet:
    def __init__(self, name, *values):
        self.name = name
        # Support multiple values: each can be a string or tuple (value, comment)
        self.records = []
        for v in values:
            if isinstance(v, tuple):
                value, comment = v
            else:
                value, comment = v, None
            self.records.append(SimpleNamespace(value=value, comment=comment))

class FakeRRSetListResp:
    def __init__(self, rrsets=None):
        self.rrsets = rrsets or []

class FakeZonesAPI:
    def __init__(self):
        # Aufgerufen-Flags + zuletzt übergebene Argumente
        self.calls = []
        self.bound_zone = SimpleNamespace(id="Z1", name="example.com")

    # Zonen
    def get(self, zone_name):
        self.calls.append(("get", zone_name))
        assert zone_name == "example.com"
        return self.bound_zone

    # RRSet lesen
    def get_rrset_list(self, *, zone, name, type):
        self.calls.append(("get_rrset_list", zone.name, name, type))
        # Rückgabe wird pro Test per Injection gesetzt
        return self._rrset_list

    # RRSet löschen
    def delete_rrset(self, rrset):
        self.calls.append(("delete_rrset", rrset.name))

    # RRSet erstellen
    def create_rrset(self, *, zone, name, type, records):
        self.calls.append(("create_rrset", zone.name, name, type, tuple(r.value for r in records)))
        # Minimal-Response mit rrset zurückgeben (ähnlich hcloud)
        return SimpleNamespace(rrset=FakeRRSet(name, records[0].value))

class FakeClient:
    def __init__(self):
        self.zones = FakeZonesAPI()

class FakeBoundZone:
    def __init__(self, name="example.com", id_="Z1"):
        self.name = name
        self.id = id_

# ---- Fixtures ----

@pytest.fixture
def helper(monkeypatch):
    # 1) Hetzner-Client faken
    def fake_init(self, api_key: str):
        self.client = FakeClient()
    monkeypatch.setattr(HetznerCloudHelper, "__init__", fake_init)

    # 2) BoundZone-Klasse im Modul patchen, damit isinstance(...) True ist
    monkeypatch.setattr(mod, "BoundZone", FakeBoundZone)

    # 3) Instanz + Default-BoundZone setzen
    h = HetznerCloudHelper("DUMMY")
    h.client.zones.bound_zone = FakeBoundZone(name="example.com", id_="Z1")

    # (optional) leere RRSet-Liste als Default
    h.client.zones._rrset_list = FakeRRSetListResp([])

    return h

# ---- Tests ----

def test_ensure_zone_with_string(helper):
    zones = helper.client.zones
    zones._rrset_list = FakeRRSetListResp([])  # default

    z = helper._ensure_zone("example.com")
    assert z.name == "example.com"
    assert ("get", "example.com") in zones.calls

def test_ensure_zone_with_boundzone(helper, monkeypatch):
    zones = helper.client.zones
    zones._rrset_list = FakeRRSetListResp([])

    # make BoundZone isinstance(...) pass
    monkeypatch.setattr(mod, "BoundZone", FakeBoundZone)

    bound = FakeBoundZone(name="example.com", id_="Z1")
    zones.bound_zone = bound  # our fake bound zone

    z = helper._ensure_zone(bound)

    assert z is bound
    assert z.name == "example.com"
    assert not any(c[0] == "get" for c in zones.calls)

def test_delete_txt_record_deletes_when_present(helper):
    zones = helper.client.zones
    # Simuliere vorhandenes RRSet
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"old"')])

    helper.delete_txt_record("example.com", "_acme-challenge")

    # Erwartung: get -> get_rrset_list -> delete_rrset
    assert ("get", "example.com") in zones.calls
    assert ("get_rrset_list", "example.com", "_acme-challenge", "TXT") in zones.calls
    assert ("delete_rrset", "_acme-challenge") in zones.calls

def test_delete_txt_record_noop_when_absent(helper):
    zones = helper.client.zones
    zones._rrset_list = FakeRRSetListResp([])

    helper.delete_txt_record("example.com", "_acme-challenge")

    # Kein delete_rrset-Call
    assert ("get_rrset_list", "example.com", "_acme-challenge", "TXT") in zones.calls
    assert not [c for c in zones.calls if c[0] == "delete_rrset"]

def test_put_txt_record_quotes_value_and_replaces(helper):
    zones = helper.client.zones
    # Vorhandenes RRSet -> sollte erst gelöscht, dann neu erstellt werden
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"old"')])

    resp = helper.put_txt_record("example.com", "_acme-challenge", value="abc123", comment="test")

    # Reihenfolge prüfen: get → get_rrset_list → delete_rrset → create_rrset
    names = [c[0] for c in zones.calls]
    assert names[:4] == ["get", "get_rrset_list", "delete_rrset", "create_rrset"]

    # create_rrset wurde mit gequotetem Value aufgerufen:
    create_call = [c for c in zones.calls if c[0] == "create_rrset"][-1]
    _, zone_name, rr_name, rr_type, values = create_call
    assert zone_name == "example.com"
    assert rr_name == "_acme-challenge"
    assert rr_type == "TXT"
    # Should preserve old value and add new one
    assert values == ('"old"', '"abc123"')

    # Response rrset contains records (first one is preserved old value)
    assert len(resp.rrset.records) > 0

def test_put_txt_record_preserves_multiple_existing_records(helper):
    zones = helper.client.zones
    # Multiple existing records
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"token1"', '"token2"')])

    helper.put_txt_record("example.com", "_acme-challenge", value="token3", comment="test")

    # Should create with all three values
    create_call = [c for c in zones.calls if c[0] == "create_rrset"][-1]
    _, zone_name, rr_name, rr_type, values = create_call
    assert values == ('"token1"', '"token2"', '"token3"')

def test_put_txt_record_avoids_duplicates(helper):
    zones = helper.client.zones
    # Record with same value already exists
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"token1"', '"abc123"')])

    helper.put_txt_record("example.com", "_acme-challenge", value="abc123", comment="test")

    # Should not duplicate, only have token1 and abc123
    create_call = [c for c in zones.calls if c[0] == "create_rrset"][-1]
    _, zone_name, rr_name, rr_type, values = create_call
    assert values == ('"token1"', '"abc123"')

def test_delete_txt_record_with_value_removes_only_that_value(helper):
    zones = helper.client.zones
    # Multiple records exist
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"token1"', '"token2"')])

    helper.delete_txt_record("example.com", "_acme-challenge", value="token1")

    # Should delete and recreate with only token2
    assert ("delete_rrset", "_acme-challenge") in zones.calls
    create_call = [c for c in zones.calls if c[0] == "create_rrset"]
    assert len(create_call) == 1
    _, zone_name, rr_name, rr_type, values = create_call[0]
    assert values == ('"token2"',)

def test_delete_txt_record_with_value_deletes_all_when_last_removed(helper):
    zones = helper.client.zones
    # Only one record exists
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"token1"')])

    helper.delete_txt_record("example.com", "_acme-challenge", value="token1")

    # Should delete but not recreate (no remaining records)
    assert ("delete_rrset", "_acme-challenge") in zones.calls
    create_calls = [c for c in zones.calls if c[0] == "create_rrset"]
    assert len(create_calls) == 0

def test_delete_txt_record_without_value_deletes_all(helper):
    zones = helper.client.zones
    # Multiple records exist
    zones._rrset_list = FakeRRSetListResp([FakeRRSet("_acme-challenge", '"token1"', '"token2"')])

    helper.delete_txt_record("example.com", "_acme-challenge", value=None)

    # Should delete entire rrset without recreation
    assert ("delete_rrset", "_acme-challenge") in zones.calls
    create_calls = [c for c in zones.calls if c[0] == "create_rrset"]
    assert len(create_calls) == 0
