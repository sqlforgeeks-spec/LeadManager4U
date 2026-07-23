from django.test import TestCase
from django.contrib.auth import get_user_model

from .models import AutoConfig, SmtpProfile


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
