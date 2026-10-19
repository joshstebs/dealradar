from pathlib import Path
import re
import unittest

from backend.main import create_app


class AndroidContractTests(unittest.TestCase):
    def test_android_dtos_match_openapi_wire_names_and_money_types(self):
        source = (Path(__file__).parents[1] / "android/app/src/main/java/ca/dealradar/app/Api.kt").read_text()
        schemas = create_app().openapi()["components"]["schemas"]
        for kotlin_name, schema_name in (("Offer", "Offer"), ("ComparisonGroup", "ComparisonGroup"),
                                         ("SearchResponse", "SearchResponse"), ("SourceStatus", "SourceStatus")):
            block = re.search(rf"data class {kotlin_name}\((.*?)(?=\n(?:data class|interface)|\Z)", source, re.S)
            self.assertIsNotNone(block, kotlin_name)
            fields = re.findall(r'(?:@SerializedName\("([^\"]+)"\)\s+)?val\s+(\w+):\s*([\w<>?]+)', block[1])
            self.assertTrue(fields)
            for wire_name, field_name, field_type in fields:
                prop = schemas[schema_name]["properties"].get(wire_name or field_name)
                self.assertIsNotNone(prop, f"{kotlin_name}.{field_name} has no API wire field")
                if (wire_name or field_name) in ("sale_price", "regular_price", "unit_price"):
                    possible_types = {p.get("type") for p in prop.get("anyOf", [prop])}
                    self.assertIn("string", possible_types)
                    self.assertEqual(field_type.rstrip("?"), "String")


if __name__ == "__main__":
    unittest.main()
