import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { cacheSavedAnalysis, cacheSavedBlocks, deleteSavedDocument, deleteSavedNote, listSavedDocuments, pdfKey,
  readSavedAnalysis, readSavedDocument, readSession, saveSession } from './savedDocuments.js'
import './style.css'

GlobalWorkerOptions.workerSrc = worker

// 브라우저 방문 기록의 PDF 항목을 읽는다.
function historyDocument() {
  return window.history.state?.speakspotDocument ?? null
}

// 홈에서는 PDF 항목을 추가하고 뷰어 안에서 문서를 바꾸면 현재 항목을 교체한다.
function recordHistoryDocument(doc, replace = Boolean(historyDocument())) {
  const state = { ...window.history.state, speakspotDocument: doc ? { key: doc.key, name: doc.name } : null }
  if (replace) window.history.replaceState(state, '')
  else window.history.pushState(state, '')
}

// 백엔드 응답을 JSON으로 읽고 실패하면 상태 코드와 오류 메시지를 전달한다.
async function request(path, options) {
  const response = await fetch(`/api/${path}`, options)
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw Object.assign(new Error(error.detail || '서버에 연결하지 못했습니다.'), { status: response.status })
  }
  return response.json()
}

const ISLAND_UPLOAD_MIN_MS = 480

// PDF 파일을 분석 API에 보내 문서 ID와 블럭 좌표를 받아온다.
function uploadPdf(file, signal) {
  const form = new FormData()
  form.append('file', file)
  return request('documents', { method: 'POST', body: form, signal })
}

// 메모리 색인, 브라우저에 저장한 색인, 원본 PDF 순으로 검색을 준비한다.
async function restoreSearch(doc, signal) {
  const body = new FormData()
  body.append('key', doc.key)
  body.append('name', doc.name)
  const options = { method: 'POST', body, signal }
  try {
    return { doc: await request('documents/restore', options), refreshSnapshot: false }
  } catch (error) {
    if (error.status !== 404) throw error
  }
  const snapshot = await readSavedAnalysis(doc.key).catch(() => null)
  if (snapshot) {
    body.append('snapshot', snapshot, 'analysis.npz')
    try {
      return { doc: await request('documents/restore', options), refreshSnapshot: false }
    } catch (error) {
      if (![404, 413, 422].includes(error.status)) throw error
    }
  }
  return { doc: await uploadPdf(doc.file, signal), refreshSnapshot: true }
}

// 서버의 색인을 브라우저에 저장해 서버를 재시작한 뒤에도 불러온다.
async function storeSearchSnapshot(doc, refresh = false) {
  if (!refresh && await readSavedAnalysis(doc.key)) return
  const response = await fetch(`/api/documents/${doc.id}/analysis`, { signal: AbortSignal.timeout(10000) })
  if (!response.ok) return
  await cacheSavedAnalysis(doc.key, await response.blob())
}

// 첫 페이지의 원본 크기를 읽어 뷰어 배율의 기준으로 사용한다.
async function readPdf(pdf) {
  const firstPage = await pdf.getPage(1)
  const { width, height } = firstPage.getViewport({ scale: 1 })
  return { document: pdf, firstPageSize: { width, height } }
}

// 발화와 페이지 보정을 문서별 API로 전송한다.
function sendUpdate(doc, update, signal) {
  const correction = update.kind === 'page'
  return request(`documents/${doc.id}/${correction ? 'page' : 'speech'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify(correction ? { page: update.page } : update),
  })
}

// 브라우저 음성 인식의 임시 결과를 갱신하고 확정 결과는 한 번만 보낸다.
class Transcript {
  sequence = 0
  cycle = 0
  ids = new Map()
  finalized = new Set()
  last = ''
  // 재연결에 대비해 인식 주기를 올리고 직전 결과를 비운다.
  reset() { this.cycle++; this.ids.clear(); this.finalized.clear(); this.last = '' }
  // 같은 인식 결과에는 임시 상태에서 확정까지 동일한 발화 ID를 부여한다.
  id(index) {
    if (!this.ids.has(index)) this.ids.set(index, `speech_${++this.sequence}`)
    return this.ids.get(index)
  }
  // 새 확정 발화와 내용이 바뀐 임시 발화를 골라 전송할 목록을 만든다.
  read(event) {
    const { resultIndex, results } = event
    const updates = []
    const partials = []
    let firstPartial
    for (let i = 0; i < results.length; i++) {
      const result = results[i]
      if (this.finalized.has(i) || (result.isFinal && i < resultIndex)) continue
      const text = result[0].transcript.trim()
      if (!text) continue
      if (result.isFinal) {
        this.finalized.add(i)
        updates.push({ text, is_final: true, utterance_id: this.id(i), recognition_cycle: this.cycle })
      } else {
        firstPartial ??= i
        partials.push(text)
      }
    }
    const text = partials.join(' '), id = text ? this.id(firstPartial) : null
    const signature = id ? `${id}:${text}` : ''
    if (signature && signature !== this.last) updates.push({ text, is_final: false, utterance_id: id, recognition_cycle: this.cycle })
    this.last = signature
    return { updates, interim: text }
  }
}

// 자막에 표시할 마지막 문장 조각을 추린다.
function unfinishedSentence(text) {
  return text.split(/(?:[.!?]+[”’"']*(?=\s|$)|[。！？]+[”’"']*)\s*/u).at(-1).trim()
}

const SPEECH_RETRY_DELAYS = [500, 1000, 2000, 4000]
const SPEECH_TIMEOUT = '음성 인식 서비스가 응답하지 않습니다. 마이크와 네트워크를 확인한 뒤 다시 시작해 주세요.'
const SPEECH_ERRORS = {
  'not-allowed': '브라우저에서 이 사이트의 마이크 사용을 허용해 주세요.',
  'service-not-allowed': '브라우저가 음성 인식 서비스 연결을 허용하지 않습니다.',
  'audio-capture': '마이크 입력을 시작하지 못했습니다. 브라우저와 시스템의 입력 장치를 확인해 주세요.',
  'language-not-supported': '이 브라우저에서는 한국어 음성 인식을 사용할 수 없습니다.',
}

// 브라우저 음성 인식의 시작, 종료, 재연결과 자막 갱신을 관리한다.
function useSpeech(onUpdate, onCaption, onError) {
  const [status, setStatus] = useState('idle')
  const session = useRef({ recognition: null, wanted: false, timer: null, failures: 0 })
  const tracker = useRef(new Transcript())
  const callbacks = useRef({ onUpdate, onCaption, onError })
  // 진행 중인 인식 요청에서도 최신 화면 콜백을 호출한다.
  useEffect(() => { callbacks.current = { onUpdate, onCaption, onError } }, [onUpdate, onCaption, onError])
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
  const state = session.current

  // 이전 타이머를 취소하고 다음 시간 제한이나 재시도를 예약한다.
  function waitFor(delay, callback) {
    clearTimeout(state.timer)
    state.timer = setTimeout(callback, delay)
  }

  // 현재 인식 객체와 타이머를 정리하고 필요하면 녹음을 즉시 중단한다.
  function release(abort = false) {
    clearTimeout(state.timer)
    state.timer = null
    const recognition = state.recognition
    state.recognition = null
    if (abort) recognition?.abort()
  }

  // 인식을 대기 상태로 되돌리고 종료 원인이 있으면 오류를 표시한다.
  function finish(message) {
    state.wanted = false
    release(true)
    callbacks.current.onCaption('')
    setStatus('idle')
    if (message) callbacks.current.onError(message)
  }

  // 정상 종료와 연결 오류에 맞춰 재연결하거나 재시도 한도에서 멈춘다.
  function restart(message, abort = false) {
    release(abort)
    callbacks.current.onCaption('')
    if (!state.wanted) {
      setStatus('idle')
      return
    }
    if (message && state.failures >= SPEECH_RETRY_DELAYS.length) return finish(message)
    if (!message) state.failures = 0
    const delay = message ? SPEECH_RETRY_DELAYS[state.failures++] : 250
    setStatus('reconnecting')
    waitFor(delay, connect)
  }

  // 한국어 연속 인식을 시작하고 브라우저 이벤트를 연결한다.
  function connect() {
    if (!state.wanted) return
    try {
      const recognition = new Recognition()
      recognition.lang = 'ko-KR'
      recognition.continuous = true
      recognition.interimResults = true
      state.recognition = recognition
      tracker.current.reset()
      let readyAt = null, receivedSpeech = false, audioEnded = false
      // 교체된 인식 객체가 뒤늦게 보낸 이벤트를 걸러낸다.
      const current = () => state.recognition === recognition
      // 최초 연결을 기록하고 마이크 상태를 듣는 중으로 바꾼다.
      function connected() {
        if (!current() || !state.wanted || readyAt !== null) return
        readyAt = performance.now()
        if (!audioEnded) clearTimeout(state.timer)
        setStatus('listening')
      }
      recognition.onstart = connected
      // 인식 결과를 실시간 자막과 검색 요청에 반영한다.
      recognition.onresult = event => {
        if (!current()) return
        connected()
        const { updates, interim } = tracker.current.read(event)
        const finalText = updates.findLast(update => update.is_final)?.text
        if (state.wanted && (interim || finalText)) {
          callbacks.current.onCaption(interim ? unfinishedSentence(interim) : finalText)
        }
        if (updates.length) {
          receivedSpeech = true
          state.failures = 0
        }
        for (const update of updates) callbacks.current.onUpdate(update)
      }
      // 오디오 입력이 끝난 뒤 인식 응답을 기다릴 시간을 제한한다.
      recognition.onaudioend = () => {
        if (!current() || !state.wanted) return
        audioEnded = true
        waitFor(4000, () => restart(SPEECH_TIMEOUT, true))
      }
      // 마이크 권한 오류는 안내하고 일시적인 연결 오류는 재시도한다.
      recognition.onerror = ({ error }) => {
        if (!current()) return
        if (!state.wanted || SPEECH_ERRORS[error]) return finish(state.wanted ? SPEECH_ERRORS[error] : undefined)
        const message = error === 'network' ? '음성 인식 서비스와 연결이 끊겼습니다. 네트워크를 확인한 뒤 다시 시작해 주세요.' : SPEECH_TIMEOUT
        restart(error === 'no-speech' ? null : message, true)
      }
      // 브라우저 인식이 끝나면 연결이 정상적으로 동작했는지 확인한다.
      recognition.onend = () => {
        if (!current()) return
        const ranNormally = receivedSpeech || (readyAt !== null && performance.now() - readyAt >= 2000)
        restart(ranNormally ? null : SPEECH_TIMEOUT)
      }
      waitFor(15000, () => restart(SPEECH_TIMEOUT, true))
      recognition.start()
    } catch (error) {
      if (error.name === 'NotAllowedError') finish(SPEECH_ERRORS['not-allowed'])
      else restart(SPEECH_TIMEOUT, true)
    }
  }

  // 마이크 시작 요청을 받아 음성 인식을 한 번만 시작한다.
  function start() {
    if (!Recognition || state.wanted || state.recognition) return
    state.wanted = true
    state.failures = 0
    setStatus('connecting')
    connect()
  }

  // 남은 확정 결과를 받을 시간을 두고 음성 인식을 종료한다.
  function stop() {
    state.wanted = false
    if (!state.recognition) return finish()
    callbacks.current.onCaption('')
    setStatus('stopping')
    waitFor(4000, finish)
    try { state.recognition.stop() } catch { finish() }
  }

  // 화면이 사라지면 예약된 재연결과 마이크를 정리한다.
  useEffect(() => () => { state.wanted = false; release(true) }, [])
  return { status, active: status !== 'idle', supported: !!Recognition, start, stop, cancel: finish }
}

const ISLAND_ICONS = {
  upload: <path d="M12 15V4m-4 4 4-4 4 4M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" />,
  mic: <>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-3 0h6" />
  </>,
  stop: <rect x="5.5" y="5.5" width="13" height="13" rx="2.5" fill="currentColor" stroke="none" />,
  prepare: <path d="M13 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9m-6-6v6h6l-6-6M9 13h6m-6 4h6" />,
  retry: <path d="M20 13a8 8 0 1 1-2.3-6.7L21 10m0-5v5h-5" />,
}

const TRASH_ICON = <path d="M4 7h16m-10 4v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3" />

const ISLAND_TEXT = {
  upload: { icon: 'upload', action: 'PDF 업로드' },
  uploading: { icon: 'upload', action: 'PDF 분석 중' },
  preparing: { icon: 'prepare', action: 'PDF 탐색 준비 중' },
  retry: { icon: 'retry', action: '탐색 준비 다시 시도', status: 'PDF 탐색 준비 실패' },
  idle: { icon: 'mic', action: '음성 인식 시작' },
  connecting: { icon: 'stop', action: '음성 인식 중지', status: '마이크 연결 중' },
  listening: { icon: 'stop', action: '음성 인식 중지' },
  reconnecting: { icon: 'stop', action: '음성 인식 중지', status: '마이크 재연결 중' },
  stopping: { icon: 'stop', action: '음성 인식 종료 중', status: '마이크 종료 중' },
}

// PDF 업로드와 음성 인식 상태에 따라 상단 버튼과 자막을 표시한다.
function LectureIsland({ hasDocument, uploading, speech, caption, searchReady,
  searchFailed, onUpload, onToggleSpeech, onRetrySearch }) {
  let mode = 'upload'
  if (uploading) mode = 'uploading'
  else if (hasDocument) {
    if (speech.active || searchReady) mode = speech.status
    else mode = searchFailed ? 'retry' : 'preparing'
  }
  const { icon, action, status } = ISLAND_TEXT[mode]
  const expanded = speech.active || ['uploading', 'preparing', 'retry'].includes(mode)
  const unavailable = mode === 'idle' && !speech.supported
  const disabled = ['uploading', 'preparing', 'stopping'].includes(mode) || unavailable
  const label = unavailable ? '이 브라우저에서는 음성 인식을 사용할 수 없습니다.' : action
  const onClick = mode === 'upload' ? onUpload : mode === 'retry' ? onRetrySearch : onToggleSpeech
  return <div className="island" data-mode={mode} data-expanded={expanded}>
    <button className="island-action" type="button" aria-label={label} aria-disabled={disabled}
      onClick={disabled ? undefined : onClick}>
      <svg className="island-icon" key={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ISLAND_ICONS[icon]}</svg>
    </button>
    <div className="island-info" role="status" aria-label={speech.active ? '실시간 자막' : undefined}>
      {uploading && <>
        <span className="island-title">{uploading}</span>
        <span className="island-status">{action}</span>
      </>}
      {(mode === 'preparing' || mode === 'retry') && <span className="island-status">{status || action}</span>}
      {speech.active && <>
        {caption && <div className="island-caption"><p>{caption}</p></div>}
        {status && <span className="island-status">{status}</span>}
      </>}
    </div>
    {expanded && mode !== 'retry' && <span className="island-indicator" aria-hidden="true">
      {mode === 'listening' && <><i /><i /><i /></>}
    </span>}
  </div>
}

// 설정 값에 맞춰 ON/OFF 상태를 표시하고 클릭하면 전환한다.
function SettingsSwitch({ checked, onChange, children }) {
  return <button type="button" role="switch" aria-checked={checked} className="settings-switch"
    onClick={() => onChange(!checked)}>
    <span>{children}</span>
    <span className="switch-state" aria-hidden="true">{checked ? 'ON' : 'OFF'}</span>
  </button>
}

// 메뉴 버튼의 아이콘에 공통 크기와 선 스타일을 적용한다.
function ActionIcon({ children }) {
  return <svg className="action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
}

// 문서 열기, 음성 검색, 필기 저장을 연결하고 홈과 PDF 뷰어를 표시한다.
function App() {
  const [document, setDocument] = useState(null), [pdf, setPdf] = useState(null)
  const [pageView, setPageView] = useState({ page: 1, alignment: 'top' })
  const { page } = pageView
  const [highlight, setHighlight] = useState(null)
  const [uploading, setUploading] = useState(''), [error, setError] = useState(''), [caption, setCaption] = useState('')
  const [speechError, setSpeechError] = useState('')
  const [notes, setNotes] = useState([]), [activeNote, setActiveNote] = useState(null)
  const [noteMenu, setNoteMenu] = useState(null)
  const [recent, setRecent] = useState([]), [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false), [deleting, setDeleting] = useState(false)
  const [savedAt, setSavedAt] = useState(0)
  const [follow, setFollow] = useState(false), [showBlocks, setShowBlocks] = useState(false)
  const [searchPreparation, setSearchPreparation] = useState('idle')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [historyRequest, setHistoryRequest] = useState(null)
  const viewerPageRef = useRef(1)
  const followRef = useRef(false), matchedPageRef = useRef(null)
  const spaceHeld = useRef(false)
  const input = useRef(null), settingsTrigger = useRef(null), pages = useRef(null)
  const noteMenuRef = useRef(null)
  const currentDocument = useRef(null), currentPdf = useRef(null)
  const busy = useRef(false)
  const work = useRef({ queue: [], sending: false, sequence: 0, generation: 0, revision: 0, controller: null })

  // 이전 PDF의 렌더링과 대기 중인 검색을 정리하고 문서를 교체한다.
  function replacePdf(next) {
    work.current.generation++
    work.current.queue = []
    work.current.controller?.abort()
    spaceHeld.current = false
    setSearchPreparation('idle')
    setNoteMenu(null)
    void currentPdf.current?.destroy()
    currentPdf.current = next
  }

  // 문서를 전환할 때 직전 오류와 저장 안내를 지운다.
  function clearDocumentFeedback() {
    setError(''); setSpeechError(''); setSaveError(''); setSavedAt(0)
  }

  // 현재 페이지를 기록하고 필요하면 해당 페이지로 스크롤한다.
  function showPage(next, alignment) {
    viewerPageRef.current = next
    setPageView({ page: next, alignment })
  }
  // 검색 요청을 순서대로 보내고 만료된 세션을 복구한 뒤 강조와 페이지를 갱신한다.
  async function flush() {
    const state = work.current
    if (state.sending) return
    state.sending = true
    try {
      while (state.queue.length) {
        const update = state.queue.shift(), generation = state.generation
        let doc = currentDocument.current
        if (!doc) continue
        const controller = new AbortController()
        state.controller = controller
        let restoring = false
        // 문서를 바꾸기 전에 보낸 요청이 새 뷰어에 반영되는 것을 막는다.
        const isCurrent = () => generation === state.generation && doc === currentDocument.current
        try {
          let result
          if (doc.id) {
            try {
              result = await sendUpdate(doc, update, controller.signal)
            } catch (err) {
              if (err.status !== 404 || !isCurrent()) throw err
            }
          }
          if (!result) {
            restoring = true
            setSearchPreparation('loading')
            const missingBlocks = !doc.blocks
            const { doc: restored, refreshSnapshot } = await restoreSearch(doc, controller.signal)
            if (!isCurrent()) continue
            const anchor = matchedPageRef.current
            if (update.kind === 'page') result = await sendUpdate(restored, update, controller.signal)
            else if (anchor > 1) await sendUpdate(restored, { kind: 'page', page: anchor }, controller.signal)
            if (!isCurrent()) continue
            doc = { ...doc, ...restored, name: restored.name || doc.name }
            currentDocument.current = doc
            setDocument(doc)
            setSearchPreparation('idle')
            restoring = false
            if (missingBlocks && doc.blocks) void cacheSavedBlocks(doc.key, doc.blocks).catch(() => {})
            void storeSearchSnapshot(doc, refreshSnapshot).catch(() => {})
            setHighlight(null)
            if (update.revision !== state.revision) continue
            if (!result) result = await sendUpdate(doc, update, controller.signal)
          }
          if (!isCurrent() || update.revision !== state.revision) continue
          if (update.kind !== 'page') {
            const block = result.block_id ? doc.blocks?.find(block => block.id === result.block_id) : null
            if (result.block_id || result.page != null) setHighlight(block || null)
            const matchedPage = result.page ?? block?.page
            if (Number.isInteger(matchedPage) && matchedPage >= 1 && matchedPage <= currentPdf.current.numPages) {
              matchedPageRef.current = matchedPage
              if ((followRef.current || spaceHeld.current) && (result.page != null || matchedPage !== viewerPageRef.current)) {
                showPage(matchedPage, spaceHeld.current ? 'bottom' : 'top')
              }
            }
          }
          setError('')
        } catch (err) {
          if (restoring && isCurrent()) setSearchPreparation('failed')
          if (!update.quiet && generation === state.generation && update.revision === state.revision) setError(err.message)
        }
      }
    } finally { state.controller = null; state.sending = false }
  }
  // 같은 발화의 대기 중인 임시 결과를 교체하고 최신 결과를 검색 큐에 넣는다.
  function onSpeech(update) {
    if (!currentDocument.current) return
    const state = work.current
    state.queue = state.queue.filter(item => item.kind === 'page' || item.is_final || item.utterance_id !== update.utterance_id)
    state.queue.push({ ...update, sequence: ++state.sequence, revision: state.revision })
    void flush()
  }
  const speech = useSpeech(onSpeech, setCaption, setSpeechError)
  // 진행 중인 작업이 없을 때 브라우저의 파일 선택창을 연다.
  function selectPdf() {
    if (!busy.current) input.current?.click()
  }
  // 삭제 확인을 받은 저장본을 최근 목록에서 제거하고 서버에도 색인 삭제를 요청한다.
  async function removeRecent({ key, name }) {
    if (busy.current || !window.confirm(`“${name}” 저장본을 삭제할까요? 저장된 필기와 블럭 경계도 삭제됩니다.`)) return
    busy.current = true
    setDeleting(true); setSaveError('')
    try {
      await deleteSavedDocument(key)
      setRecent(documents => documents.filter(doc => doc.key !== key))
      void fetch(`/api/documents/cache/${key}`, { method: 'DELETE', keepalive: true }).catch(() => {})
    } catch {
      setSaveError('저장한 PDF를 삭제하지 못했습니다. 다시 시도해 주세요.')
    } finally {
      busy.current = false
      setDeleting(false)
    }
  }
  // 스크롤 위치를 유지하면서 키보드 입력의 초점을 PDF 뷰어로 돌린다.
  function focusViewer() {
    pages.current?.focus({ preventScroll: true })
  }
  // 마이크 버튼으로 음성 인식을 시작하거나 중지한다.
  function toggleSpeech(event) {
    if (busy.current) return
    if (!speech.active && (!currentDocument.current?.id || searchPreparation !== 'idle')) return
    setSpeechError('')
    if (speech.active) speech.stop()
    else speech.start()
    if (event.detail) focusViewer()
  }
  // 열려 있는 필기 가운데 아직 저장되지 않은 내용만 반환한다.
  function changedNote() {
    if (!activeNote) return
    const saved = notes.find(note => note.id === activeNote.id)
    if (saved?.text !== activeNote.text) return activeNote
  }
  // PDF, 현재 페이지, 필기를 저장하고 결과를 화면에 반영한다.
  async function writeSession(note) {
    try {
      const session = await saveSession(currentDocument.current, viewerPageRef.current, note)
      setNotes(session.notes)
      return true
    } catch {
      setSaveError('PDF와 필기를 저장하지 못했습니다. 브라우저 저장 공간을 확인한 뒤 다시 시도해 주세요.')
      return false
    }
  }
  // 필기를 저장하거나 삭제하는 동안 다른 문서 작업을 막는다.
  async function withSaveLock(operation) {
    if (busy.current) return false
    busy.current = true
    setSaving(true); setSaveError('')
    try {
      return await operation()
    } finally {
      busy.current = false
      setSaving(false)
    }
  }
  // 앱의 저장 상태를 표시하면서 필기의 변경 사항을 기록한다.
  function persistNote(note) {
    return withSaveLock(() => writeSession(note))
  }
  // 작성 중인 필기를 저장한 뒤 지정한 필기를 열거나 편집창을 접는다.
  async function editNote(next) {
    if (busy.current) return false
    const pending = changedNote()
    if (pending && !(await persistNote(pending))) return false
    setActiveNote(next)
    return true
  }
  // 클릭한 PDF 좌표에 빈 필기를 만들고 입력창을 연다.
  function addNote(page, x, y) {
    void editNote({ id: crypto.randomUUID(), page, x, y, text: '' })
  }
  // 필기를 펼치거나 접고 클릭으로 접었으면 뷰어에 초점을 돌린다.
  async function toggleNote(note, fromPointer = false) {
    const closing = activeNote?.id === note.id
    const changed = await editNote(closing ? null : note)
    if (closing && changed && fromPointer) focusViewer()
    return changed
  }
  // 드래그로 옮긴 좌표를 저장하고 열린 편집창의 위치도 맞춘다.
  async function moveNote({ id, page }, position) {
    if (await persistNote({ id, page, ...position })) {
      setActiveNote(active => active?.id === id ? { ...active, ...position } : active)
    }
  }
  // 마우스 위치에 선택한 필기의 삭제 메뉴를 연다.
  function openNoteMenu(id, position, returnFocus) {
    if (!busy.current) setNoteMenu({ id, ...position, returnFocus })
  }
  // 선택한 필기를 저장본에서 지우고 열린 필기와 메뉴를 정리한다.
  function removeNote(id) {
    return withSaveLock(async () => {
      try {
        setNotes(await deleteSavedNote(currentDocument.current.key, id))
        setActiveNote(note => note?.id === id ? null : note)
        setNoteMenu(null)
        focusViewer()
      } catch {
        setSaveError('필기를 삭제하지 못했습니다. 다시 시도해 주세요.')
      }
    })
  }
  // 현재 문서를 저장하고 저장 완료 표시를 켠다.
  async function saveDocument() {
    setSavedAt(0)
    if (await persistNote(changedNote())) setSavedAt(Date.now())
  }
  // 미저장 필기를 확인하고 문서와 마이크를 정리해 홈으로 돌아간다.
  function closeDocument() {
    if (changedNote() && !window.confirm('저장하지 않은 필기가 있습니다. 나갈까요?')) return false
    replacePdf(null)
    currentDocument.current = null
    speech.cancel()
    setSettingsOpen(false)
    setPdf(null); setDocument(null); setNotes([]); setActiveNote(null)
    clearDocumentFeedback()
    return true
  }
  // 나가기 메뉴에서도 브라우저 기록의 홈 항목으로 돌아간다.
  function exitDocument() {
    if (!currentDocument.current || busy.current || !closeDocument()) return
    if (historyDocument()) window.history.back()
  }
  // 새 PDF를 분석해 자동 저장하거나 최근 저장본의 페이지와 필기를 복원한다.
  async function open({ file, saved, fromHistory = false }) {
    if ((!file && !saved) || busy.current || speech.active) return
    const historyKey = historyDocument()?.key
    busy.current = true
    setSettingsOpen(false)
    setUploading(file?.name || saved.name)
    clearDocumentFeedback()
    const showUpload = saved ? null : new Promise(resolve => setTimeout(resolve, ISLAND_UPLOAD_MIN_MS))
    let loading
    try {
      const pending = changedNote()
      if (pending && !(await writeSession(pending))) return
      const stored = saved ? await readSavedDocument(saved.key) : null
      file ??= stored.file
      const data = await file.arrayBuffer()
      const key = saved?.key ?? await pdfKey(data)
      loading = getDocument({ data: new Uint8Array(data) })
      const [local, result, session] = await Promise.all([
        loading.promise.then(readPdf),
        saved ? null : uploadPdf(file),
        stored?.session ?? readSession(key).catch(() => null),
        showUpload,
      ])
      // 불러오는 동안 뒤로 이동했으면 늦게 도착한 PDF로 화면을 바꾸지 않는다.
      if (historyDocument()?.key !== historyKey) {
        void loading.destroy()
        return
      }
      const doc = { ...result, name: result?.name || file.name, file, key, blocks: result?.blocks ?? stored?.blocks }
      const initialPage = Math.min(session?.page ?? 1, local.document.numPages)
      if (!fromHistory) recordHistoryDocument(doc)
      replacePdf(local.document)
      currentDocument.current = doc
      matchedPageRef.current = 1
      setPdf({ ...local, generation: work.current.generation }); setDocument(doc)
      setNotes(session?.notes ?? []); setActiveNote(null)
      showPage(initialPage, 'top'); setHighlight(null); setCaption('')
      if (saved || initialPage > 1) correctPage(initialPage, Boolean(saved))
      if (!saved && await writeSession()) await storeSearchSnapshot(doc).catch(() => {})
    } catch (err) {
      void loading?.destroy()
      if (historyDocument()?.key !== historyKey) return
      if (fromHistory) recordHistoryDocument(currentDocument.current, true)
      setError(err.message || 'PDF를 열지 못했습니다.')
    } finally {
      busy.current = false
      setUploading('')
    }
  }
  // 뒤로가기나 앞으로가기를 받으면 해당 브라우저 항목을 기억한다.
  useEffect(() => {
    // 작업 중에도 가장 최근에 요청한 방문 기록을 놓치지 않는다.
    function onPopState() {
      setHistoryRequest({ doc: historyDocument() })
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])
  // 작업이 끝난 후 방문 기록에 맞춰 홈으로 돌아가거나 저장한 PDF를 다시 연다.
  const applyHistoryRequest = useEffectEvent(() => {
    if (!historyRequest || busy.current) return
    setHistoryRequest(null)
    const doc = historyRequest.doc
    if (!doc) {
      if (currentDocument.current && !closeDocument()) window.history.forward()
    } else if (currentDocument.current?.key !== doc.key) {
      void open({ saved: doc, fromHistory: true })
    }
  })
  // 저장이나 업로드 때문에 보류했던 브라우저 이동을 다시 처리한다.
  useEffect(() => { applyHistoryRequest() }, [historyRequest, uploading, saving, deleting])
  // 앱을 닫을 때 진행 중인 검색 응답을 무효화하고 PDF 리소스를 해제한다.
  useEffect(() => () => {
    work.current.generation++
    work.current.queue = []
    work.current.controller?.abort()
    void currentPdf.current?.destroy()
  }, [])
  // 저장 완료 표시를 잠시 보여준 뒤 지운다.
  useEffect(() => {
    if (!savedAt) return
    const timer = setTimeout(() => setSavedAt(0), 2200)
    return () => clearTimeout(timer)
  }, [savedAt])
  // 홈이 표시될 때 브라우저에 저장된 최근 PDF를 불러온다.
  useEffect(() => {
    if (pdf) return
    let current = true
    listSavedDocuments().then(
      documents => { if (current) setRecent(documents) },
      () => { if (current) setSaveError('최근에 저장한 PDF 목록을 불러오지 못했습니다.') },
    )
    return () => { current = false }
  }, [pdf])
  // 필기 메뉴에 초점을 주고 바깥을 누르거나 PDF 위치가 바뀌면 접는다.
  useEffect(() => {
    if (!noteMenu) return
    const element = noteMenuRef.current
    element.firstElementChild.focus({ preventScroll: true })

    // 메뉴를 닫아 이후 입력을 PDF 뷰어에서 받을 수 있게 한다.
    function dismiss() { setNoteMenu(null) }
    // 메뉴 안에서 삭제를 누를 때는 클릭이 처리될 때까지 기다린다.
    function dismissOutside(event) {
      if (!element.contains(event.target)) dismiss()
    }

    window.addEventListener('pointerdown', dismissOutside)
    window.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    return () => {
      window.removeEventListener('pointerdown', dismissOutside)
      window.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
    }
  }, [noteMenu])
  // 가장 최근에 발화가 매칭된 페이지로 한 번 이동한다.
  function jumpToSpeech(alignment = 'bottom') {
    if (matchedPageRef.current != null) showPage(matchedPageRef.current, alignment)
  }
  // 발화 자동 따라가기를 바꾸고 켜면 최근 매칭 페이지로 이동한다.
  function changeFollow(enabled) {
    followRef.current = enabled
    setFollow(enabled)
    if (enabled) jumpToSpeech('top')
  }
  // 블럭 경계 표시를 바꾸고 이전 좌표 로드가 실패했으면 다시 요청한다.
  function changeBlockVisibility(enabled) {
    setShowBlocks(enabled)
    if (enabled && !document?.blocks && searchPreparation === 'failed' && !busy.current) {
      correctPage(matchedPageRef.current ?? viewerPageRef.current, true)
    }
  }
  // 현재 검색 기준을 지정한 페이지로 바꾸고 페이지 보정 요청을 예약한다.
  function correctPage(next, quiet = false) {
    if (!currentDocument.current) return
    const state = work.current
    const revision = ++state.revision
    // 발화와 페이지 보정을 같은 순서로 보내고 이전 보정 기준의 응답은 무시한다.
    if (state.queue.at(-1)?.kind === 'page') state.queue.pop()
    state.queue.push({ kind: 'page', page: next, revision, quiet })
    matchedPageRef.current = next
    setHighlight(null)
    void flush()
  }
  // 실제 표시 페이지가 바뀐 경우에만 뷰어 위치를 갱신한다.
  function changeViewerPage(next, alignment = 'bottom') {
    if (next === viewerPageRef.current) return
    showPage(next, alignment)
  }
  // 지금 보고 있는 페이지를 발화 검색의 기준으로 삼는다.
  function resetSpeechToViewer() {
    correctPage(viewerPageRef.current)
  }
  // 준비에 실패한 문서는 마이크를 켜지 않고 검색 세션만 다시 연다.
  function retrySearch() {
    if (!busy.current) correctPage(viewerPageRef.current, true)
  }
  // 더보기 메뉴를 닫고 단축 동작을 실행한 뒤 뷰어에 초점을 돌린다.
  function runMenuAction(action) {
    setSettingsOpen(false)
    action()
    focusViewer()
  }
  // 페이지 버튼으로 이동하고 마우스로 눌렀다면 뷰어에 초점을 돌린다.
  function navigateFromButton(event, next) {
    changeViewerPage(next)
    if (event.detail) focusViewer()
  }
  // Space를 누르면 최근 발화 위치로 이동하고 누르고 있는 동안 발화를 따라간다.
  const handleShortcut = useEffectEvent(event => {
    if (!currentDocument.current || event.defaultPrevented ||
        event.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return
    const target = event.target
    if (target instanceof HTMLElement && (target.isContentEditable || target.closest('input, textarea, select'))) return
    if (event.code === 'Space' || event.key === ' ') {
      if (target instanceof Element && target.closest('button, a[href]')) return
      event.preventDefault()
      if (!event.repeat && !busy.current && !noteMenu) {
        spaceHeld.current = true
        jumpToSpeech()
      }
    } else if (!event.repeat && !busy.current && !noteMenu && (event.code === 'KeyC' || event.key.toLowerCase() === 'c')) {
      event.preventDefault()
      resetSpeechToViewer()
    }
  })
  // 앱의 키보드 단축키 리스너를 등록하고 화면을 닫을 때 해제한다.
  useEffect(() => {
    // 브라우저 키 이벤트를 최신 단축키 처리기로 넘긴다.
    const onKeyDown = event => handleShortcut(event)
    // Space를 떼거나 창 초점을 잃으면 누르는 동안의 따라가기를 끝낸다.
    const stopHoldingSpace = () => { spaceHeld.current = false }
    const onKeyUp = event => { if (event.code === 'Space' || event.key === ' ') stopHoldingSpace() }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', stopHoldingSpace)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', stopHoldingSpace)
    }
  }, [])
  const searchReady = Boolean(document?.id) && searchPreparation === 'idle'

  return <div className="app" data-document={Boolean(pdf)}>
    <input ref={input} type="file" accept="application/pdf,.pdf" hidden aria-label="PDF 파일 선택"
      onChange={event => { void open({ file: event.target.files?.[0] }); event.target.value = '' }} />
    <LectureIsland hasDocument={Boolean(pdf)} uploading={uploading} speech={speech} caption={caption}
      searchReady={searchReady} searchFailed={searchPreparation === 'failed'}
      onUpload={selectPdf} onToggleSpeech={toggleSpeech} onRetrySearch={retrySearch} />
    {(saveError || speechError || error) && <div className="error" role="alert">{saveError || speechError || error}</div>}

    {!pdf && !uploading && <main className="welcome">
      <h1>SpeakSpot</h1>
      <p>강의 PDF를 올리고 설명하면 관련 페이지를 자동으로 찾아줍니다.</p>
      {recent.length > 0 && <section className="recent" aria-labelledby="recent-title">
        <h2 id="recent-title">최근 저장한 PDF</h2>
        <ul>
          {recent.map(doc => <li className="recent-item" key={doc.key}>
            <button className="recent-open" type="button" disabled={deleting} onClick={() => void open({ saved: doc })}>
              <span>{doc.name}</span>
              <small>{doc.page}페이지</small>
            </button>
            <button className="recent-delete" type="button" disabled={deleting}
              aria-label={`${doc.name} 저장본 삭제`} onClick={() => void removeRecent(doc)}>
              <ActionIcon>{TRASH_ICON}</ActionIcon>
            </button>
          </li>)}
        </ul>
      </section>}
    </main>}

    {pdf && <>
      <main className="document">
        <Viewer key={pdf.generation} container={pages} pdf={pdf.document} firstPageSize={pdf.firstPageSize} pageView={pageView}
          onPage={changeViewerPage} highlight={highlight} boundaries={showBlocks ? document.blocks || [] : []}
          notes={notes} activeNote={activeNote} onAddNote={addNote} onToggleNote={toggleNote} onMoveNote={moveNote}
          onNoteMenu={openNoteMenu} onNoteText={text => setActiveNote({ ...activeNote, text })}
          noteReadOnly={saving || Boolean(uploading)} />
      </main>
      {noteMenu && <div className="note-context-menu" ref={noteMenuRef} role="menu" aria-label="필기 작업"
        style={{ '--menu-x': `${noteMenu.x}px`, '--menu-y': `${noteMenu.y}px` }} onBlur={event => {
          if (!event.currentTarget.contains(event.relatedTarget)) setNoteMenu(null)
        }} onKeyDown={event => {
          // Esc로 메뉴만 닫고 원래 필기 아이콘이나 입력창에 초점을 돌린다.
          if (event.key !== 'Escape') return
          event.preventDefault()
          setNoteMenu(null)
          noteMenu.returnFocus?.focus({ preventScroll: true })
        }}>
        <button type="button" role="menuitem" aria-disabled={saving} onClick={() => void removeNote(noteMenu.id)}>
          <ActionIcon>{TRASH_ICON}</ActionIcon>
          필기 삭제
        </button>
      </div>}
      <div className="viewer-controls">
        <nav className="page-navigation" aria-label="페이지 이동">
          <button type="button" disabled={page === 1} onClick={event => navigateFromButton(event, page - 1)} aria-label="이전 페이지">
            <span className="chevron" aria-hidden="true" />
          </button>
          <span>{page} / {pdf.document.numPages}</span>
          <button type="button" disabled={page === pdf.document.numPages}
            onClick={event => navigateFromButton(event, page + 1)} aria-label="다음 페이지">
            <span className="chevron" aria-hidden="true" />
          </button>
        </nav>
        <div className="viewer-settings expanding-surface" data-expanded={settingsOpen} onKeyDown={event => {
          // Esc로 더보기를 접고 더보기 버튼에 초점을 돌린다.
          if (settingsOpen && event.key === 'Escape') {
            event.preventDefault()
            setSettingsOpen(false)
            settingsTrigger.current?.focus()
          }
        }}>
          <button ref={settingsTrigger} className="settings-trigger" type="button" aria-label="더보기"
            aria-expanded={settingsOpen} aria-controls="viewer-settings-panel" onClick={() => setSettingsOpen(open => !open)}>
            <span aria-hidden="true">⋯</span>
          </button>
          <div className="settings-panel expand-content" id="viewer-settings-panel" inert={!settingsOpen}>
            <div className="settings-content">
              <div className="settings-group">
                <SettingsSwitch checked={follow} onChange={changeFollow}>발화 따라가기</SettingsSwitch>
                <SettingsSwitch checked={showBlocks} onChange={changeBlockVisibility}>블럭 경계 표시</SettingsSwitch>
                {showBlocks && !document.blocks && <p className="settings-status" role="status">
                  {searchPreparation === 'failed' ? '경계를 불러오지 못했습니다. 다시 켜서 재시도하세요.' : '블럭 경계를 준비 중입니다.'}
                </p>}
                {showBlocks && document.blocks && !document.blocks.some(block => block.page === page) &&
                  <p className="settings-status" role="status">이 페이지에는 추출된 블럭이 없습니다.</p>}
              </div>
              <div className="settings-group">
                <p className="shortcut-help"><span>필기 추가</span><kbd>Shift+클릭</kbd></p>
                <button className="shortcut-action" type="button" aria-keyshortcuts="Space" disabled={Boolean(uploading) || saving}
                  onClick={() => runMenuAction(jumpToSpeech)}>
                  <span>발화 위치로 이동</span><kbd aria-hidden="true">Space</kbd>
                </button>
                <p className="shortcut-help"><span>발화 따라가기</span><kbd>Space 길게</kbd></p>
                <button className="shortcut-action" type="button" aria-keyshortcuts="C" disabled={Boolean(uploading) || saving}
                  onClick={() => runMenuAction(resetSpeechToViewer)}>
                  <span>현재 페이지로 초기화</span><kbd aria-hidden="true">C</kbd>
                </button>
              </div>
              <div className="settings-group file-actions">
                <button type="button" disabled={Boolean(uploading) || saving || speech.active} onClick={selectPdf}>
                  <ActionIcon>{ISLAND_ICONS.upload}</ActionIcon>
                  다른 PDF 열기
                </button>
                <button type="button" disabled={Boolean(uploading) || saving} onClick={() => void saveDocument()}>
                  <ActionIcon><path d="M5 4h12l3 3v13H4V4h1zm3 0v6h8V4M8 20v-6h8v6" /></ActionIcon>
                  저장
                  <span className="save-status" role="status">{savedAt ? '저장됨' : ''}</span>
                </button>
                <button type="button" disabled={Boolean(uploading) || saving} onClick={exitDocument}>
                  <ActionIcon><path d="M12 4H5v16h7m-2-8h10m-4-4 4 4-4 4" /></ActionIcon>
                  나가기
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>}
  </div>
}

// 스크롤 컨테이너 안에서 페이지 하단의 좌표를 구한다.
function pageBottom(element) {
  return element.offsetTop + element.offsetHeight
}

// 스크롤 좌표에 해당하는 페이지를 이진 검색으로 찾는다.
function pageIndexAt(pages, offset) {
  let first = 0, last = pages.length - 1
  while (first < last) {
    const middle = Math.floor((first + last) / 2)
    if (pageBottom(pages[middle]) <= offset) first = middle + 1
    else last = middle
  }
  return first
}

// 하단에 맞춰 이동한 뒤에는 뷰어 하단과 가장 가까운 페이지를 찾는다.
function pageIndexNearBottom(pages, offset) {
  const index = pageIndexAt(pages, offset)
  if (index === 0) return 0
  const before = Math.abs(offset - pageBottom(pages[index - 1]))
  const after = Math.abs(offset - pageBottom(pages[index]))
  return before < after ? index - 1 : index
}

// PDF 페이지를 배치하고 스크롤 위치와 현재 페이지를 서로 맞춘다.
function Viewer({ container, pdf, firstPageSize, pageView, onPage, highlight, boundaries, notes, activeNote, onAddNote,
  onToggleNote, onMoveNote, onNoteMenu, onNoteText, noteReadOnly }) {
  const { page, alignment } = pageView
  const visible = useRef(page)
  const previousSize = useRef(null)
  const scrollPosition = useRef({ alignment: 'top', lead: 0, top: null })
  const [pageSize, setPageSize] = useState({ width: 0, height: 0, viewportHeight: 0 })
  const [renderRange, setRenderRange] = useState({ first: 0, last: 1 })
  const scale = Math.min(pageSize.width / firstPageSize.width, pageSize.height / firstPageSize.height)
  const width = Math.floor(firstPageSize.width * scale)
  const height = Math.floor(firstPageSize.height * scale)

  // 화면에 보이는 페이지와 그 앞뒤 한 페이지에만 캔버스를 만든다.
  function renderVisiblePages(element) {
    const first = Math.max(0, pageIndexAt(element.children, element.scrollTop) - 1)
    const last = Math.min(pdf.numPages - 1, pageIndexAt(element.children, element.scrollTop + element.clientHeight) + 1)
    setRenderRange(range => range.first === first && range.last === last ? range : { first, last })
  }

  // 뷰어 크기를 관찰해 화면 배율을 맞추고 사라지면 관찰을 중단한다.
  useEffect(() => {
    const element = container.current
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setPageSize({ width, height, viewportHeight: element.clientHeight })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // 페이지 이동이나 크기 변경을 화면이 그려지기 전에 스크롤에 반영한다.
  useLayoutEffect(() => {
    if (!pageSize.width || !pageSize.height) return
    const element = container.current
    if (alignment || previousSize.current !== pageSize) {
      visible.current = page
      previousSize.current = pageSize
      const position = scrollPosition.current
      const anchor = alignment ?? position.alignment
      const target = element.children[page - 1]
      const bottomAligned = anchor === 'bottom'
      // 짧은 앞쪽 페이지도 하단에 맞출 수 있도록 필요한 만큼만 시작 여백을 더한다.
      const lead = bottomAligned ? Math.max(0, element.clientHeight - pageBottom(target) + position.lead) : 0
      element.style.setProperty('--page-scroll-lead', `${lead}px`)
      element.scrollTop = bottomAligned ? pageBottom(target) - element.clientHeight : (page === 1 ? 0 : target.offsetTop)
      scrollPosition.current = { alignment: anchor, lead, top: element.scrollTop }
    }
    renderVisiblePages(element)
  }, [pageView, pageSize])

  // 여러 페이지가 있으면 마지막 페이지도 뷰어 상단까지 스크롤할 수 있게 한다.
  const tail = pdf.numPages > 1 ? Math.max(0, pageSize.viewportHeight - height) : 0
  return <div className="pages" ref={container} tabIndex={0} aria-label="PDF 페이지"
    style={{ '--page-scroll-tail': `${tail}px` }}
    onScroll={event => {
      const element = event.currentTarget
      renderVisiblePages(element)
      const position = scrollPosition.current
      if (element.scrollTop === position.top) return
      position.top = null
      const index = position.alignment === 'bottom'
        ? pageIndexNearBottom(element.children, element.scrollTop + element.clientHeight)
        : pageIndexAt(element.children, element.scrollTop)
      const next = index + 1
      if (next !== visible.current) { visible.current = next; onPage(next, null) }
    }} onKeyDown={event => {
      // 뷰어에 초점이 있을 때 방향키와 Page Up/Down으로 이동한다.
      if (event.target !== event.currentTarget) return
      const delta = ['ArrowDown','PageDown'].includes(event.key) ? 1 : ['ArrowUp','PageUp'].includes(event.key) ? -1 : 0
      if (delta) { event.preventDefault(); onPage(Math.max(1,Math.min(pdf.numPages,page+delta))) }
    }}>
    {Array.from({ length: pdf.numPages }, (_, index) => <div className="page" key={index} aria-label={`${index+1}페이지`}>
      <div className="sheet" style={{ width, height }}>
        {index >= renderRange.first && index <= renderRange.last
          ? <Page pdf={pdf} number={index + 1} width={width} height={height}
            rects={highlight?.page === index + 1 ? highlight.rects : []}
            blocks={boundaries.filter(block => block.page === index + 1)}
            notes={notes.filter(note => note.page === index + 1)}
            activeNote={activeNote?.page === index + 1 ? activeNote : null}
            onAddNote={onAddNote} onToggleNote={onToggleNote} onMoveNote={onMoveNote}
            onNoteMenu={onNoteMenu} onNoteText={onNoteText} noteReadOnly={noteReadOnly} />
          : <span className="page-placeholder">{index + 1}</span>}
      </div>
    </div>)}
  </div>
}

// PDF 상대 좌표를 페이지 크기에 맞는 CSS 백분율 사각형으로 바꾼다.
function boxStyle([x0,y0,x1,y1]) {
  return { left:`${x0*100}%`, top:`${y0*100}%`, width:`${(x1-x0)*100}%`, height:`${(y1-y0)*100}%` }
}

// PDF에 고정된 필기 아이콘과 입력창을 표시하고 드래그를 처리한다.
function PdfNote({ note, expanded, onToggle, onMove, onMenu, onText, readOnly, pageWidth }) {
  const marker = useRef(null), editor = useRef(null)
  const drag = useRef(null), suppressClick = useRef(false)
  const [preview, setPreview] = useState(null)
  const { x, y } = preview ?? note
  const editorWidth = Math.min(270, pageWidth)
  const editorLeft = Math.max(0, Math.min(pageWidth - editorWidth, x * pageWidth - editorWidth / 2))
  const markerX = Math.max(12, Math.min(pageWidth - 12, x * pageWidth))
  const position = {
    '--note-x': `${x * 100}%`, '--note-y': `${y * 100}%`,
    '--editor-width': `${editorWidth}px`, '--editor-left': `${editorLeft}px`,
    '--marker-offset': `${markerX - editorLeft - 12}px`,
  }
  const label = expanded ? '필기 접기' : '필기 열기'
  // 필기를 펼치면 PDF를 스크롤하지 않고 입력창에 초점을 둔다.
  useLayoutEffect(() => {
    if (expanded) editor.current?.focus({ preventScroll: true })
  }, [expanded])

  // 드래그 시작 위치와 아이콘 중심에서 포인터까지의 거리를 기록한다.
  function startDrag(event) {
    if (readOnly || !event.isPrimary || event.button !== 0) return
    const bounds = event.currentTarget.getBoundingClientRect()
    drag.current = {
      id: event.pointerId,
      x: event.clientX, y: event.clientY,
      offsetX: event.clientX - (bounds.left + bounds.width / 2),
      offsetY: event.clientY - (bounds.top + bounds.height / 2),
      moved: false,
    }
    suppressClick.current = false
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  // 아이콘 중심을 기준으로 포인터 위치를 PDF 페이지의 상대 좌표로 바꾼다.
  function pointerPosition(event, start) {
    const { left, top, width, height } = event.currentTarget.closest('.sheet').getBoundingClientRect()
    // 좌표가 PDF 페이지 밖으로 벗어나지 않게 한다.
    const clamp = value => Math.max(0, Math.min(1, value))
    return {
      x: clamp((event.clientX - left - start.offsetX) / width),
      y: clamp((event.clientY - top - start.offsetY) / height),
    }
  }

  // 클릭으로 볼 수 있는 작은 움직임을 제외하고 드래그 위치를 미리 표시한다.
  function updateDrag(event) {
    const start = drag.current
    if (!start || start.id !== event.pointerId) return
    if (!start.moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 5) return
    start.moved = true
    setPreview(pointerPosition(event, start))
  }

  // 드래그가 끝난 좌표를 저장하고 펼친 필기의 입력 초점을 복원한다.
  async function finishDrag(event) {
    const start = drag.current
    if (!start || start.id !== event.pointerId) return
    drag.current = null
    if (!start.moved) return
    suppressClick.current = true
    await onMove(note, pointerPosition(event, start))
    setPreview(null)
    if (expanded) editor.current?.focus({ preventScroll: true })
  }

  // 포인터가 취소되면 미리보기 위치를 지워 저장된 자리로 돌아간다.
  function cancelDrag(event) {
    if (drag.current?.id !== event.pointerId) return
    drag.current = null
    setPreview(null)
  }

  // 브라우저 기본 메뉴 대신 오른쪽 클릭 위치에 필기 메뉴를 연다.
  function openContextMenu(event) {
    event.preventDefault()
    const bounds = marker.current.getBoundingClientRect()
    const fromKeyboard = event.clientX === 0 && event.clientY === 0
    onMenu(note.id, {
      x: fromKeyboard ? bounds.left : event.clientX,
      y: fromKeyboard ? bounds.bottom : event.clientY,
    }, expanded ? editor.current : marker.current)
  }

  return <div className="note-card expanding-surface" data-expanded={expanded} data-above={y > .5}
    data-dragging={Boolean(preview)} style={position} onContextMenu={openContextMenu}>
    <button ref={marker} className="note-marker" type="button" aria-label={label}
      aria-expanded={expanded} aria-controls={`note-${note.id}`}
      onPointerDown={startDrag} onPointerMove={updateDrag} onPointerUp={finishDrag}
      onPointerCancel={cancelDrag} onLostPointerCapture={cancelDrag}
      onClick={event => {
        // 드래그 직후에 발생한 클릭은 필기 열기로 처리하지 않는다.
        if (event.detail && suppressClick.current) return
        void onToggle(note, event.detail > 0)
      }}>
      <i aria-hidden="true" />
    </button>
    <div className="note-editor expand-content" id={`note-${note.id}`} inert={!expanded}>
      <textarea ref={editor} aria-label="필기 내용" value={note.text}
        readOnly={readOnly} onChange={event => onText(event.target.value)} onKeyDown={async event => {
          // 한글 조합과 줄바꿈을 제외한 Enter로 필기를 저장하고 접는다.
          if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
          event.preventDefault()
          if (await onToggle(note)) marker.current?.focus({ preventScroll: true })
        }} />
    </div>
  </div>
}

// 한 페이지의 PDF 캔버스와 블럭 경계, 발화 강조, 필기를 표시한다.
function Page({ pdf, number, width, height, rects, blocks, notes, activeNote, onAddNote, onToggleNote, onMoveNote,
  onNoteMenu, onNoteText, noteReadOnly }) {
  const canvas = useRef(null)
  // 화면 밀도에 맞춰 PDF를 그리고 페이지가 사라지거나 크기가 달라지면 이전 작업을 취소한다.
  useEffect(() => {
    let cancelled = false, task = null
    if (!width || !height) return
    void pdf.getPage(number).then(async page => {
      if (cancelled) return
      const ratio = window.devicePixelRatio || 1
      const viewport = page.getViewport({ scale: 1 }), target = canvas.current
      target.width = Math.floor(width * ratio)
      target.height = Math.floor(height * ratio)
      task = page.render({
        canvas: target, viewport,
        transform: [target.width / viewport.width, 0, 0, target.height / viewport.height, 0, 0],
      })
      await task.promise
    }).catch(err => { if (err.name !== 'RenderingCancelledException') console.error(err) })
    return () => { cancelled = true; task?.cancel() }
  }, [pdf, number, width, height])
  const pageNotes = activeNote && !notes.some(note => note.id === activeNote.id) ? [...notes, activeNote] : notes
  return <><canvas ref={canvas} onClick={event => {
    // Shift+클릭 좌표를 PDF 안의 상대 위치로 바꿔 필기를 만든다.
    if (!event.shiftKey) return
    const bounds = event.currentTarget.getBoundingClientRect()
    onAddNote(number, (event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height)
  }} />
    {blocks.map(block => {
      // 블럭에 속한 모든 사각형을 감싸는 경계를 그린다.
      const bounds = block.rects.reduce(([x0,y0,x1,y1],[a,b,c,d]) =>
        [Math.min(x0,a),Math.min(y0,b),Math.max(x1,c),Math.max(y1,d)])
      return <div className="block-boundary" key={block.id} aria-hidden="true" style={boxStyle(bounds)} />
    })}
    {rects.map((rect,index) => <div className="highlight" key={index} aria-hidden="true" style={boxStyle(rect)} />)}
    {pageNotes.map(note => <PdfNote key={note.id} note={activeNote?.id === note.id ? activeNote : note}
      expanded={activeNote?.id === note.id} onToggle={onToggleNote} onMove={onMoveNote} onText={onNoteText}
      onMenu={onNoteMenu} readOnly={noteReadOnly} pageWidth={width} />)}
  </>
}

createRoot(document.getElementById('root')).render(<App />)
