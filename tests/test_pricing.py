import unittest
from pathlib import Path
from app.services.pricing_service import PricingService

class PricingTests(unittest.TestCase):
    def test_uploaded_price_table(self):
        path = Path(__file__).resolve().parents[1] / "resources" / "DeckPrices.xlsx"
        result = PricingService().load(path)
        self.assertEqual(len(result.deck_sizes), 16)
        self.assertEqual(result.deck_sizes[0], 612)
        self.assertEqual(result.deck_sizes[-1], 18)
        self.assertAlmostEqual(result.prices[612]["01-005"], 139.00)
        self.assertAlmostEqual(result.prices[18]["15000+"], 0.75)
        self.assertEqual(len(result.quantity_tiers), 14)
