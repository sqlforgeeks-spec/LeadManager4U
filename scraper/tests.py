from django.test import TestCase
from django.contrib.auth import get_user_model

from .models import AutoConfig, SmtpProfile
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
