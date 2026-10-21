from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from decimal import Decimal

from turfs.models import Turf
from reviews.models import Review


class ReviewFeatureTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.turf = Turf.objects.create(
            name="Test Arena Pro",
            slug="test-arena-pro",
            sport_type="FOOTBALL",
            base_price=Decimal("1200.00"),
            is_active=True,
        )

    def test_post_guest_review_success(self):
        payload = {
            "turf_id": self.turf.id,
            "rating": 5,
            "review_text": "Pristine turf conditions and excellent night floodlights!",
            "guest_name": "Suresh Raina",
            "guest_phone": "9876543210",
        }
        response = self.client.post("/api/reviews/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["rating"], 5)
        self.assertEqual(response.data["customer_name"], "Suresh Raina")
        self.assertEqual(response.data["guest_phone"], "9876543210")

        # Verify DB storage
        review_obj = Review.objects.get(id=response.data["id"])
        self.assertIsNone(review_obj.booking)
        self.assertIsNone(review_obj.customer)
        self.assertEqual(review_obj.guest_name, "Suresh Raina")

    def test_invalid_star_ratings_rejected(self):
        # Test rating > 5
        res_high = self.client.post(
            "/api/reviews/",
            {"turf_id": self.turf.id, "rating": 6, "review_text": "Too good"},
            format="json",
        )
        self.assertEqual(res_high.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("between 1 and 5", res_high.data["error"])

        # Test rating < 1
        res_low = self.client.post(
            "/api/reviews/",
            {"turf_id": self.turf.id, "rating": 0, "review_text": "Terrible"},
            format="json",
        )
        self.assertEqual(res_low.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("between 1 and 5", res_low.data["error"])

    def test_empty_review_text_rejected(self):
        res = self.client.post(
            "/api/reviews/",
            {"turf_id": self.turf.id, "rating": 5, "review_text": "   "},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reviews_list_and_min_rating_filter(self):
        Review.objects.create(
            turf=self.turf,
            rating=5,
            review_text="Top quality pitch",
            guest_name="Player 1",
        )
        Review.objects.create(
            turf=self.turf,
            rating=3,
            review_text="Average bounce",
            guest_name="Player 2",
        )
        Review.objects.create(
            turf=self.turf,
            rating=5,
            review_text="Spam review",
            guest_name="Spammer",
            is_hidden=True,
        )

        # GET /api/reviews/?min_rating=4
        res = self.client.get("/api/reviews/?min_rating=4")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Should only contain public reviews with rating >= 4
        # Note: auto-provisioned turfs might also exist in the test db, so filter for Player 1
        player1_reviews = [r for r in res.data if r.get("guest_name") == "Player 1"]
        self.assertEqual(len(player1_reviews), 1)
        self.assertEqual(player1_reviews[0]["rating"], 5)
        
        # Verify hidden review is not present for non-admin
        hidden_reviews = [r for r in res.data if r.get("guest_name") == "Spammer"]
        self.assertEqual(len(hidden_reviews), 0)
