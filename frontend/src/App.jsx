import { useEffect, useRef, useState } from 'react'
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

function uploadPdf(file) {
  const form = new FormData()
  form.append('file', file)
  return request('documents', { method: 'POST', body: form })
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
    const partials = [], indexes = []
    for (let i = 0; i < event.results.length; i++) {
      const result = event.results[i], text = result[0].transcript.trim()
      if (!result.isFinal && !this.finalized.has(i) && text) { partials.push(text); indexes.push(i) }
    }
    const text = partials.join(' '), signature = text ? `${this.id(indexes[0])}:${text}` : ''
    if (signature && signature !== this.last) updates.push({ text, is_final: false, utterance_id: this.id(indexes[0]), recognition_cycle: this.cycle })
    this.last = signature
    return updates
  }
}

const SPEECH_RETRY_DELAYS = [500, 1000, 2000, 4000]
const SPEECH_TIMEOUT = '음성 인식 서비스가 응답하지 않습니다. 마이크와 네트워크를 확인한 뒤 다시 시작해 주세요.'
const SPEECH_ERRORS = {
  'not-allowed': '브라우저에서 이 사이트의 마이크 사용을 허용해 주세요.',
  'service-not-allowed': '브라우저가 음성 인식 서비스 연결을 허용하지 않습니다.',
  'audio-capture': '마이크 입력을 시작하지 못했습니다. 브라우저와 시스템의 입력 장치를 확인해 주세요.',
  'language-not-supported': '이 브라우저에서는 한국어 음성 인식을 사용할 수 없습니다.',
}

function useSpeech(onUpdate, onError) {
  const [status, setStatus] = useState('idle')
  const session = useRef({ recognition: null, wanted: false, timer: null, failures: 0 })
  const tracker = useRef(new Transcript())
  const callbacks = useRef({ onUpdate, onError })
  useEffect(() => { callbacks.current = { onUpdate, onError } }, [onUpdate, onError])
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
    setStatus('idle')
    if (message) callbacks.current.onError(message)
  }

  function restart(message, abort = false) {
    release(abort)
    if (!state.wanted) {
      setStatus('idle')
      return
    }
    if (message && state.failures >= SPEECH_RETRY_DELAYS.length) {
      finish(message)
      return
    }
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
        const updates = tracker.current.read(event)
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
        if (!state.wanted || SPEECH_ERRORS[error]) {
          finish(state.wanted ? SPEECH_ERRORS[error] : undefined)
          return
        }
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
    if (!state.recognition) return finish()
    setStatus('stopping')
    waitFor(4000, finish)
    try { state.recognition.stop() } catch { finish() }
  }

  useEffect(() => () => { state.wanted = false; release(true) }, [])
  return { status, active: status !== 'idle', supported: !!Recognition, start, stop }
}

function App() {
  const [document, setDocument] = useState(null), [pdf, setPdf] = useState(null)
  const [page, setPage] = useState(1), [highlight, setHighlight] = useState(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [caption, setCaption] = useState('')
  const [speechError, setSpeechError] = useState('')
  const [follow, setFollow] = useState(true), [showBlocks, setShowBlocks] = useState(false)
  const [correctViewerPage, setCorrectViewerPage] = useState(false)
  const correctionRef = useRef(false), viewerPageRef = useRef(1)
  const followRef = useRef(true), matchedPageRef = useRef(null)
  const input = useRef(null), currentDocument = useRef(null), currentPdf = useRef(null)
  const work = useRef({ queue: [], sending: false, sequence: 0, generation: 0, revision: 0 })
  async function flush() {
    const state = work.current
    if (state.sending) return
    state.sending = true
    try {
      while (state.queue.length) {
        const update = state.queue.shift(), generation = state.generation
        let doc = currentDocument.current
        if (!doc) continue
        try {
          let result
          try {
            result = await sendUpdate(doc, update)
          } catch (err) {
            if (err.status !== 404 || generation !== state.generation || doc !== currentDocument.current) throw err
            const restored = await uploadPdf(doc.file)
            if (generation !== state.generation || doc !== currentDocument.current) continue
            const anchor = matchedPageRef.current
            if (update.kind !== 'page' && anchor > 1) await sendUpdate(restored, { kind: 'page', page: anchor })
            if (generation !== state.generation || doc !== currentDocument.current) continue
            doc = { ...restored, file: doc.file }
            currentDocument.current = doc
            setDocument(restored)
            setHighlight(null)
            if (update.revision !== state.revision) continue
            result = await sendUpdate(doc, update)
          }
          if (generation !== state.generation || doc !== currentDocument.current || update.revision !== state.revision) continue
          if (update.kind !== 'page') {
            if (result.block_id || result.page != null) {
              setHighlight(doc.blocks?.find(block => block.id === result.block_id) || null)
            }
            if (result.page != null) {
              matchedPageRef.current = result.page
              if (followRef.current) {
                viewerPageRef.current = result.page
                setPage(result.page)
              }
            }
          }
          setError('')
        } catch (err) { if (generation === state.generation && update.revision === state.revision) setError(err.message) }
      }
    } finally { state.sending = false }
  }
  function onSpeech(update) {
    setCaption(update.text)
    if (!currentDocument.current) return
    const state = work.current
    state.queue = state.queue.filter(item => item.kind === 'page' || item.is_final || item.utterance_id !== update.utterance_id)
    state.queue.push({ ...update, sequence: ++state.sequence, revision: state.revision })
    void flush()
  }
  const speech = useSpeech(onSpeech, setSpeechError)
  const speechLabel = {
    idle: '음성 인식', connecting: '연결 중…', listening: '인식 중지', reconnecting: '재연결 중…', stopping: '인식 종료 중…',
  }[speech.status]
  async function open(file) {
    if (!file || busy) return
    setBusy(true); setError('')
    work.current.generation++
    work.current.queue = []
    let loaded = null, loading = null
    try {
      loading = getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
      const local = loading.promise
      local.catch(() => {})
      const doc = await uploadPdf(file)
      loaded = await local
      void currentPdf.current?.destroy()
      currentPdf.current = loaded
      currentDocument.current = { ...doc, file }
      viewerPageRef.current = 1
      matchedPageRef.current = 1
      setPdf(loaded); setDocument(doc); setPage(1); setHighlight(null); setCaption('')
    } catch (err) {
      if (!loaded) void loading?.destroy()
      setError(err.message || 'PDF를 열지 못했습니다.')
    } finally { setBusy(false) }
  }
  useEffect(() => () => { work.current.generation++; void currentPdf.current?.destroy() }, [])
  function changeFollow(enabled) {
    followRef.current = enabled
    setFollow(enabled)
    if (enabled && matchedPageRef.current != null) {
      viewerPageRef.current = matchedPageRef.current
      setPage(matchedPageRef.current)
    }
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
  function changeViewerPage(next) {
    if (next === viewerPageRef.current) return
    viewerPageRef.current = next
    setPage(next)
    if (correctionRef.current) correctPage(next)
  }
  function changeCorrection(enabled) {
    correctionRef.current = enabled
    setCorrectViewerPage(enabled)
    if (enabled) correctPage(viewerPageRef.current)
  }
  return <div className="app">
    <header>
      <div className="brand">SpeakSpot<span>PDF 음성 탐색</span></div>
    </header>
    <section className="speech-bar" aria-label="음성 인식과 자막">
      <button className={speech.status === 'listening' ? 'recording' : 'primary'}
        disabled={busy || !document || !speech.supported || speech.status === 'stopping'} aria-pressed={speech.active}
        onClick={() => { setSpeechError(''); speech.active ? speech.stop() : speech.start() }}>
        <span className="dot" />{speechLabel}
      </button>
      <p aria-live="polite">{busy ? '문서의 텍스트와 검색 모델을 준비하고 있습니다…' : caption || (!speech.supported ? 'Chrome 또는 Edge에서 음성 인식을 사용할 수 있습니다.' : '음성 인식을 시작하면 여기에 발화가 표시됩니다.')}</p>
    </section>
    {(speechError || error) && <div className="error" role="alert">{speechError || error}</div>}
    <main>
      <div className="document-bar">
        <div className="document-file">
          <span className="filename" title={document?.name}>{document?.name || '열린 문서 없음'}</span>
          <button disabled={busy || speech.active} onClick={() => input.current?.click()}>{busy ? '문서 준비 중…' : 'PDF 업로드'}</button>
          <input ref={input} type="file" accept="application/pdf,.pdf" hidden aria-label="PDF 파일 선택"
            onChange={event => { void open(event.target.files?.[0]); event.target.value = '' }} />
        </div>
        <div className="document-tools">
          <label className="viewer-toggle"><input type="checkbox" checked={follow}
            onChange={event => changeFollow(event.target.checked)} />발화 따라가기</label>
          <label className="viewer-toggle" title="켜면 지금 보고 있는 PDF 페이지를 검색 기준으로 사용합니다."><input type="checkbox" checked={correctViewerPage}
            disabled={!document || busy} onChange={event => changeCorrection(event.target.checked)} />뷰어 페이지로 보정</label>
          <label className="viewer-toggle"><input type="checkbox" checked={showBlocks}
            disabled={!document} onChange={event => setShowBlocks(event.target.checked)} />블럭 경계 표시</label>
          <nav aria-label="페이지 이동">
            <button disabled={!document || page === 1} onClick={() => changeViewerPage(page-1)} aria-label="이전 페이지">▲</button>
            <span>{document ? `${page} / ${document.pages}` : '—'}</span>
            <button disabled={!document || page === document.pages} onClick={() => changeViewerPage(page+1)} aria-label="다음 페이지">▼</button>
          </nav>
        </div>
      </div>
      {pdf ? <Viewer pdf={pdf} page={page} onPage={changeViewerPage} highlight={highlight}
        boundaries={showBlocks ? document.blocks : []} />
        : <div className="empty"><div className="paper-icon" aria-hidden />
          <h1>문서를 업로드 해주세요.</h1><p>PDF를 열고 음성 인식을 시작하면<br />말씀하시는 내용으로 문서가 이동합니다.</p>
        </div>}
    </main>
  </div>
}

function Viewer({ pdf, page, onPage, highlight, boundaries = [] }) {
  const container = useRef(null), visible = useRef(page)
  useEffect(() => {
    if (visible.current !== page && container.current) {
      visible.current = page
      container.current.scrollTop = (page-1)*container.current.clientHeight
    }
  }, [page])
  useEffect(() => {
    const element = container.current
    const observer = new ResizeObserver(() => { element.scrollTop = (visible.current-1)*element.clientHeight })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return <div className="pages" ref={container} tabIndex={0} aria-label="PDF 페이지"
    onScroll={event => {
      const element = event.currentTarget
      const next = Math.min(pdf.numPages,Math.max(1,Math.round(element.scrollTop/element.clientHeight)+1))
      if (next !== visible.current) { visible.current = next; onPage(next) }
    }} onKeyDown={event => {
      const delta = ['ArrowDown','PageDown'].includes(event.key) ? 1 : ['ArrowUp','PageUp'].includes(event.key) ? -1 : 0
      if (delta) { event.preventDefault(); onPage(Math.max(1,Math.min(pdf.numPages,page+delta))) }
    }}>
    {Array.from({ length: pdf.numPages },(_,index) => <div className="page" key={index} aria-label={`${index+1}페이지`}>
      {Math.abs(index+1-page) <= 1 ? <Page pdf={pdf} number={index+1}
        rects={highlight?.page === index+1 ? highlight.rects : []}
        blocks={boundaries.filter(block => block.page === index+1)} />
        : <span className="page-placeholder">{index+1}</span>}
    </div>)}
  </div>
}

function boxStyle([x0,y0,x1,y1]) {
  return { left:`${x0*100}%`, top:`${y0*100}%`, width:`${(x1-x0)*100}%`, height:`${(y1-y0)*100}%` }
}

function Page({ pdf, number, rects, blocks }) {
  const canvas = useRef(null), wrapper = useRef(null)
  const [size,setSize] = useState({ width:0,height:0 })
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setSize({ width:entry.contentRect.width,height:entry.contentRect.height }))
    observer.observe(wrapper.current.parentElement)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    let cancelled = false, task = null
    if (!size.width || !size.height) return
    void pdf.getPage(number).then(async page => {
      if (cancelled) return
      const base = page.getViewport({ scale:1 }), ratio = window.devicePixelRatio || 1
      const scale = Math.min(size.width/base.width,size.height/base.height)
      const viewport = page.getViewport({ scale:scale*ratio }), target = canvas.current
      target.width = Math.floor(viewport.width); target.height = Math.floor(viewport.height)
      target.style.width = `${Math.floor(viewport.width/ratio)}px`; target.style.height = `${Math.floor(viewport.height/ratio)}px`
      task = page.render({ canvas:target,viewport })
      await task.promise
    }).catch(err => { if (err.name !== 'RenderingCancelledException') console.error(err) })
    return () => { cancelled = true; task?.cancel() }
  }, [pdf, number, size])
  return <div className="sheet" ref={wrapper}><canvas ref={canvas} />
    {blocks.map(block => {
      const bounds = block.rects.reduce(([x0,y0,x1,y1],[a,b,c,d]) =>
        [Math.min(x0,a),Math.min(y0,b),Math.max(x1,c),Math.max(y1,d)])
      return <div className="block-boundary" key={block.id} aria-hidden="true" style={boxStyle(bounds)} />
    })}
    {rects.map((rect,index) => <div className="highlight" key={index} aria-hidden="true" style={boxStyle(rect)} />)}
  </div>
}

createRoot(document.getElementById('root')).render(<App />)
