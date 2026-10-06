"""Shared entity helpers for Adelaide Metro."""

from __future__ import annotations

import logging

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

# Assistants that "expose to assistants" applies to
_ASSISTANTS = ("conversation", "cloud.google_assistant")


class AssistantExposureMixin(Entity):
    """Expose the entity to voice assistants when it is first added.

    Skips any assistant the user has already made a choice for, so
    un-exposing an entity sticks.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not getattr(self.coordinator, "expose_to_assistants", False) or not self.registry_entry:
            return
        for assistant in _ASSISTANTS:
            if "should_expose" in self.registry_entry.options.get(assistant, {}):
                continue
            try:
                async_expose_entity(self.hass, assistant, self.entity_id, True)
            except Exception as err:  # noqa: BLE001 - assistant may not be set up
                _LOGGER.debug("Could not expose %s to %s: %s", self.entity_id, assistant, err)


@callback
def remove_orphaned_entities(
    hass: HomeAssistant, entry: ConfigEntry, domain: str, prefixes: tuple[str, ...], keep: set[str]
) -> None:
    """Remove dynamic entities left in the registry by a previous run."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == domain
            and reg_entry.unique_id.startswith(prefixes)
            and reg_entry.unique_id not in keep
        ):
            _LOGGER.debug("Removing orphaned entity %s", reg_entry.entity_id)
            registry.async_remove(reg_entry.entity_id)
