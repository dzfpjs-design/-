"""Bounded pixel measurements, never a readability or domain certificate."""
import hashlib
import io
import math
import warnings
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_ANALYSIS_EDGE = 1024
METHOD = 'bounded-luma-v233'


def inspect_image(content, media_type):
    """Return explainable measurements; parser failures remain explicit gaps."""
    failure = {'visual': None, 'checks': [{'kind': 'visual', 'status': 'blocked',
        'message': '未能在安全范围内读取画面像素，不能据此确认画面可用。'}],
        'gaps': [{'kind': 'visual_decode', 'message': '画面格式、字节或像素范围未通过有界解码。'}]}
    if type(content) is not bytes or not 0 < len(content) <= MAX_BYTES or media_type not in {'image/jpeg', 'image/png'}:
        return failure
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                expected = {'image/jpeg': 'JPEG', 'image/png': 'PNG'}[media_type]
                width, height = image.size
                if image.format != expected or not 0 < width*height <= MAX_PIXELS: return failure
                image.load()
                gray = image.convert('L')
                gray.thumbnail((MAX_ANALYSIS_EDGE, MAX_ANALYSIS_EDGE), Image.Resampling.LANCZOS)
                histogram = gray.histogram(); count = gray.width*gray.height
                if len(histogram) != 256 or sum(histogram) != count or not count: return failure
                def quantile(fraction):
                    target = max(1, math.ceil(count*fraction)); total = 0
                    for value, amount in enumerate(histogram):
                        total += amount
                        if total >= target: return value
                    raise ValueError('histogram')
                mean = sum(index*amount for index, amount in enumerate(histogram))/count
                p10, p50, p90 = (quantile(fraction) for fraction in (.1, .5, .9))
                dark, bright = sum(histogram[:16])/count, sum(histogram[240:])/count
                candidates = []
                if mean < 40 or p90 < 60: candidates.append('possible_low_illumination')
                if mean > 220 and bright > .25: candidates.append('possible_high_illumination')
                if p90-p10 < 20: candidates.append('possible_low_contrast')
                visual = {'method': METHOD, 'width': width, 'height': height,
                    'analyzed_width': gray.width, 'analyzed_height': gray.height,
                    'mean_luma': mean, 'p10_luma': p10, 'p50_luma': p50, 'p90_luma': p90,
                    'contrast_p90_p10': p90-p10, 'dark_fraction': dark, 'bright_fraction': bright,
                    'candidates': candidates, 'readability_verified': False}
                return {'visual': visual, 'checks': [{'kind': 'visual', 'status': 'needs_review' if candidates else 'unverified',
                    'message': '已计算亮度与对比分布；这些未校准线索不能认证清晰度、物种可识别性或现场正常。'}],
                    'gaps': ([{'kind': 'visual_candidate', 'message': '亮度或对比分布提示可能需要人工核对。'}] if candidates else [])}
    except (OSError, ValueError, TypeError, Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError):
        return failure


def assessment_record(bundle, request, *, assessed_at):
    """One video-frame source, without borrowing full-video coverage claims."""
    from .contracts import digest
    view = bundle['view']; measured = inspect_image(bundle['content'], view['image']['media_type'])
    gaps = [{'kind': 'source_time', 'message': '画面拍摄时间未核验，批次与接收日期不能代替现场时间。'},
        {'kind': 'coverage', 'message': '只有一张已保存画面，不能证明全视频、全区域或对象覆盖。'},
        {'kind': 'domain_quality', 'message': '技术像素指标不认证物种识别或领域模型效果。'}, *measured['gaps']]
    result = {'source_hash': bundle['source_hash'], 'frame_sha256': bundle['frame_sha256'],
        'integrity_verified': True, 'source_time_verified': False, 'domain_quality_verified': False,
        'current_state_sufficient': False, 'state_conclusion': None,
        'checks': [{'kind': 'originals', 'status': 'passed', 'message': '原件、保存来源及独立接收证明通过核对'},
            {'kind': 'source_time', 'status': 'unverified', 'message': '只知道已保存的批次与接收信息，拍摄时间未知'}, *measured['checks']],
        'gaps': gaps, 'visual': measured['visual'], 'limitations': list(view['limitations']),
        'assessed_at': assessed_at, 'object_label': view['entity']['label'], 'cloud_calls': 0, 'entity_state_updated': False}
    record = {'key': 'DailyTaskFrameV233:'+view['task_id'], 'modality': 'video', 'kind': 'daily_task_frame',
        'name': '已保存任务的单张视频画面', 'source_ref': {'type': 'Evidence', 'id': view['image']['id'],
            'signature': digest(bundle['pin']['image_snapshot'])}, 'task_id': view['task_id'], 'task_title': view['task_title'],
        'entity_ids': [view['entity']['id']], 'association_basis': 'confirmed_daily_task_scope_not_verified_coverage',
        'status': 'saved', 'integrity_verified': True, 'integrity_issue': None,
        'source_times': [{'value': None, 'basis': 'scene_capture_time_unknown'}], 'source_time_verified': False,
        'source_sha256': [bundle['frame_sha256']], 'quality_gaps': gaps, 'review_state': 'unreviewed',
        'metrics': {'saved_frames': 1}, 'record_stage': 'saved_record', 'domain_quality_verified': False,
        'daily_frame_result_v233': result}
    return record
