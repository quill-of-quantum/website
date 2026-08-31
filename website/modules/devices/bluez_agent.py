"""Restricted BlueZ NoInputNoOutput agent for devices selected by the gateway."""

from dbus_fast import BusType, DBusError
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

AGENT_PATH = "/com/bbdwz/DeviceBleAgent"


class PairingAgent(ServiceInterface):
    def __init__(self):
        super().__init__("org.bluez.Agent1"); self.allowed = set()

    @staticmethod
    def _token(address): return str(address or "").upper().replace(":", "_")

    def allow(self,address): self.allowed.add(self._token(address))
    def deny(self,address): self.allowed.discard(self._token(address))

    def _check(self,device):
        if not any(token and token in str(device).upper() for token in self.allowed):
            raise DBusError("org.bluez.Error.Rejected", "Device is not selected by the BLE gateway")

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        self._check(device); return "000000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        self._check(device)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        self._check(device); return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        self._check(device)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        # ESP32 is configured NoInputNoOutput/Just Works. Application HMAC
        # authenticates the approved device immediately after OS pairing.
        self._check(device)

    @method()
    def RequestAuthorization(self, device: "o"):
        self._check(device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        self._check(device)

    @method()
    def Cancel(self):
        pass


class BluezAgent:
    def __init__(self):
        self.bus=None; self.manager=None; self.agent=PairingAgent()

    async def start(self):
        self.bus=await MessageBus(bus_type=BusType.SYSTEM).connect()
        self.bus.export(AGENT_PATH,self.agent)
        introspection=await self.bus.introspect("org.bluez","/org/bluez")
        proxy=self.bus.get_proxy_object("org.bluez","/org/bluez",introspection)
        self.manager=proxy.get_interface("org.bluez.AgentManager1")
        await self.manager.call_register_agent(AGENT_PATH,"NoInputNoOutput")
        await self.manager.call_request_default_agent(AGENT_PATH)

    async def stop(self):
        if self.manager:
            try: await self.manager.call_unregister_agent(AGENT_PATH)
            except Exception: pass
        if self.bus:
            try: self.bus.unexport(AGENT_PATH)
            except Exception: pass
            self.bus.disconnect()
        self.manager=None; self.bus=None
