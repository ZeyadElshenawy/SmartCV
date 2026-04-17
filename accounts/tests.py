import re

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from .models import User


class PasswordResetFlowTests(TestCase):
    """End-to-end coverage for the Django built-in password reset CBVs.

    We exercise the full flow — request -> email -> confirm link -> set new password ->
    complete — plus the two edge cases that most often regress: a reused link should
    show the expired state, and the old password should stop working after the reset.
    """

    def setUp(self):
        self.email = 'reset_test@example.com'
        self.old_password = 'oldpass12345'
        self.new_password = 'brandnewpass99'
        self.user = User.objects.create_user(
            username=self.email, email=self.email, password=self.old_password,
        )

    def _reset_link_from_email(self, body):
        match = re.search(r'(/accounts/password-reset-confirm/[^\s]+)', body)
        self.assertIsNotNone(match, f'No reset link found in email body:\n{body}')
        return match.group(1)

    def test_request_sends_email_and_redirects_to_done(self):
        response = self.client.post(reverse('password_reset'), {'email': self.email})
        self.assertRedirects(response, reverse('password_reset_done'))

        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, [self.email])
        self.assertIn('SmartCV', msg.subject)
        self.assertIn('/accounts/password-reset-confirm/', msg.body)

    def test_done_page_renders_without_nav_header(self):
        response = self.client.get(reverse('password_reset_done'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Check your')
        # Nav header is suppressed on auth pages — the <nav> element should be absent
        # (the anonymous footer links are a separate section and intentionally remain).
        self.assertNotContains(response, '<nav')

    def test_full_flow_updates_password(self):
        self.client.post(reverse('password_reset'), {'email': self.email})
        link = self._reset_link_from_email(mail.outbox[0].body)

        # PasswordResetConfirmView redirects the token URL to a set-password URL
        # (stores the token in the session, then redirects to a fixed slug).
        response = self.client.get(link, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'new_password1')
        self.assertContains(response, 'new_password2')

        set_url = response.redirect_chain[-1][0]
        response = self.client.post(set_url, {
            'new_password1': self.new_password,
            'new_password2': self.new_password,
        })
        self.assertRedirects(response, reverse('password_reset_complete'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertFalse(self.user.check_password(self.old_password))

    def test_reused_link_shows_invalid_state(self):
        self.client.post(reverse('password_reset'), {'email': self.email})
        link = self._reset_link_from_email(mail.outbox[0].body)

        first = self.client.get(link, follow=True)
        set_url = first.redirect_chain[-1][0]
        self.client.post(set_url, {
            'new_password1': self.new_password,
            'new_password2': self.new_password,
        })

        # Reusing the original link after a successful reset must NOT show the form.
        second = self.client.get(link, follow=True)
        self.assertNotContains(second, 'name="new_password1"')
        self.assertContains(second, 'Request a new link')

    def test_unknown_email_still_redirects_to_done(self):
        """Never leak whether an email exists in the DB."""
        response = self.client.post(
            reverse('password_reset'), {'email': 'nobody@example.com'},
        )
        self.assertRedirects(response, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)

    def test_button_component_does_not_leak_form_context(self):
        """Regression: button.html used to accept `form=` as a param, which
        collided with Django's CBV `form` context var and caused the entire
        Django form HTML to get serialized into the <button form="..."> attr.
        Renamed the param to form_id. Guard against recurrence.
        """
        response = self.client.get(reverse('password_reset'))
        self.assertEqual(response.status_code, 200)
        # The raw bytes should NOT contain a <button whose form= attr wraps HTML.
        self.assertNotIn(b'<button type="submit" form="<', response.content)
        # And the page should contain a clean submit button.
        self.assertContains(response, 'Send reset link')
