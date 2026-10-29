"""Private account aliases come from explicit config, never source-code literals."""
import unittest

from test_processing import source_row, processing


class AccountAliasTests(unittest.TestCase):
    def test_no_config_keeps_the_source_suffix(self):
        result = processing.normalize(source_row('synthetic', family='cmb_debit', account='****1111'))
        self.assertEqual(result['account_last4'], '1111')

    def test_explicit_private_override_is_validated(self):
        row = source_row('synthetic', family='cmb_debit', account='****1111')
        result = processing.normalize(row, {'cmb_debit': '2222'})
        self.assertEqual(result['account_last4'], '2222')
        self.assertEqual(row['account_identifier_raw'], '****1111')
        with self.assertRaises(ValueError):
            processing.normalize(row, {'cmb_debit': 'not-a-card-suffix'})
