# Sliding queue

기본 `sliding_queue`는 임베딩 모델 없이 동작한다. 큐에는 기존과 같이
최근 `sliding_window_words`개 원문 단어를 유지하며, 미확정 STT 가설은
누적하지 않고 교체한다. 확정 이벤트 중복 방지는 `SearchEngine`이 담당한다.

검색 비교에만 Kiwi 형태소 분석과 SQL 발음 표기 사전을 적용한다.
예를 들어 `고객이름`과 `고객 이름`, `셀렉트 디스팅트`와 `SELECT DISTINCT`를
비교할 수 있다. PDF 텍스트와 명시적인 블록 키워드에도 같은 기준을 적용하며,
다른 검색 알고리즘의 토큰화는 변경하지 않는다.

새 발화와 전체 큐의 점수를 2:1로 반영해, 긴 이전 문장이 짧은 새 주제를
가리지 않도록 한다. 이전 큐의 단어만 일치하는 블록은 제외한다.
페이지 문맥 75%, 개별 블록 일치 25%로 후보를 비교한 뒤 기존 페이지 유지
보너스, 뒤로 이동 감점, 최소 점수, 전환 마진을 적용한다. 여러 블록으로
나뉜 설명도 함께 판단하되 결과의 하이라이트는 실제 블록을 가리킨다.

현재 페이지 ±`local_radius_pages` 범위만 탐색한다. 범위 밖으로 자동 복구하는
전역 검색은 수행하지 않는다. 형태소 분석은 OCR/STT의 잘못 인식된 고유명사나
누락된 단어를 복원하지 않으며, 짧은 미확정 발화는 여전히 모호할 수 있다.

회귀 검사:

```powershell
python -m pytest tests/test_sliding_queue_accuracy.py tests/test_search_engine.py tests/test_search_api.py -q
```
