"""Real-time read/write of LOCATED variables over Modbus TCP.

The UDE COM API cannot read or write live tag values (it is a project-database
server). A Modicon CPU exposes its %M/%MW memory over its embedded Modbus TCP
server (default port 502), so this module reads/writes located tags directly
while the PLC runs — the same channel SCADA uses.

Scope and gotchas:
  * Only LOCATED tags work — those with a topological address %M / %MW (and the
    %MW-mapped 32-bit types). Unlocated DFB internals (e.g. Pump1.Running) are
    NOT in this address space; mirror them to %MW in the program to watch them.
  * Decoding is driven by the IEC TYPE, not the address letter: a REAL or DINT
    is commonly mapped onto %MW and occupies two consecutive words.
  * 32-bit word order is Schneider-native LOW WORD FIRST by default (the lower
    %MW holds the least-significant 16 bits). Override with word_order if a
    given server differs.
  * Works against the Control Expert SIMULATOR too: it exposes a Modbus TCP
    server on 127.0.0.1:502 (the endpoint a Vijeo Designer I/O scanner reaches),
    as well as a real CPU or any Modbus-TCP endpoint.

Addressing: %M{i} -> coil i; %MW{i} -> holding register i;
%MW{i}.{j} -> bit j of register i; %MD/%MF -> 32-bit at words i, i+1.
"""

from __future__ import annotations

import re
import struct
from typing import Any

# IEC types that occupy two 16-bit registers when mapped to %MW.
_DWORD_TYPES = {"DINT", "UDINT", "DWORD", "REAL", "TIME", "DATE", "TOD", "DT"}
_WORD_TYPES = {"INT", "UINT", "WORD", "INT16"}
_SIGNED = {"INT", "DINT"}
_BOOL_TYPES = {"BOOL", "EBOOL"}

_ADDR_RE = re.compile(r"^%(M|MW|MD|MF|MX)(\d+)(?:\.(\d+))?$", re.IGNORECASE)


class ModbusError(RuntimeError):
    """A readable Modbus / addressing error."""


def parse_address(address: str) -> tuple[str, int, int | None]:
    """('%MW70'|'%M3'|'%MW10.2') -> (family, offset, bit). family in
    {'M','MW','MD','MF','MX'}. bit is set only for '%MWi.j'."""
    if not address:
        raise ModbusError("Tag has no topological address — Modbus needs a located %M/%MW tag.")
    m = _ADDR_RE.match(address.strip())
    if not m:
        raise ModbusError(
            f"Address '{address}' is not Modbus-addressable. Only %M / %MW "
            "(and %MD/%MF/%MX, %MWi.j) map to the CPU Modbus server."
        )
    return m.group(1).upper(), int(m.group(2)), (int(m.group(3)) if m.group(3) else None)


def _reg_count(iec_type: str, family: str) -> int:
    t = (iec_type or "").upper()
    if family in ("MD", "MF") or t in _DWORD_TYPES:
        return 2
    return 1


def _decode(regs: list[int], iec_type: str, family: str, low_first: bool):
    t = (iec_type or "WORD").upper()
    if len(regs) == 1:
        v = regs[0] & 0xFFFF
        if t in _SIGNED and v >= 0x8000:
            v -= 0x10000
        return v
    lo, hi = (regs[0], regs[1]) if low_first else (regs[1], regs[0])
    raw = (hi << 16) | (lo & 0xFFFF)
    if t == "REAL":
        return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]
    if t == "DINT" and raw >= 0x80000000:
        return raw - 0x100000000
    return raw  # UDINT / DWORD / TIME(ms)


def _encode(value: Any, iec_type: str, family: str, low_first: bool) -> list[int]:
    t = (iec_type or "WORD").upper()
    if _reg_count(t, family) == 1:
        iv = int(round(float(value))) if not isinstance(value, bool) else int(value)
        return [iv & 0xFFFF]
    if t == "REAL":
        raw = struct.unpack("<I", struct.pack("<f", float(value)))[0]
    else:
        raw = int(round(float(value))) & 0xFFFFFFFF
    lo, hi = raw & 0xFFFF, (raw >> 16) & 0xFFFF
    return [lo, hi] if low_first else [hi, lo]


class ModbusClient:
    """Holds one Modbus TCP connection plus decode settings."""

    def __init__(self) -> None:
        self._client: Any = None
        self.host: str | None = None
        self.port: int = 502
        self.unit: int = 1
        self.low_first: bool = True

    # ------------------------------------------------------------- lifecycle

    def connect(self, host: str, port: int, unit: int, word_order: str) -> dict:
        from pymodbus.client import ModbusTcpClient

        self.disconnect()
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.low_first = (word_order or "low_first").lower() not in ("high_first", "big", "high")
        self._client = ModbusTcpClient(host, port=int(port), timeout=3)
        if not self._client.connect():
            self._client = None
            raise ModbusError(
                f"Could not open Modbus TCP connection to {host}:{port}. Check the IP, that "
                "the CPU's Modbus server is enabled, and that nothing blocks port 502. "
                "(The Control Expert simulator usually has no Modbus server.)"
            )
        return self.status()

    def disconnect(self) -> dict:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        return {"connected": False}

    def status(self) -> dict:
        return {
            "connected": self._client is not None and bool(self._client.connected),
            "host": self.host,
            "port": self.port,
            "unit": self.unit,
            "word_order": "low_first" if self.low_first else "high_first",
        }

    def _ensure(self):
        if self._client is None:
            raise ModbusError("Not connected. Call modbus_connect first.")
        return self._client

    # ------------------------------------------------------------ read/write

    def read_one(self, address: str, iec_type: str) -> Any:
        client = self._ensure()
        family, offset, bit = parse_address(address)
        if family in ("M", "MX") or (iec_type or "").upper() in _BOOL_TYPES and bit is None and family == "M":
            rr = client.read_coils(offset, count=1, device_id=self.unit)
            if rr.isError():
                raise ModbusError(f"Read coil {address} failed: {rr}")
            return bool(rr.bits[0])
        count = _reg_count(iec_type, family)
        rr = client.read_holding_registers(offset, count=count, device_id=self.unit)
        if rr.isError():
            raise ModbusError(f"Read register {address} failed: {rr}")
        if bit is not None:
            return bool((rr.registers[0] >> bit) & 1)
        return _decode(list(rr.registers), iec_type, family, self.low_first)

    def write_one(self, address: str, iec_type: str, value: Any) -> None:
        client = self._ensure()
        family, offset, bit = parse_address(address)
        if family in ("M", "MX") and bit is None and (iec_type or "BOOL").upper() in _BOOL_TYPES | {""}:
            rsp = client.write_coil(offset, bool(value), device_id=self.unit)
            if rsp.isError():
                raise ModbusError(f"Write coil {address} failed: {rsp}")
            return
        if bit is not None:  # read-modify-write a single bit of a word
            rr = client.read_holding_registers(offset, count=1, device_id=self.unit)
            if rr.isError():
                raise ModbusError(f"Read-before-write {address} failed: {rr}")
            word = rr.registers[0]
            word = (word | (1 << bit)) if bool(value) else (word & ~(1 << bit))
            rsp = client.write_register(offset, word & 0xFFFF, device_id=self.unit)
            if rsp.isError():
                raise ModbusError(f"Write bit {address} failed: {rsp}")
            return
        regs = _encode(value, iec_type, family, self.low_first)
        if len(regs) == 1:
            rsp = client.write_register(offset, regs[0], device_id=self.unit)
        else:
            rsp = client.write_registers(offset, regs, device_id=self.unit)
        if rsp.isError():
            raise ModbusError(f"Write register {address} failed: {rsp}")
