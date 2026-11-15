import unittest
from unittest.mock import MagicMock

from custom_components.brewcreator.device import find_device


class FindDeviceTestCase(unittest.TestCase):
    def test_looks_up_within_the_config_entry(self):
        registry = MagicMock(spec=["async_get_device_by_identifier"])
        self.assertIs(
            registry.async_get_device_by_identifier.return_value,
            find_device(registry, "f", "entry"),
        )
        registry.async_get_device_by_identifier.assert_called_once_with(("brewcreator", "f"), "entry")


if __name__ == "__main__":
    unittest.main()
