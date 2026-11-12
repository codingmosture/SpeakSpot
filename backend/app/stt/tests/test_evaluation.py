import unittest
from app.stt.tools.evaluate_stt import character_error_rate


class EvaluationTests(unittest.TestCase):
    def test_korean_whitespace_and_substitution(self):
        self.assertEqual(character_error_rate('안녕 하세요', '안녕하세요'), 0)
        self.assertEqual(character_error_rate('가나다라', '가나마라'), 0.25)

    def test_insertion_deletion_and_empty_reference(self):
        self.assertEqual(character_error_rate('가나', ''), 1)
        self.assertEqual(character_error_rate('가', '가나다'), 2)
        with self.assertRaises(ValueError):
            character_error_rate(' ', '가')


if __name__ == '__main__':
    unittest.main()
