"""Known local validation failures get useful messages without exposing model input."""
import copy
import unittest

from pydantic import ValidationError

from generic_agents.setup_understanding import ResultV5, safe_understanding_validation_error


def payload():
    return {
        'domain': 'private-model-output-marker',
        'businesses': [],
        'objects': [{
            'name': 'private-object-marker',
            'fields': [{
                'label': 'private-field-marker', 'kind': '数值', 'required': True,
                'unit': None, 'citations': [], 'inference_reason': 'private-inference-marker',
            }],
            'citations': [], 'inference_reason': '',
        }],
        'relationships': [],
        'questions': [],
    }


class UnderstandingSafeErrorTests(unittest.TestCase):
    def test_incomplete_object_explains_how_to_recover_without_model_text(self):
        value = payload()
        original = copy.deepcopy(value)
        with self.assertRaises(ValidationError) as ctx:
            ResultV5.model_validate(value)
        message = safe_understanding_validation_error(ctx.exception, 5)
        self.assertEqual(message, '对象缺少必填文本识别字段，可本地核对或移除不完整对象；原配置未修改')
        self.assertNotIn('private-', message)
        self.assertEqual(value, original)

    def test_mixed_or_other_validation_errors_remain_generic_and_redacted(self):
        value = payload()
        extra = copy.deepcopy(value['objects'][0])
        extra['fields'][0]['kind'] = 'private-invalid-kind'
        value['objects'].append(extra)
        with self.assertRaises(ValidationError) as ctx:
            ResultV5.model_validate(value)
        self.assertEqual(safe_understanding_validation_error(ctx.exception, 5), '模型建议字段结构未通过核对，原配置未修改')
        self.assertNotIn('private-', safe_understanding_validation_error(ctx.exception, 5))
        self.assertEqual(safe_understanding_validation_error(ValueError('private-error'), 5), '模型建议字段结构未通过核对，原配置未修改')

    def test_older_contracts_do_not_advertise_unavailable_local_review(self):
        with self.assertRaises(ValidationError) as ctx:
            ResultV5.model_validate(payload())
        self.assertEqual(safe_understanding_validation_error(ctx.exception, 3), '模型建议字段结构未通过核对，原配置未修改')
        self.assertIn('本地核对', safe_understanding_validation_error(ctx.exception, 4))


if __name__ == '__main__':
    unittest.main()
