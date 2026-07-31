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


    def test_quote_options_uses_deck_count_tier_and_finds_best_price(self):
        path = Path(__file__).resolve().parents[1] / "resources" / "DeckPrices.xlsx"
        service = PricingService()
        summary = service.load(path)
        options = service.quote_options(summary, 3641)
        self.assertTrue(options)
        best = options[0]
        self.assertEqual(best["deck_capacity"], 72)
        self.assertEqual(best["deck_count"], 51)
        self.assertEqual(best["quantity_tier"], "50-99")
        self.assertAlmostEqual(best["unit_price"], 10.55)
        self.assertAlmostEqual(best["total_price"], 538.05)
        self.assertEqual(best["unused_slots"], 31)
