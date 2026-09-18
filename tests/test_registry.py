import pytest

from rrs_admin.registry import RegistryError, find_ha_site

REGISTRY = """
clients:
  - name: oscar-home
    host: 192.168.13.13
    port: 8123
    pass_vault: "Smart Home Agent"
    pass_item: "HA - Oscar Home"
  - name: evergreen-a201
    host: 192.168.22.2
    pass_vault: "Smart Home Agent"
    pass_item: "HA - Evergreen"
  - name: roman-kissonegra
    host: 192.168.56.2
    port: 8123
    remote_host: 10.121.18.60
    remote_port: 8123
    pass_vault: "Smart Home Agent"
    pass_item: "HA - Roman Kissonegra"
"""


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(REGISTRY)
    return path


def test_site_is_found_by_its_slug(registry):
    site = find_ha_site(registry, "oscar-home")
    assert (site.url, site.pass_item) == ("http://192.168.13.13:8123", "HA - Oscar Home")


def test_hyphens_may_differ_and_port_defaults(registry):
    assert find_ha_site(registry, "evergreen-a-201").url == "http://192.168.22.2:8123"


def test_remote_address_is_used_on_request(registry):
    assert find_ha_site(registry, "roman-kissonegra", remote=True).url == "http://10.121.18.60:8123"
    with pytest.raises(RegistryError, match="remote_host"):
        find_ha_site(registry, "oscar-home", remote=True)


def test_unknown_site(registry):
    with pytest.raises(RegistryError, match="not"):
        find_ha_site(registry, "qube-block-b-104")
