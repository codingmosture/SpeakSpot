"""CLI: python -m app.search.cli --blocks blocks.json --script script.txt --strategy global --model e5-small"""
import argparse
import json
from pathlib import Path

from app.schemas.models import Block
from app.search import SearchEngine, SearchSettings
from app.search.registry import ALGORITHMS, MODELS


def run(blocks_path: str, script_path: str, strategy: str = 'sliding_queue',
        model: str | None = None, radius: int = 5, top_k_pages: int = 3,
        threshold: float = 0.34, min_margin: float = 0.0) -> None:
    payload = json.loads(Path(blocks_path).read_text(encoding='utf-8'))
    if isinstance(payload, dict):
        payload = payload.get('blocks', [])
    blocks = [Block.model_validate(item) for item in payload]
    engine = SearchEngine()
    engine.register_document('cli_doc', blocks)
    engine.create_session('cli_session', 'cli_doc', strategy, model,
                          SearchSettings(local_radius_pages=radius,
                                         global_top_k_pages=top_k_pages,
                                         min_match_score=threshold, min_margin=min_margin))
    lines = Path(script_path).read_text(encoding='utf-8').splitlines()
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        result = engine.search('cli_session', line, f'line_{index:05}')
        print(json.dumps({
            'index': index, 'speech': line, 'result': result.model_dump(),
            'current_page': engine.get_current_page('cli_session'),
        }, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SpeakSpot 검색 알고리즘 비교 CLI')
    parser.add_argument('--blocks', required=True, help='공통 Block[] JSON 경로')
    parser.add_argument('--script', required=True, help='줄바꿈으로 구분된 발화 텍스트')
    parser.add_argument('--strategy', choices=ALGORITHMS, default='sliding_queue')
    parser.add_argument('--model', choices=MODELS, default=None)
    parser.add_argument('--radius', type=int, default=5)
    parser.add_argument('--top-k-pages', type=int, default=3)
    parser.add_argument('--threshold', type=float, default=0.34)
    parser.add_argument('--min-margin', type=float, default=0.0)
    args = parser.parse_args()
    run(args.blocks, args.script, args.strategy, args.model, args.radius,
        args.top_k_pages, args.threshold, args.min_margin)
