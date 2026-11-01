
import { useEffect, useState } from 'react'

type Block = {
  block_id: string
  page: number
  text: string
  bbox: [number, number, number, number]
}

type SearchResult = {
  status: 'MATCH' | 'NONE'
  page: number | null
  block_id: string | null
  score: number | null
  model: string
}

function App() {
  const [blocks, setBlocks] = useState<Block[]>([])
  const [text, setText] = useState(
    '운영체제는 프로세스를 관리합니다.'
  )
  const [result, setResult] = useState<SearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    async function loadBlocks() {
      try {
        const response = await fetch('/api/mock/blocks')
        if (!response.ok) throw new Error('블록 조회 실패')

        const data: Block[] = await response.json()
        setBlocks(data)
      } catch {
        setError('PDF 블록 데이터를 불러오지 못했습니다.')
      }
    }

    loadBlocks()
  }, [])

  async function handleSearch() {
    if (!text.trim()) return

    setLoading(true)
    setError('')
    setResult(null)

    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          document_id: 'doc_001',
          session_id: 'session_001',
          segment_id: 'seg_001',
          text: text.trim(),
        }),
      })

      if (!response.ok) throw new Error('탐색 요청 실패')

      const data: SearchResult = await response.json()
      setResult(data)
    } catch {
      setError('백엔드 탐색 요청에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="app">
      <header>
        <h1>SpeakSpot</h1>
        <p>Real-time Speech-Based Document Highlighting</p>
      </header>

      <section className="search-panel">
        <h2>발화 텍스트 입력</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void handleSearch()
          }}
        >
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="교수님의 발화 내용을 입력하세요"
          />
          <button type="submit" disabled={loading}>
            {loading ? '탐색 중...' : '탐색'}
          </button>
        </form>

        {error && <p className="error">{error}</p>}

        {result && (
          <p className="result">
            {result.status === 'MATCH'
              ? `매칭 성공: ${result.page}페이지 / ${result.block_id}`
              : '일치하는 블록이 없습니다.'}
          </p>
        )}
      </section>

      <section className="document-panel">
        <h2>PDF 미리보기 (Mock)</h2>
        <div className="pdf-page">
          <span className="page-label">Page 5</span>

          {blocks.map((block) => {
            const [x0, y0, x1, y1] = block.bbox
            const selected =
              result?.status === 'MATCH' &&
              result.block_id === block.block_id

            return (
              <div
                key={block.block_id}
                className={`pdf-block ${selected ? 'highlight' : ''}`}
                style={{
                  left: `${x0 * 100}%`,
                  top: `${y0 * 100}%`,
                  width: `${(x1 - x0) * 100}%`,
                  height: `${(y1 - y0) * 100}%`,
                }}
              >
                {block.text}
              </div>
            )
          })}
        </div>
      </section>
    </main>
  )
}

export default App
