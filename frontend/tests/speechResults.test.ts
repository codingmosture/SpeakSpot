import assert from 'node:assert/strict'
import { test } from 'node:test'
import { SpeechResultTracker } from '../src/lib/speechResults.ts'

const result = (transcript: string, isFinal = false) => ({ isFinal, 0: { transcript } })

test('multiple interims form one hypothesis and unchanged events do not replay it', () => {
  const tracker = new SpeechResultTracker()
  const event = { resultIndex: 0, results: [result('고객 테이블'), result('기본키를 봅니다')] }
  const frame = tracker.read(event)
  assert.equal(frame.updates.length, 1)
  assert.equal(frame.updates[0].text, '고객 테이블 기본키를 봅니다')
  assert.equal(tracker.read(event).updates.length, 0)
})

test('finalization commits once and the remaining interim gets its own ID', () => {
  const tracker = new SpeechResultTracker()
  const initial = tracker.read({ resultIndex: 0, results: [result('고객'), result('제품')] })
  const next = tracker.read({ resultIndex: 0, results: [result('고객', true), result('제품')] })
  assert.equal(next.updates.length, 2)
  assert.equal(next.updates[0].utteranceId, initial.updates[0].utteranceId)
  assert.equal(next.updates[0].isFinal, true)
  assert.notEqual(next.updates[1].utteranceId, next.updates[0].utteranceId)
  assert.equal(next.updates[1].text, '제품')
  assert.equal(tracker.read({ resultIndex: 0, results: [result('고객', true), result('제품')] }).updates.length, 0)
})

test('late partial for a finalized result cannot revive it', () => {
  const tracker = new SpeechResultTracker()
  tracker.read({ resultIndex: 0, results: [result('정규화', true)] })
  assert.equal(tracker.read({ resultIndex: 0, results: [result('정규', false)] }).updates.length, 0)
})

test('browser restart reuses result indices with fresh utterance IDs', () => {
  const tracker = new SpeechResultTracker()
  const before = tracker.read({ resultIndex: 0, results: [result('이전 발화')] })
  tracker.resetCycle()
  const after = tracker.read({ resultIndex: 0, results: [result('새 발화', true)] })
  assert.notEqual(before.updates[0].utteranceId, after.updates[0].utteranceId)
  assert.equal(after.caption, '새 발화')
})
