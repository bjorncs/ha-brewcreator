import unittest
from unittest.mock import MagicMock

from custom_components.brewcreator.device import find_device


class FindDeviceTestCase(unittest.TestCase):
    def test_looks_up_within_the_config_entry(self):
        registry = MagicMock(spec=["async_get_device", "async_get_device_by_identifier"])
        self.assertIs(
            registry.async_get_device_by_identifier.return_value,
            find_device(registry, "f", "entry"),
        )
        registry.async_get_device_by_identifier.assert_called_once_with(("brewcreator", "f"), "entry")
        registry.async_get_device.assert_not_called()

    def test_falls_back_before_home_assistant_2026_11(self):
        registry = MagicMock(spec=["async_get_device"])
        self.assertIs(registry.async_get_device.return_value, find_device(registry, "f", "entry"))
        registry.async_get_device.assert_called_once_with(identifiers={("brewcreator", "f")})


if __name__ == "__main__":
    unittest.main()
