"""Install the stock client's room-browser route through the EDOPal storage journal."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .relay import DEFAULT_PORT, validate_target

PLUGIN_ID = "deck-tracker"
SERVER_NAME = "EDOPal Relay"
USER_CONFIG = "config/user_configs.json"


@dataclass(frozen=True)
class Server:
    name: str
    address: str
    duelport: int
    roomaddress: str
    roomlistport: int
    roomlistprotocol: str = "http"

    def entry(self, port):
        return dict(
            name=SERVER_NAME,
            address="127.0.0.1",
            duelport=port,
            roomaddress=self.roomaddress,
            roomlistport=self.roomlistport,
            roomlistprotocol=self.roomlistprotocol,
        )


def _config(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_bytes().decode("utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("servers", []), list):
        raise ValueError(f"Invalid servers list in {path.name}.")
    return data


def servers(install):
    result = []
    for rel in (USER_CONFIG, "config/configs.json"):
        for item in _config(install.path(rel)).get("servers", []):
            if not isinstance(item, dict) or item.get("name") == SERVER_NAME:
                continue
            try:
                server = Server(**{key: item[key] for key in Server.__dataclass_fields__ if key in item})
            except TypeError:
                continue
            if (
                all(isinstance(s, str) and s.strip() for s in (server.name, server.address, server.roomaddress))
                and all(type(p) is int and 0 < p < 65536 for p in (server.duelport, server.roomlistport))
                and server.roomlistprotocol in ("http", "https")
            ):
                result.append(server)
    return result


def install_entry(storage, install, server, port=DEFAULT_PORT, *, confirmed_closed=False):
    validate_target(server.address, server.duelport, port)
    doc = _config(install.path(USER_CONFIG))
    entries = doc.setdefault("servers", [])
    desired = server.entry(port)
    existing = [s for s in entries if isinstance(s, dict) and s.get("name") == SERVER_NAME]
    if existing == [desired]:
        return
    if existing:
        if len(existing) != 1 or existing[0].get("address") != "127.0.0.1" or not storage.last_receipt(install, PLUGIN_ID):
            raise ValueError("A different server already uses the name EDOPal Relay.")
        entries[entries.index(existing[0])] = desired
    else:
        entries.append(desired)
    with storage.changeset(install, PLUGIN_ID, "Set up EDOPal Relay", confirmed_closed=confirmed_closed) as cs:
        cs.write(USER_CONFIG, (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode())


def route_info(storage, install, preferred=None):
    """Resolve the existing route, including a server selected in an earlier version."""
    choices = servers(install)
    entries = _config(install.path(USER_CONFIG)).get("servers", [])
    matching = [s for s in choices if s.entry(DEFAULT_PORT) in entries]
    current = next((s for s in matching if s.name == preferred), None) or next(iter(matching), None)
    chosen = next((s for s in choices if s.name == preferred), None) or current or next(iter(choices), None)
    return choices, chosen, current, bool(storage.last_receipt(install, PLUGIN_ID))
