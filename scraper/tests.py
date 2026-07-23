from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import AutoConfig, BusinessListing, ContactAttempt, EmailCampaign, EmailSend, SmtpProfile
from .email_sender import send_campaign
from .views import _advance_due_followups
from .bing_maps_scraper import _extract_from_list_item
from .search_scraper import _fetch, _is_captcha, _parse_bing_images_results, _visit_site_for_details


class SmtpGlobalSettingsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="settings-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def post_settings(self, **data):
        return self.client.post("/smtp/", data=data, follow=True)

    def test_preset_limits_are_saved(self):
        response = self.post_settings(
            global_daily_limit_option="600",
            smtp_rotation_limit_option="200",
        )

        self.assertEqual(response.status_code, 200)
        config = AutoConfig.get()
        self.assertEqual(config.global_daily_limit, 600)
        self.assertEqual(config.smtp_rotation_limit, 200)
        self.assertContains(response, "Global settings saved successfully.")

    def test_custom_limits_are_saved_without_javascript(self):
        self.post_settings(
            global_daily_limit_option="custom",
            global_daily_limit_custom="275",
            smtp_rotation_limit_option="custom",
            smtp_rotation_limit_custom="85",
        )

        config = AutoConfig.get()
        self.assertEqual(config.global_daily_limit, 275)
        self.assertEqual(config.smtp_rotation_limit, 85)

    def test_limits_can_be_cleared(self):
        config = AutoConfig.get()
        config.global_daily_limit = 500
        config.smtp_rotation_limit = 100
        config.save()

        self.post_settings(
            global_daily_limit_option="0",
            smtp_rotation_limit_option="0",
        )

        config.refresh_from_db()
        self.assertEqual(config.global_daily_limit, 0)
        self.assertEqual(config.smtp_rotation_limit, 0)

    def test_smtp_rotation_pool_is_saved_with_limits(self):
        first = SmtpProfile.objects.create(name="First", user="first@example.com", password="secret")
        second = SmtpProfile.objects.create(name="Second", user="second@example.com", password="secret")

        self.post_settings(
            global_daily_limit_option="300",
            smtp_rotation_limit_option="0",
            global_smtp_profiles=[str(first.id), str(second.id)],
        )

        self.assertSetEqual(
            set(AutoConfig.get().global_smtp_profiles.values_list("id", flat=True)),
            {first.id, second.id},
        )

    def test_invalid_custom_limit_does_not_overwrite_existing_value(self):
        config = AutoConfig.get()
        config.global_daily_limit = 400
        config.save()

        response = self.post_settings(
            global_daily_limit_option="custom",
            global_daily_limit_custom="",
            smtp_rotation_limit_option="0",
        )

        config.refresh_from_db()
        self.assertEqual(config.global_daily_limit, 400)
        self.assertContains(response, "Enter a whole number for the global daily limit.")

    def test_legacy_hidden_field_names_remain_compatible(self):
        self.post_settings(
            global_daily_limit="325",
            smtp_rotation_limit="125",
        )

        config = AutoConfig.get()
        self.assertEqual(config.global_daily_limit, 325)
        self.assertEqual(config.smtp_rotation_limit, 125)

    def test_partial_submission_preserves_existing_custom_limits(self):
        config = AutoConfig.get()
        config.global_daily_limit = 275
        config.smtp_rotation_limit = 85
        config.save()

        self.post_settings(global_smtp_profiles=[])

        config.refresh_from_db()
        self.assertEqual(config.global_daily_limit, 275)
        self.assertEqual(config.smtp_rotation_limit, 85)

    def test_smtp_table_shows_effective_rotation_limit(self):
        SmtpProfile.objects.create(
            name="Gmail",
            user="gmail@example.com",
            password="secret",
            daily_limit=300,
        )
        config = AutoConfig.get()
        config.smtp_rotation_limit = 200
        config.save()

        response = self.client.get("/smtp/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Effective Daily Limit")
        self.assertContains(response, "200/day")
        self.assertContains(response, "Global rotation override")


class ScraperParsingTests(TestCase):
    def test_visible_script_words_do_not_mark_search_page_as_blocked(self):
        html = "<script>robot blocked captcha</script><main>Search results</main>"
        self.assertFalse(_is_captcha(html))

    def test_bing_maps_current_business_card_extracts_embedded_data(self):
        card = """
        <div role="listitem" data-type="Business">
          <div class="b_maglistcard" data-entity='{"entity":{
            "title":"Example Fitness",
            "phone":"+91 98765 43210",
            "address":"Mumbai, MH",
            "website":"https://example.test/"
          }}'>
            <h3 class="l_magTitle">Example Fitness</h3>
          </div>
        </div>
        """

        class FakeElement:
            def get_attribute(self, name):
                return card

        result = _extract_from_list_item(FakeElement())
        self.assertEqual(result["name"], "Example Fitness")
        self.assertEqual(result["phone"], "+91 98765 43210")
        self.assertEqual(result["address"], "Mumbai, MH")
        self.assertEqual(result["website"], "https://example.test/")

    def test_bing_images_current_card_extracts_source_page(self):
        html = """
        <a class="iusc" m='{"purl":"https://example.test/product",
        "murl":"https://cdn.example.test/image.jpg","t":"Example product"}'></a>
        """
        result = _parse_bing_images_results(html, 10)
        self.assertEqual(result[0]["website"], "https://example.test/product")
        self.assertEqual(result[0]["name"], "Example product")

    def test_site_enrichment_uses_no_retry_budget(self):
        import scraper.search_scraper as module
        calls = []
        original = module._fetch
        try:
            module._fetch = lambda url, **kwargs: calls.append((url, kwargs)) or ""
            _visit_site_for_details("https://example.test", {}, __import__("threading").Lock())
        finally:
            module._fetch = original
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[1]["retries"] == 0 for call in calls))

    def test_zero_retry_rate_limit_returns_without_backoff(self):
        import scraper.search_scraper as module
        import requests

        class FakeResponse:
            status_code = 429

            def raise_for_status(self):
                raise requests.exceptions.HTTPError(response=self)

        class FakeSession:
            def get(self, *args, **kwargs):
                return FakeResponse()

        original_session = module._get_session
        original_sleep = module.time.sleep
        sleeps = []
        try:
            module._get_session = lambda: FakeSession()
            module.time.sleep = lambda seconds: sleeps.append(seconds)
            self.assertEqual(_fetch("https://example.test", timeout=1, retries=0), "")
        finally:
            module._get_session = original_session
            module.time.sleep = original_sleep
        self.assertEqual(sleeps, [])


class CampaignFollowupTests(TestCase):
    def make_listing(self, email="lead@example.com"):
        return BusinessListing.objects.create(
            name="Test Lead",
            email=email,
            search_query="test",
            location="Test",
        )

    def make_campaign(self, listing, name):
        campaign = EmailCampaign.objects.create(
            name=name,
            subject="Hello {name}",
            body="Hello {name}",
            from_email="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="sender@example.com",
            smtp_password="not-a-real-password",
        )
        EmailSend.objects.create(campaign=campaign, listing=listing)
        return campaign

    def test_creating_campaign_does_not_mark_lead_contacted(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username="campaign-user", password="password")
        self.client.force_login(user)
        listing = self.make_listing()
        response = self.client.post("/campaigns/new/", {
            "name": "Draft campaign",
            "subject": "Hello",
            "body": "Hello {name}",
            "from_email": "sender@example.com",
            "listing_ids": str(listing.id),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactAttempt.objects.filter(listing=listing).count(), 0)
        self.assertEqual(listing.refresh_from_db(), None)
        self.assertEqual(listing.lead_status, "fresh")

    def test_due_followups_advance_then_stop(self):
        from datetime import date
        listing = self.make_listing()
        listing.lead_status = "following_up"
        listing.follow_up_stage = 3
        listing.follow_up_date = date(2026, 7, 23)
        listing.save()

        _advance_due_followups(date(2026, 7, 23))
        listing.refresh_from_db()
        self.assertEqual((listing.lead_status, listing.follow_up_stage, listing.follow_up_date), ("stopped", 4, None))

    def test_non_final_due_followup_waits_for_successful_send(self):
        from datetime import date
        listing = self.make_listing()
        listing.lead_status = "following_up"
        listing.follow_up_stage = 1
        listing.follow_up_date = date(2026, 7, 23)
        listing.save()

        _advance_due_followups(date(2026, 7, 23))
        listing.refresh_from_db()
        self.assertEqual((listing.lead_status, listing.follow_up_stage, listing.follow_up_date), (
            "following_up", 1, date(2026, 7, 23)
        ))

    def test_successful_campaign_sends_advance_email_lifecycle(self):
        from datetime import timedelta
        from unittest.mock import patch
        import scraper.email_sender as sender_module

        class FakeSMTP:
            def sendmail(self, *_args):
                return {}

            def quit(self):
                return None

        listing = self.make_listing()
        with patch.object(sender_module, "_build_smtp", return_value=FakeSMTP()), \
             patch.object(sender_module.time, "sleep", return_value=None):
            for index, (stage, interval) in enumerate(((1, 1), (2, 7), (3, 14)), start=1):
                campaign = self.make_campaign(listing, f"Campaign {index}")
                sender_module.send_campaign(campaign.id)
                listing.refresh_from_db()
                self.assertEqual(listing.lead_status, "following_up")
                self.assertEqual(listing.follow_up_stage, stage)
                self.assertEqual(listing.follow_up_date, timezone.localdate() + timedelta(days=interval))
                self.assertEqual(
                    ContactAttempt.objects.filter(listing=listing, channel="email").count(),
                    index,
                )

        _advance_due_followups(listing.follow_up_date)
        listing.refresh_from_db()
        self.assertEqual((listing.lead_status, listing.follow_up_stage, listing.follow_up_date), ("stopped", 4, None))

    def test_failed_campaign_send_does_not_update_lead_lifecycle(self):
        from unittest.mock import patch
        import scraper.email_sender as sender_module

        class FailedSMTP:
            def sendmail(self, *_args):
                raise sender_module.smtplib.SMTPException("simulated failure")

            def quit(self):
                return None

        listing = self.make_listing("failed@example.com")
        campaign = self.make_campaign(listing, "Failed campaign")
        with patch.object(sender_module, "_build_smtp", return_value=FailedSMTP()), \
             patch.object(sender_module.time, "sleep", return_value=None):
            sender_module.send_campaign(campaign.id)

        listing.refresh_from_db()
        self.assertEqual(listing.lead_status, "fresh")
        self.assertEqual(listing.follow_up_stage, 0)
        self.assertIsNone(listing.follow_up_date)
        self.assertFalse(ContactAttempt.objects.filter(listing=listing).exists())
