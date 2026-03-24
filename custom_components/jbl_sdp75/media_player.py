"""Support for JBL SDP-75 media player."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_IDLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Protocol value -> display name
SOUND_MODE_NAMES: dict[str, str] = {
    "none": "None",
    "native": "Native",
    "auto": "Auto",
    "dolby": "Dolby",
    "dts": "DTS",
    "auro3d": "Auro-3D",
    "legacy": "Legacy",
    "upmix on native": "Upmix on Native",
}
# Reverse: display name -> protocol value
SOUND_MODE_PROTOCOL = {v: k for k, v in SOUND_MODE_NAMES.items()}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up JBL SDP-75 media player from a config entry."""
    host = entry.data[CONF_HOST]
    entity = JBLSDP75MediaPlayer(host, entry)
    hass.data[DOMAIN][entry.entry_id]["entity"] = entity
    async_add_entities([entity], True)

class JBLSDP75MediaPlayer(MediaPlayerEntity):
    """Representation of a JBL SDP-75 media player."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.SELECT_SOUND_MODE
    )

    def __init__(self, host: str, entry: ConfigEntry) -> None:
        """Initialize the media player."""
        self._host = host
        self._entry = entry
        self._attr_unique_id = f"jbl_sdp75_{host}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": "JBL SDP-75",
            "manufacturer": "JBL",
            "model": "SDP-75",
        }
        self._state = STATE_OFF
        self._volume = 0
        self._muted = False
        self._source = None
        self._available = False
        self._reader = None
        self._writer = None
        self._sources = {}  # Map of profile index to name
        self._sound_mode: str | None = None
        self._sound_modes: list[str] = list(SOUND_MODE_NAMES.values())
        self._read_task = None
        self._running = False

    async def _read_loop(self) -> None:
        """Continuously read from the device."""
        self._running = True
        while self._running and self._reader is not None:
            try:
                line = await self._reader.readline()
                if not line:  # EOF
                    break
                    
                line = line.decode().strip()
                # Process the received line
                await self._process_line(line)
                
            except Exception as ex:
                if self._running:  # Only log if we're still meant to be running
                    _LOGGER.error("Error reading from device: %s", ex)
                break
        
        if self._running:  # Only cleanup if we didn't stop intentionally
            await self._cleanup()

    async def _process_line(self, line: str) -> None:
        """Process a line received from the device."""
        if line.startswith("PROFILE "):
            try:
                # Format: "PROFILE X: Name"
                profile_parts = line.split(": ", 1)
                if len(profile_parts) == 2:
                    # Extract index from "PROFILE X"
                    index = int(profile_parts[0].split(" ")[1])
                    name = profile_parts[1]
                    self._sources[index] = name
                    self.async_write_ha_state()
            except (ValueError, IndexError) as ex:
                _LOGGER.error("Error parsing profile: %s", ex)
        elif line.startswith("META_PRESET_LOADED ") or line.startswith("CURRENT_PRESET "):
            try:
                index = int(line.split(" ")[1])
                if index in self._sources:
                    self._source = self._sources[index]
                    self.async_write_ha_state()
            except (ValueError, IndexError) as ex:
                _LOGGER.error("Error parsing preset: %s", ex)
        elif line.startswith("VOLUME "):
            try:
                # Extract volume value (e.g., -33.600000)
                volume_db = float(line.split(" ")[1])
                # Convert from dB (-80..0) to Home Assistant range (0..1)
                volume = (volume_db + 80) / 80
                # Clamp to valid range
                volume = max(0.0, min(1.0, volume))
                self._volume = volume
                self.async_write_ha_state()
            except (ValueError, IndexError) as ex:
                _LOGGER.error("Error parsing volume: %s", ex)
        elif line.startswith("MUTE "):
            try:
                # Extract mute value (1 for muted, 0 for unmuted)
                mute_value = int(line.split(" ")[1])
                self._muted = bool(mute_value)
                self.async_write_ha_state()
            except (ValueError, IndexError) as ex:
                _LOGGER.error("Error parsing mute status: %s", ex)
        elif line.startswith("DECODER "):
            match = re.match(
                r"^DECODER NONAUDIO [01] PLAYABLE [01] DECODER .+ UPMIXER (.+)$",
                line,
            )
            if match:
                upmixer = match.group(1).strip()
                display = SOUND_MODE_NAMES.get(upmixer, upmixer)
                self._sound_mode = display
                if display not in self._sound_modes:
                    self._sound_modes.append(display)
                self.async_write_ha_state()
        elif line in SOUND_MODE_NAMES:
            # Bare upmixer query response (e.g. "auto")
            self._sound_mode = SOUND_MODE_NAMES[line]
            self.async_write_ha_state()
        else:
            if line != "OK":
                _LOGGER.warning("Received line from device: %s", line)

    async def _ensure_connected(self) -> bool:
        """Ensure connection to device is established."""
        if self._writer is not None:
            try:
                self._writer.write(b"\n")
                await self._writer.drain()
                return True
            except Exception:
                # Connection lost, clean up and try to reconnect
                await self._cleanup()

        # Try to connect with exponential backoff
        retry_count = 0
        max_retries = 5  # Will try immediately, then at 2s, 4s, 8s, 16s
        base_delay = 2.0

        while retry_count <= max_retries:
            try:
                _LOGGER.warning("Connecting to JBL SDP-75 at %s", self._host)
                self._reader, self._writer = await asyncio.open_connection(self._host, 44100)
                _LOGGER.warning("Initially connected to JBL SDP-75 at %s", self._host)
                
                # Read welcome message
                try:
                    welcome = await asyncio.wait_for(self._reader.readline(), timeout=2.0)
                    welcome_text = welcome.decode().strip()
                    if "Welcome on Trinnov Optimizer" in welcome_text:
                        _LOGGER.warning("Connected to JBL SDP-75: %s", welcome_text)
                        
                        # Send login command
                        self._writer.write(b"id homeassistant\n")
                        await self._writer.drain()
                        
                        # Reset sources dictionary
                        self._sources = {}
                        
                        # Start the read loop task
                        if self._read_task is None or self._read_task.done():
                            self._read_task = asyncio.create_task(self._read_loop())
                        
                        await self._send_command("get_current_state")
                        await self._send_command("upmixer")
                        return True
                    else:
                        _LOGGER.error("Unexpected welcome message: %s", welcome_text)
                        await self._cleanup()
                        return False
                except asyncio.TimeoutError:
                    _LOGGER.exception("Timeout reading welcome message")
                    await self._cleanup()
                    return False
            except Exception as ex:
                retry_count += 1
                if retry_count <= max_retries:
                    delay = base_delay * (2 ** (retry_count - 1))
                    _LOGGER.warning(
                        "Failed to connect to JBL SDP-75, retrying in %.1f seconds: %s",
                        delay, ex
                    )
                    await asyncio.sleep(delay)
                else:
                    _LOGGER.error(
                        "Failed to connect to JBL SDP-75 after %d attempts: %s",
                        max_retries + 1, ex
                    )
                    await self._cleanup()
                    return False

    async def _cleanup(self) -> None:
        """Clean up the connection."""
        self._running = False
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
            self._read_task = None
            
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None
        self._available = False
        self._state = STATE_OFF

    async def _send_command(self, command: str) -> tuple[bool, str | None]:
        """Send command to device and read response."""
        if not await self._ensure_connected():
            return False, None

        try:
            self._writer.write(f"{command}\n".encode())
            await self._writer.drain()
            return True, "OK"  # Return first response
        except Exception as ex:
            _LOGGER.error("Failed to send command '%s': %s", command, ex)
            await self._cleanup()
            return False, None

    async def async_update(self) -> None:
        """Update state of device."""
        # Always try to ensure connection during update
        if not await self._ensure_connected():
            self._available = False
            self._state = STATE_OFF
            return
        
        self._available = True
        self._state = STATE_ON

    async def async_turn_on(self) -> None:
        """Turn the media player on."""
        # TODO: Implement turn on command
        pass

    async def async_turn_off(self) -> None:
        """Turn the media player off."""
        # TODO: Implement turn off command
        pass

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute the volume."""
        # Send mute command: 1 for mute, 0 for unmute
        success, response = await self._send_command(f"mute {1 if mute else 0}")
        if success:
            if response == "OK":
                self._muted = mute
                self.async_write_ha_state()
            elif response.startswith("ERROR"):
                _LOGGER.error("Device returned error: %s", response)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        # Convert Home Assistant's 0-1 range to dB
        # Assuming reasonable range of -80dB to 0dB
        volume_db = -80 + (volume * 80)
        
        success, response = await self._send_command(f"volume {volume_db:.1f}")
        if success:
            if response == "OK":
                self._volume = volume
                self.async_write_ha_state()
            elif response.startswith("ERROR"):
                _LOGGER.error("Device returned error: %s", response)

    async def async_select_sound_mode(self, sound_mode: str) -> None:
        """Select sound mode (upmixer)."""
        protocol_value = SOUND_MODE_PROTOCOL.get(sound_mode, sound_mode)
        success, response = await self._send_command(f"upmixer {protocol_value}")
        if success:
            self._sound_mode = sound_mode
            self.async_write_ha_state()

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        # Find index for the given source name
        try:
            index = next(idx for idx, name in self._sources.items() if name == source)
            success, response = await self._send_command(f"profile {index}")
            if success:
                if response == "OK":
                    self._source = source
                    self.async_write_ha_state()
                elif response.startswith("ERROR"):
                    _LOGGER.error("Failed to set source: %s", response)
        except StopIteration:
            _LOGGER.error("Invalid source: %s", source)

    @property
    def source_list(self) -> list[str] | None:
        """List of available input sources."""
        if not self._sources:
            return None
        hidden = self._entry.options.get("hidden_sources", [])
        return [s for s in self._sources.values() if s not in hidden] or None

    @property
    def state(self) -> str | None:
        """State of the player."""
        return self._state

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self) -> bool | None:
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def source(self) -> str | None:
        """Name of the current input source."""
        return self._source

    @property
    def sound_mode(self) -> str | None:
        """Name of the current sound mode (upmixer)."""
        return self._sound_mode

    @property
    def sound_mode_list(self) -> list[str] | None:
        """List of available sound modes."""
        return self._sound_modes

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        await self._cleanup()
