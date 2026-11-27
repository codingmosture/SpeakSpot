"""Persist prepared document indexes on the backend."""
from collections import defaultdict
from dataclasses import fields
from hashlib import sha256
import logging
import os
from pathlib import Path
import pickle
from tempfile import NamedTemporaryFile

import numpy as np

from analysis_snapshot import PdfAnalysis
from lecture_matching import LectureMatching, LecturePolicy
from pdf import Block
from search import PassageSearch
from text_index import BlockPhrases, DocumentTerms, LectureAnchors, PageIndex

_BACKEND = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get('SPEAKSPOT_ANALYSIS_CACHE_DIR', _BACKEND / '.cache' / 'analysis'))
_VERSION = sha256(b''.join(
    (_BACKEND / source).read_bytes() for source in (
        'analysis_cache.py', 'analysis_snapshot.py', 'search.py', 'text_index.py', 'lecture_matching.py', 'pdf.py',
    )
)).digest()
_LOGGER = logging.getLogger(__name__)

# 발화마다 달라지는 페이지, 문구 기억, 잠금은 제외하고 문서별 색인만 기록한다.
_FIELDS = {
    PdfAnalysis: ('key', 'pages', 'blocks', 'layout', 'batches', 'index', 'geometry'),
    Block: tuple(field.name for field in fields(Block)),
    PassageSearch: ('page_count', '_block_indices', '_block_ids', '_index', '_passage_starts',
                    '_page_vectors', '_block_vectors', '_block_terms', '_lecture'),
    PageIndex: ('pages', 'texts', '_coverage_index'),
    LectureMatching: ('pages', '_policy', '_positions', '_anchors', '_phrases', '_counts', '_groups',
                      '_title_blocks', '_body', '_vectors', '_siblings', '_front', '_lead'),
    LecturePolicy: tuple(field.name for field in fields(LecturePolicy)),
    DocumentTerms: ('_forms', '_lengths'),
    LectureAnchors: ('pages', 'numbers', '_dictionary', '_single_weight', 'columns', '_paired',
                     'ownership', 'weights', 'heading', 'block_indexes'),
    BlockPhrases: ('_dictionary', 'pages'),
}
_RECORDS = {record.__name__: record for record in _FIELDS}


# 문서 분석 생성자를 실행하지 않고 기록한 필드만 복원한다.
def _read_record(name, state):
    record = _RECORDS[name]
    expected = set(_FIELDS[record])
    if record is PassageSearch and state.get('page_count') == 1:
        expected -= {'_passage_starts', '_page_vectors'}
    if state.keys() != expected:
        raise pickle.UnpicklingError('Invalid index fields')
    value = object.__new__(record)
    vars(value).update(state)
    return value


# 숫자와 불리언 배열은 메모리에서 그대로 참조할 수 있게 읽는다.
def _read_array(dtype, shape, data, scalar):
    dtype = np.dtype(dtype)
    if dtype.kind not in 'biuf':
        raise pickle.UnpicklingError('Invalid index array type')
    array = np.frombuffer(data, dtype=dtype).reshape(shape)
    return array[()] if scalar else array


class _IndexWriter(pickle.Pickler):
    # 지원하는 검색 객체와 NumPy 숫자를 명시적인 복원 형식으로 바꾼다.
    def reducer_override(self, value):
        names = _FIELDS.get(type(value))
        if names is not None:
            state = {name: getattr(value, name) for name in names if hasattr(value, name)}
            return _read_record, (type(value).__name__, state)
        if isinstance(value, (np.ndarray, np.generic)):
            if value.dtype.kind not in 'biuf':
                raise pickle.PicklingError('Invalid index array type')
            return _read_array, (value.dtype.str, value.shape, value.tobytes(), isinstance(value, np.generic))
        return NotImplemented


class _IndexReader(pickle.Unpickler):
    # 서버 캐시에서 쓸 수 있는 생성자만 허용한다.
    def find_class(self, module, name):
        if module == __name__ and name in ('_read_record', '_read_array'):
            return {'_read_record': _read_record, '_read_array': _read_array}[name]
        if (module, name) == ('collections', 'defaultdict'):
            return defaultdict
        if module == 'builtins' and name in ('list', 'set'):
            return {'list': list, 'set': set}[name]
        raise pickle.UnpicklingError(f'Unsupported index record: {module}.{name}')


# PDF 내용의 해시로 해당 문서의 서버 캐시를 찾는다.
def cache_file(key):
    return CACHE_DIR / f'{key}.index'


# 같은 분석 코드에서 만든 파일만 읽고 손상됐으면 기존 복원 경로를 사용한다.
def read_index(key):
    try:
        with cache_file(key).open('rb') as cache:
            if cache.read(len(_VERSION)) != _VERSION:
                return None
            analysis = _IndexReader(cache).load()
            if type(analysis) is not PdfAnalysis or analysis.key != key:
                raise pickle.UnpicklingError('Index document mismatch')
            return analysis
    except FileNotFoundError:
        return None
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, KeyError, TypeError, ValueError, OverflowError):
        _LOGGER.warning('Could not read saved search index for %s', key, exc_info=True)
        return None


# 임시 파일에 모두 기록한 뒤 바꿔서 저장 도중에는 이전 파일을 사용할 수 있게 한다.
def save_index(analysis):
    temp = None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=CACHE_DIR, suffix='.tmp', delete=False) as cache:
            temp = Path(cache.name)
            cache.write(_VERSION)
            _IndexWriter(cache, protocol=5).dump(analysis)
        temp.replace(cache_file(analysis.key))
    except (OSError, pickle.PicklingError):
        _LOGGER.warning('Could not persist search index for %s', analysis.key, exc_info=True)
        if temp is not None:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                _LOGGER.warning('Could not remove temporary search index %s', temp, exc_info=True)
