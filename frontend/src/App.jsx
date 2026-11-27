import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist'
import worker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import './style.css'

GlobalWorkerOptions.workerSrc = worker

async function request(path, options) {
  const response = await fetch(`/api/${path}`, options)
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw Object.assign(new Error(error.detail || '서버에 연결하지 못했습니다.'), { status: response.status })
  }
  return response.json()
}

const ISLAND_UPLOAD_MIN_MS = 480

function uploadPdf(file) {
  const form = new FormData()
  form.append('file', file)
  return request('documents', { method: 'POST', body: form })
}

async function readPdf(pdf) {
  const firstPage = await pdf.getPage(1)
  const { width, height } = firstPage.getViewport({ scale: 1 })
  return { document: pdf, firstPageSize: { width, height } }
}

function sendUpdate(doc, update) {
  const correction = update.kind === 'page'
  return request(`documents/${doc.id}/${correction ? 'page' : 'speech'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(correction ? { page: update.page } : update),
  })
}

// Replace interim hypotheses and commit each final result once, across restarts.
class Transcript {
  sequence = 0
  cycle = 0
  ids = new Map()
  finalized = new Set()
  last = ''
  reset() { this.cycle++; this.ids.clear(); this.finalized.clear(); this.last = '' }
  id(index) {
    if (!this.ids.has(index)) this.ids.set(index, `speech_${++this.sequence}`)
    return this.ids.get(index)
  }
  read(event) {
    const updates = []
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i], text = result[0].transcript.trim()
      if (!text || !result.isFinal || this.finalized.has(i)) continue
      this.finalized.add(i)
      updates.push({ text, is_final: true, utterance_id: this.id(i), recognition_cycle: this.cycle })
    }
    const partials = []
    let firstPartial
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i], text = result[0].transcript.trim()
      if (!result.isFinal && !this.finalized.has(i) && text) { firstPartial ??= i; partials.push(text) }
    }
    const text = partials.join(' '), id = text ? this.id(firstPartial) : null
    const signature = id ? `${id}:${text}` : ''
    if (signature && signature !== this.last) updates.push({ text, is_final: false, utterance_id: id, recognition_cycle: this.cycle })
    this.last = signature
    return { updates, interim: text }
  }
}

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

function useSpeech(onUpdate, onCaption, onError) {
  const [status, setStatus] = useState('idle')
  const session = useRef({ recognition: null, wanted: false, timer: null, failures: 0 })
  const tracker = useRef(new Transcript())
  const callbacks = useRef({ onUpdate, onCaption, onError })
  useEffect(() => { callbacks.current = { onUpdate, onCaption, onError } }, [onUpdate, onCaption, onError])
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
  const state = session.current

  function waitFor(delay, callback) {
    clearTimeout(state.timer)
    state.timer = setTimeout(callback, delay)
  }

  function release(abort = false) {
    clearTimeout(state.timer)
    state.timer = null
    const recognition = state.recognition
    state.recognition = null
    if (abort) recognition?.abort()
  }

  function finish(message) {
    state.wanted = false
    release(true)
    callbacks.current.onCaption('')
    setStatus('idle')
    if (message) callbacks.current.onError(message)
  }

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
      const current = () => state.recognition === recognition
      function connected() {
        if (!current() || !state.wanted || readyAt !== null) return
        readyAt = performance.now()
        if (!audioEnded) clearTimeout(state.timer)
        setStatus('listening')
      }
      recognition.onstart = connected
      recognition.onresult = event => {
        if (!current()) return
        connected()
        const { updates, interim } = tracker.current.read(event)
        if (state.wanted) callbacks.current.onCaption(unfinishedSentence(interim))
        if (updates.length) {
          receivedSpeech = true
          state.failures = 0
        }
        for (const update of updates) callbacks.current.onUpdate(update)
      }
      recognition.onaudioend = () => {
        if (!current() || !state.wanted) return
        audioEnded = true
        waitFor(4000, () => restart(SPEECH_TIMEOUT, true))
      }
      recognition.onerror = ({ error }) => {
        if (!current()) return
        if (!state.wanted || SPEECH_ERRORS[error]) return finish(state.wanted ? SPEECH_ERRORS[error] : undefined)
        const message = error === 'network' ? '음성 인식 서비스와 연결이 끊겼습니다. 네트워크를 확인한 뒤 다시 시작해 주세요.' : SPEECH_TIMEOUT
        restart(error === 'no-speech' ? null : message, true)
      }
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

  function start() {
    if (!Recognition || state.wanted || state.recognition) return
    state.wanted = true
    state.failures = 0
    setStatus('connecting')
    connect()
  }

  function stop() {
    state.wanted = false
    callbacks.current.onCaption('')
    if (!state.recognition) return finish()
    setStatus('stopping')
    waitFor(4000, finish)
    try { state.recognition.stop() } catch { finish() }
  }

  useEffect(() => () => { state.wanted = false; release(true) }, [])
  return { status, active: status !== 'idle', supported: !!Recognition, start, stop }
}

const ISLAND_ICONS = {
  upload: <path d="M12 15V4m-4 4 4-4 4 4M5 14v5a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5" />,
  mic: <>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21m-3 0h6" />
  </>,
  stop: <rect x="5.5" y="5.5" width="13" height="13" rx="2.5" fill="currentColor" stroke="none" />,
}

const ISLAND_TEXT = {
  upload: { icon: 'upload', action: 'PDF 업로드' },
  uploading: { icon: 'upload', action: 'PDF 준비 중' },
  idle: { icon: 'mic', action: '음성 인식 시작' },
  connecting: { icon: 'stop', action: '음성 인식 중지', status: '마이크 연결 중' },
  listening: { icon: 'stop', action: '음성 인식 중지' },
  reconnecting: { icon: 'stop', action: '음성 인식 중지', status: '마이크 재연결 중' },
  stopping: { icon: 'stop', action: '음성 인식 종료 중', status: '마이크 종료 중' },
}

function LectureIsland({ hasDocument, uploading, speech, caption, onUpload, onToggleSpeech }) {
  const mode = uploading ? 'uploading' : hasDocument ? speech.status : 'upload'
  const { icon, action, status } = ISLAND_TEXT[mode]
  const expanded = Boolean(uploading || speech.active)
  const unavailable = mode === 'idle' && !speech.supported
  const disabled = Boolean(uploading || mode === 'stopping' || unavailable)
  const label = unavailable ? '이 브라우저에서는 음성 인식을 사용할 수 없습니다.' : action
  return <div className="island" data-mode={mode} data-expanded={expanded}>
    <button className="island-action" type="button" aria-label={label} title={label} aria-disabled={disabled}
      onClick={disabled ? undefined : mode === 'upload' ? onUpload : onToggleSpeech}>
      <svg className="island-icon" key={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ISLAND_ICONS[icon]}</svg>
    </button>
    <div className="island-info" role="status" aria-label={speech.active ? '실시간 자막' : undefined}>
      {uploading && <>
        <span className="island-title" title={uploading}>{uploading}</span>
        <span className="island-status">{action}</span>
      </>}
      {speech.active && <div className="island-caption">
        {caption ? <p>{caption}</p> : status && <span className="island-status">{status}</span>}
      </div>}
    </div>
    {expanded && <span className="island-indicator" aria-hidden="true">
      {mode === 'listening' && <><i /><i /><i /></>}
    </span>}
  </div>
}

function App() {
  const [document, setDocument] = useState(null), [pdf, setPdf] = useState(null)
  const [pageView, setPageView] = useState({ page: 1, navigate: false })
  const { page } = pageView
  const [highlight, setHighlight] = useState(null)
  const [uploading, setUploading] = useState(''), [error, setError] = useState(''), [caption, setCaption] = useState('')
  const [speechError, setSpeechError] = useState('')
  const [follow, setFollow] = useState(true), [showBlocks, setShowBlocks] = useState(false)
  const [correctViewerPage, setCorrectViewerPage] = useState(false)
  const correctionRef = useRef(false), viewerPageRef = useRef(1)
  const followRef = useRef(true), matchedPageRef = useRef(null)
  const input = useRef(null), settings = useRef(null)
  const currentDocument = useRef(null), currentPdf = useRef(null)
  const work = useRef({ queue: [], sending: false, sequence: 0, generation: 0, revision: 0 })
  function showPage(next, navigate = true) {
    viewerPageRef.current = next
    setPageView({ page: next, navigate })
  }
  async function flush() {
    const state = work.current
    if (state.sending) return
    state.sending = true
    try {
      while (state.queue.length) {
        const update = state.queue.shift(), generation = state.generation
        let doc = currentDocument.current
        if (!doc) continue
        const isCurrent = () => generation === state.generation && doc === currentDocument.current
        try {
          let result
          try {
            result = await sendUpdate(doc, update)
          } catch (err) {
            if (err.status !== 404 || !isCurrent()) throw err
            const restored = await uploadPdf(doc.file)
            if (!isCurrent()) continue
            const anchor = matchedPageRef.current
            if (update.kind !== 'page' && anchor > 1) await sendUpdate(restored, { kind: 'page', page: anchor })
            if (!isCurrent()) continue
            doc = { ...restored, name: restored.name || doc.name, file: doc.file }
            currentDocument.current = doc
            setDocument(doc)
            setHighlight(null)
            if (update.revision !== state.revision) continue
            result = await sendUpdate(doc, update)
          }
          if (!isCurrent() || update.revision !== state.revision) continue
          if (update.kind !== 'page') {
            const block = result.block_id ? doc.blocks?.find(block => block.id === result.block_id) : null
            if (result.block_id || result.page != null) setHighlight(block || null)
            const matchedPage = result.page ?? block?.page
            if (Number.isInteger(matchedPage) && matchedPage >= 1 && matchedPage <= currentPdf.current.numPages) {
              matchedPageRef.current = matchedPage
              if (followRef.current && (result.page != null || matchedPage !== viewerPageRef.current)) {
                showPage(matchedPage)
              }
            }
          }
          setError('')
        } catch (err) { if (generation === state.generation && update.revision === state.revision) setError(err.message) }
      }
    } finally { state.sending = false }
  }
  function onSpeech(update) {
    if (!currentDocument.current) return
    const state = work.current
    state.queue = state.queue.filter(item => item.kind === 'page' || item.is_final || item.utterance_id !== update.utterance_id)
    state.queue.push({ ...update, sequence: ++state.sequence, revision: state.revision })
    void flush()
  }
  const speech = useSpeech(onSpeech, setCaption, setSpeechError)
  function selectPdf() {
    input.current?.click()
  }
  function toggleSpeech() {
    setSpeechError('')
    if (speech.active) speech.stop()
    else speech.start()
  }
  async function open(file) {
    if (!file || uploading || speech.active) return
    if (settings.current) settings.current.open = false
    setUploading(file.name); setError(''); setSpeechError('')
    const showUpload = new Promise(resolve => setTimeout(resolve, ISLAND_UPLOAD_MIN_MS))
    work.current.generation++
    work.current.queue = []
    let loaded = null, loading = null
    try {
      loading = getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
      const [result, local] = await Promise.all([uploadPdf(file), loading.promise.then(readPdf), showUpload])
      const doc = { ...result, name: result.name || file.name, file }
      loaded = local.document
      void currentPdf.current?.destroy()
      currentPdf.current = loaded
      currentDocument.current = doc
      matchedPageRef.current = 1
      setPdf(local); setDocument(doc); showPage(1); setHighlight(null); setCaption('')
    } catch (err) {
      if (!loaded) void loading?.destroy()
      setError(err.message || 'PDF를 열지 못했습니다.')
    } finally { setUploading('') }
  }
  useEffect(() => () => { work.current.generation++; void currentPdf.current?.destroy() }, [])
  function changeFollow(enabled) {
    followRef.current = enabled
    setFollow(enabled)
    if (enabled && matchedPageRef.current != null) showPage(matchedPageRef.current)
  }
  function correctPage(next) {
    if (!currentDocument.current) return
    const state = work.current
    const revision = ++state.revision
    // Serialize corrections with speech; ignore results started before this correction.
    if (state.queue.at(-1)?.kind === 'page') state.queue.pop()
    state.queue.push({ kind: 'page', page: next, revision })
    matchedPageRef.current = next
    setHighlight(null)
    void flush()
  }
  function changeViewerPage(next, navigate = true) {
    if (next === viewerPageRef.current) return
    showPage(next, navigate)
    if (correctionRef.current) correctPage(next)
  }
  function changeCorrection(enabled) {
    correctionRef.current = enabled
    setCorrectViewerPage(enabled)
    if (enabled) correctPage(viewerPageRef.current)
  }
  return <div className="app" data-document={Boolean(pdf)}>
    <input ref={input} type="file" accept="application/pdf,.pdf" hidden aria-label="PDF 파일 선택"
      onChange={event => { void open(event.target.files?.[0]); event.target.value = '' }} />
    <LectureIsland hasDocument={Boolean(pdf)} uploading={uploading} speech={speech} caption={caption}
      onUpload={selectPdf} onToggleSpeech={toggleSpeech} />
    {(speechError || error) && <div className="error" role="alert">{speechError || error}</div>}

    {!pdf && !uploading && <main className="welcome">
      <h1>SpeakSpot</h1>
      <p>강의 PDF를 올리고 설명하면 관련 페이지를 자동으로 찾아줍니다.</p>
    </main>}

    {pdf && <>
      <main className="document">
        <Viewer key={pdf.document.fingerprints[0]} pdf={pdf.document} firstPageSize={pdf.firstPageSize} pageView={pageView}
          onPage={changeViewerPage} highlight={highlight} boundaries={showBlocks ? document.blocks || [] : []} />
      </main>
      <nav className="page-navigation" aria-label="페이지 이동">
        <button type="button" disabled={page === 1} onClick={() => changeViewerPage(page - 1)} aria-label="이전 페이지">
          <span className="chevron" aria-hidden="true" />
        </button>
        <span>{page} / {pdf.document.numPages}</span>
        <button type="button" disabled={page === pdf.document.numPages} onClick={() => changeViewerPage(page + 1)} aria-label="다음 페이지">
          <span className="chevron" aria-hidden="true" />
        </button>
      </nav>
      <details className="viewer-settings" ref={settings}>
        <summary aria-label="뷰어 설정" title="뷰어 설정"><span aria-hidden="true">⋯</span></summary>
        <div className="settings-panel">
          <label><input type="checkbox" checked={follow}
            onChange={event => changeFollow(event.target.checked)} />발화 따라가기</label>
          <label title="켜면 지금 보고 있는 PDF 페이지를 검색 기준으로 사용합니다.">
            <input type="checkbox" checked={correctViewerPage} disabled={Boolean(uploading)}
              onChange={event => changeCorrection(event.target.checked)} />뷰어 페이지로 보정</label>
          <label><input type="checkbox" checked={showBlocks}
            onChange={event => setShowBlocks(event.target.checked)} />블럭 경계 표시</label>
          <button type="button" disabled={Boolean(uploading) || speech.active} onClick={selectPdf}>다른 PDF 열기</button>
        </div>
      </details>
    </>}
  </div>
}

function pageBottom(element) {
  return element.offsetTop + element.offsetHeight
}

function pageIndexAt(pages, offset) {
  let first = 0, last = pages.length - 1
  while (first < last) {
    const middle = Math.floor((first + last) / 2)
    if (pageBottom(pages[middle]) < offset) first = middle + 1
    else last = middle
  }
  return first
}

function Viewer({ pdf, firstPageSize, pageView, onPage, highlight, boundaries = [] }) {
  const { page, navigate } = pageView
  const container = useRef(null), visible = useRef(page)
  const previousSize = useRef(null)
  const [pageSize, setPageSize] = useState({ width: 0, height: 0 })
  const [renderRange, setRenderRange] = useState({ first: 0, last: 1 })
  const scale = Math.min(pageSize.width / firstPageSize.width, pageSize.height / firstPageSize.height)
  const width = Math.floor(firstPageSize.width * scale)
  const height = Math.floor(firstPageSize.height * scale)

  function renderVisiblePages(element) {
    const first = Math.max(0, pageIndexAt(element.children, element.scrollTop) - 1)
    const last = Math.min(pdf.numPages - 1, pageIndexAt(element.children, element.scrollTop + element.clientHeight) + 1)
    setRenderRange(range => range.first === first && range.last === last ? range : { first, last })
  }

  useEffect(() => {
    const element = container.current
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setPageSize({ width, height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useLayoutEffect(() => {
    if (!pageSize.width || !pageSize.height) return
    const element = container.current
    if (navigate || previousSize.current !== pageSize) {
      visible.current = page
      previousSize.current = pageSize
      element.scrollTop = pageBottom(element.children[page - 1]) - element.clientHeight
    }
    renderVisiblePages(element)
  }, [pageView, pageSize])

  return <div className="pages" ref={container} tabIndex={0} aria-label="PDF 페이지"
    onScroll={event => {
      const element = event.currentTarget
      const bottom = element.scrollTop + element.clientHeight
      const index = pageIndexAt(element.children, bottom)
      const previous = element.children[index - 1]
      const closest = previous && bottom - pageBottom(previous) < pageBottom(element.children[index]) - bottom
        ? index - 1 : index
      const next = closest + 1
      renderVisiblePages(element)
      if (next !== visible.current) { visible.current = next; onPage(next, false) }
    }} onKeyDown={event => {
      const delta = ['ArrowDown','PageDown'].includes(event.key) ? 1 : ['ArrowUp','PageUp'].includes(event.key) ? -1 : 0
      if (delta) { event.preventDefault(); onPage(Math.max(1,Math.min(pdf.numPages,page+delta))) }
    }}>
    {Array.from({ length: pdf.numPages }, (_, index) => <div className="page" key={index} aria-label={`${index+1}페이지`}>
      <div className="sheet" style={{ width, height }}>
        {index >= renderRange.first && index <= renderRange.last
          ? <Page pdf={pdf} number={index + 1} width={width} height={height}
            rects={highlight?.page === index + 1 ? highlight.rects : []}
            blocks={boundaries.filter(block => block.page === index + 1)} />
          : <span className="page-placeholder">{index + 1}</span>}
      </div>
    </div>)}
  </div>
}

function boxStyle([x0,y0,x1,y1]) {
  return { left:`${x0*100}%`, top:`${y0*100}%`, width:`${(x1-x0)*100}%`, height:`${(y1-y0)*100}%` }
}

function Page({ pdf, number, width, height, rects, blocks }) {
  const canvas = useRef(null)
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
  return <><canvas ref={canvas} />
    {blocks.map(block => {
      const bounds = block.rects.reduce(([x0,y0,x1,y1],[a,b,c,d]) =>
        [Math.min(x0,a),Math.min(y0,b),Math.max(x1,c),Math.max(y1,d)])
      return <div className="block-boundary" key={block.id} aria-hidden="true" style={boxStyle(bounds)} />
    })}
    {rects.map((rect,index) => <div className="highlight" key={index} aria-hidden="true" style={boxStyle(rect)} />)}
  </>
}

createRoot(document.getElementById('root')).render(<App />)
